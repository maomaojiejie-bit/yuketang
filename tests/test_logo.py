"""立体 YKT 标题测试。"""

from __future__ import annotations

from cjsolver.logo import (
    ASCII_FACE,
    ASCII_SIDE,
    FACE,
    FONT,
    GLYPH_HEIGHT,
    GLYPH_WIDTH,
    LOGO,
    LOGO_ASCII,
    LOGO_ASCII_LINES,
    LOGO_LINES,
    SIDE,
    fits,
    rasterize,
    render,
)


def test_logo_has_expected_shape() -> None:
    # 6 行字模 + 1 行挤出
    assert len(LOGO_LINES) == GLYPH_HEIGHT + 1
    assert LOGO.strip()
    assert "\t" not in LOGO
    # 每行都得有笔画，否则说明挤出把字吃掉了
    for line in LOGO_LINES:
        assert line.strip(), f"空行会让标题看起来缺一块：{line!r}"


def test_rasterize_dimensions() -> None:
    grid = rasterize("YKT", spacing=2)
    assert len(grid) == GLYPH_HEIGHT
    # 3 个字 * 7 宽 + 2 个间隔 * 2
    assert len(grid[0]) == GLYPH_WIDTH * 3 + 2 * 2


def test_rasterize_rejects_unknown_glyph() -> None:
    try:
        rasterize("YKTZ")
    except KeyError as exc:
        assert "Z" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("未知字符应当报错")


def test_face_and_side_are_used() -> None:
    lines = render("YKT", depth=1, spacing=2)
    joined = "\n".join(lines)
    assert FACE in joined
    assert SIDE in joined
    # 正面必须存在，且挤出层不该盖掉它
    assert joined.count(FACE) > joined.count(SIDE)


def test_ascii_variant_is_pure_ascii() -> None:
    assert LOGO_ASCII.isascii(), "降级版必须能塞进任何编码"
    for line in LOGO_ASCII_LINES:
        assert ASCII_FACE in line or ASCII_SIDE in line or not line.strip()
    # 两个版本的行数必须一致，否则终端里会抖
    assert len(LOGO_ASCII_LINES) == len(LOGO_LINES)


def test_render_is_deterministic() -> None:
    assert LOGO_LINES == tuple(render("YKT"))
    assert LOGO == "\n".join(LOGO_LINES)


def test_depth_two_adds_more_rows() -> None:
    shallow = render("YKT", depth=1, spacing=2)
    deep = render("YKT", depth=2, spacing=2)
    assert len(deep) > len(shallow)


def test_lines_are_right_trimmed() -> None:
    for line in LOGO_LINES:
        assert line == line.rstrip(), "行尾空白会让终端输出出现多余空隙"


def test_fits_detects_encoding_support() -> None:
    assert fits("utf-8") is True
    assert fits("utf-8") and fits("UTF-8")
    # GBK 里没有 U+2588 这类方块字符
    assert fits("gbk") is False
    assert fits("ascii") is False
    assert fits(None) is False
    assert fits("no-such-codec") is False


def test_every_glyph_matches_declared_size() -> None:
    for char, glyph in FONT.items():
        assert len(glyph) == GLYPH_HEIGHT, char
        for row in glyph:
            assert len(row) == GLYPH_WIDTH, (char, row)
            assert set(row) <= {"1", "."}, (char, row)
