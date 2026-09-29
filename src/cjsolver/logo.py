"""把 "YKT" 渲染成立体（挤出+倒角）ASCII 艺术字。

用生成器而不是硬写死一段字符串，是为了：
  - 终端（rich）和 `一键启动.bat` 能用同一个定义，不会各写一份写歪
  - 有测试兜底（行宽一致、只含预期字符、可重复）
"""

from __future__ import annotations

#: 7 宽 6 高的粗体点阵字模，`1` 表示笔画。
#: 用粗体而不是单像素骨架：挤出层太宽时，细笔画会被挤出的阴影糊成一团。
FONT: dict[str, list[str]] = {
    "Y": [
        "11...11",
        "11...11",
        "11...11",
        ".11.11.",
        "..111..",
        "...1...",
    ],
    "K": [
        "11...11",
        "11..11.",
        "11.11..",
        "1111...",
        "11.11..",
        "11..11.",
    ],
    "T": [
        "1111111",
        "...1...",
        "...1...",
        "...1...",
        "...1...",
        "...1...",
    ],
}

FACE = "█"      # 正面
SIDE = "▓"      # 挤出的侧面
SHADOW = "░"    # 最外层的投影

#: 每个字模的宽高
GLYPH_WIDTH = 7
GLYPH_HEIGHT = 6


def rasterize(text: str, *, spacing: int = 1) -> list[list[bool]]:
    """把文字排成一个二维布尔网格。spacing 是字与字之间的空白字符数。"""
    if spacing < 0:
        raise ValueError("spacing 不能为负")
    columns: list[list[str]] = []
    for index, char in enumerate(text.upper()):
        glyph = FONT.get(char)
        if glyph is None:
            raise KeyError(f"字模里没有字符 {char!r}")
        if len(glyph) != GLYPH_HEIGHT or any(len(row) != GLYPH_WIDTH for row in glyph):
            raise ValueError(f"字模 {char!r} 的尺寸不是 {GLYPH_WIDTH}x{GLYPH_HEIGHT}")
        if index and spacing:
            columns.append([" " * spacing] * GLYPH_HEIGHT)
        columns.append(glyph)

    grid: list[list[bool]] = []
    for row in range(GLYPH_HEIGHT):
        line: list[bool] = []
        for column in columns:
            line.extend(cell == "1" for cell in column[row])
        grid.append(line)
    return grid


def render(
    text: str = "YKT",
    *,
    depth: int = 1,
    spacing: int = 3,
    face: str = FACE,
    side: str = SIDE,
    shadow: str = SHADOW,
) -> list[str]:
    """渲染成立体字，返回若干行（右侧与底部空白已裁掉）。

    先按偏移量由远及近铺出投影与侧面，再用正面盖上去；
    因为正面最后画，其它字的挤出层不会盖掉本字的笔画。
    """
    grid = rasterize(text, spacing=spacing)
    height = len(grid)
    width = len(grid[0]) if grid else 0

    canvas = [[" "] * (width + depth + 1) for _ in range(height + depth + 1)]

    def paint(offset: int, char: str) -> None:
        for y in range(height):
            for x in range(width):
                if grid[y][x]:
                    canvas[y + offset][x + offset] = char

    if depth >= 2:
        paint(depth + 1, shadow)
        for step in range(depth, 0, -1):
            paint(step, side)
    elif depth == 1:
        paint(1, side)
    paint(0, face)

    lines = ["".join(row).rstrip() for row in canvas]
    # 挤出层用不到画布预留的最后一行时，把它去掉，免得标题底部多一条空行
    while lines and not lines[-1]:
        lines.pop()
    return lines


#: 供终端与批处理共用的成品
LOGO_LINES: tuple[str, ...] = tuple(render("YKT"))
LOGO: str = "\n".join(LOGO_LINES)

#: 控制台编码撑不住方块字符时（例如 GBK 的 conhost、或输出被管道接走）的降级版本
ASCII_FACE = "#"
ASCII_SIDE = "+"
LOGO_ASCII_LINES: tuple[str, ...] = tuple(
    render("YKT", face=ASCII_FACE, side=ASCII_SIDE)
)
LOGO_ASCII: str = "\n".join(LOGO_ASCII_LINES)

#: 立体字要用到的字符，用于探测输出编码是否支持
UNICODE_BLOCKS = FACE + SIDE + SHADOW


def fits(encoding: str | None) -> bool:
    """判断给定编码能否输出方块字符。"""
    if not encoding:
        return False
    try:
        UNICODE_BLOCKS.encode(encoding)
        return True
    except (UnicodeEncodeError, LookupError):
        return False


if __name__ == "__main__":  # pragma: no cover - 手工预览
    print(LOGO)
    print()
    for index, line in enumerate(LOGO_LINES):
        print(f"{index}: len={len(line)}")
