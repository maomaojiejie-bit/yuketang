# 长江雨课堂自动解题器（cjsolver）

用 Python 接管本机 Chrome，抓取长江雨课堂的随堂习题，交给 DeepSeek / 千问 / 豆包等
大模型作答。支持只提示、自动点击、直接调接口三种模式，带本地网页控制台和模拟检测。

> ⚠️ 仅供个人学习辅助与技术研究。请遵守学校规定与雨课堂服务条款，不要用它替代自己的诚信作答。

## 它是怎么工作的

```
Chrome(调试端口) ──CDP──> Playwright ──┬── 接口响应 + wsapp 推送  → 抓到结构化题目
                                        └── DOM 兜底（页面里按「A.」约定扫题）
                                                 │
                                    DeepSeek / 千问 / 豆包 …（OpenAI 兼容）
                                                 │
                            manual 只提示 ｜ dom 点击作答 ｜ api 调接口
```

题目优先从接口拿（`presentation/fetch` 响应与 `wsapp` WebSocket 帧），因为那里有
`problemType`、`options`、`blanks` 等结构化字段；DOM 扫描只作兜底。

| 用途 | 接口 |
| --- | --- |
| 进行中的课堂 | `GET /api/v3/classroom/on-lesson` |
| 课件（含随堂题） | `GET /api/v3/lesson/presentation/fetch?presentation_id=…` |
| 提交 / 重答 | `POST /api/v3/lesson/problem/answer`、`/retry` |
| 课堂事件推送 | `wss://<host>/wsapp/` |

题型编码：`1` 单选、`2` 多选、`3` 投票、`4` 填空、`5` 主观。
答案格式：单选 `["A"]`、多选 `["A","C"]`、填空 `["空1","空2"]`、主观 `{"content":"…","pics":[]}`。

## 安装

需要 **Python 3.10+** 和本机已装的 **Chrome**（不下载 Playwright 自带浏览器，
直接接管你的 Chrome，登录状态可复用）。

**双击 `一键启动.bat`** 即可：自动检查 Python、缺依赖就装，然后进入菜单。
启动时会打印一个立体 **YKT** 标题（终端编码不支持方块字符时自动降级成 ASCII）。

手动安装：

```powershell
python -m pip install -r requirements.txt
```

不需要 `playwright install`——本项目只用 CDP 接管现有 Chrome。

## 目录结构

```
一键启动.bat / 打开控制台.bat   # 双击即用
run.py                          # 免安装启动脚本（等价于 cjsolver 命令）
config.example.yaml / .env.example
src/cjsolver/
├── cli.py  launcher.py         # 命令行入口 / 交互式菜单与初始配置向导
├── simulate.py  providers.py   # 模拟检测 / 模型服务商预设
├── solver.py  submit.py        # 编排：抓题 → 问模型 → 作答 → 记录 / 三种作答方式
├── logo.py  console.py         # 立体 YKT 标题 / 终端输出
├── ai/       deepseek.py  prompts.py       # 模型客户端 / 提示词与答案归一化
├── browser/  launcher.py  driver.py        # 调试 Chrome 启动 / CDP 接管
├── web/      server.py  runtime.py  bus.py  settings.py  static/index.html
└── yuketang/ capture.py  parsing.py  dom.py  constants.py
tests/                          # 195 个离线单元测试
tools/                          # 三个冒烟测试
```

## 快速开始

```powershell
Copy-Item .env.example .env      # 填入你要用的那家 API Key
python run.py --check            # 自检：配置 / 模型 / 调试端口
python run.py                    # 首次会开 Chrome，扫码登录一次即可
python run.py --web              # 或：打开网页控制台 http://127.0.0.1:8765/
```

登录状态存在专用配置目录 `.chrome-profile/`，不影响你日常的 Chrome。

## 作答模式

在 `config.yaml` 或控制台设置里改，也可用 `--mode` 临时覆盖：

| 模式 | 行为 |
| --- | --- |
| `manual`（默认） | 只打印答案、解析、置信度，**不碰页面** |
| `dom` | 在页面上按选项文字点击，再点「提交」 |
| `api` | 页面内 fetch 直接 POST 作答接口（实验性） |

想先看效果又不提交，加 `--dry-run`。`dom` 模式找不到选项元素会跳过并告警，不会乱点。

## 模型服务

都是 OpenAI 兼容端点，换一家只动 `provider` / `base_url` / `model` 三项。
在控制台「⚙️ 设置 → 模型服务」里直接切。

| 服务商 | Base URL | 环境变量 |
| --- | --- | --- |
| DeepSeek（默认） | `https://api.deepseek.com` | `DEEPSEEK_API_KEY` |
| 通义千问（阿里云百炼） | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `DASHSCOPE_API_KEY` |
| 豆包（火山方舟） | `https://ark.cn-beijing.volces.com/api/v3` | `ARK_API_KEY` |
| 腾讯混元（元宝同源） | `https://api.hunyuan.cloud.tencent.com/v1` | `HUNYUAN_API_KEY` |
| 智谱 GLM | `https://open.bigmodel.cn/api/paas/v4` | `ZHIPU_API_KEY` |
| Kimi（月之暗面） | `https://api.moonshot.cn/v1` | `MOONSHOT_API_KEY` |
| 硅基流动 | `https://api.siliconflow.cn/v1` | `SILICONFLOW_API_KEY` |
| 自定义 | 自己填 | `AI_API_KEY` |

**每家一个 Key 变量**：切到千问却还带 DeepSeek 的 Key 只会换来个 401，
所以程序按当前服务商取对应的变量，缺哪个会明确告诉你。只想用一个变量就用
`AI_API_KEY`（所有服务商都认）。Base URL 一律可手动改——这些地址会变。

> 元宝 App 没有公开 API，表里是同底座的**腾讯混元**接口。

题干画在课件图片里时（接口只给 `image_url`），打开 `vision_enabled` 即可：
`deepseek-flash` 本身就支持看图，`vision_model` 留空就用当前模型；
换别家时再填对应视觉模型（如 `qwen-vl-max`）。

## 模拟检测：能不能收到题目

**不用真在上课，也不用等老师发题**就能验证整条链路：

```powershell
python run.py --simulate offline    # 不碰浏览器
python run.py --simulate browser    # 需要 Chrome
```

控制台左栏有三个按钮，深度递增：

| | 验证什么 |
| --- | --- |
| ① 离线自检 | 把仿真载荷喂给**真实的解析链路**：题型映射、字段提取、去重、提交格式，并真实调一次模型 |
| ② 浏览器自检 | 往当前页面注入模拟答题卡，验证 DOM 抓题、选项定位与点击、提交按钮识别 |
| ③ 端到端投喂 | 把仿真题目喂给正在运行的监听会话，走完抓题 → 问模型 → 作答 → 落盘 |

仿真载荷刻意混用了雨课堂真实出现过的字段写法（选项既有 `{content:…}` 也有纯字符串，
题干既有 `prompt` 也有 `content`，id 既有 `problemId` 也有 `problem_id`，WS 帧用全小写
`lessonid`）。上游一改字段，自检会先于真实课堂报出来。

## 网页控制台

`python run.py --web`（或双击 `打开控制台.bat`）→ `http://127.0.0.1:8765/`。
单页应用，后端 aiohttp，与 Playwright 共用同一个 asyncio 事件循环。

- **实时事件流**：每题一张卡片，答案/解析/置信度/耗时紧随其后
- **带边框日志**：左侧色条按级别着色，带时间戳
- **自动滚动**：默认贴底；滚轮上滑会暂停并浮出「↓ 回到底部」。只看用户手势，
  不用 `scroll` 事件推断（程序自己滚动也会触发它，必然误判）
- **心跳日志**：监听期间定时输出状态（时长、题目计数、接口/WS 流量、当前页面），
  终端与网页同时可见；间隔 `solver.heartbeat_interval`（默认 30 秒，0 关闭）
- **设置面板**：模式、置信度、DOM 轮询、自动进课堂、窗口尺寸、心跳间隔、模型服务、
  自定义背景

主要接口：`/api/state`、`/api/events`（SSE）、`/api/watch/start|stop`、
`/api/simulate`、`/api/ask`、`/api/settings`、`/api/providers`、`/api/dump`、`/api/probe`。
监听 `127.0.0.1`，不对外开放。

## 命令行

```powershell
python run.py                    # 启动监听（默认 manual）
python run.py --menu             # 交互式一键启动菜单
python run.py --web              # 网页控制台
python run.py --check            # 自检
python run.py --simulate offline # 离线抓题自检
python run.py --dump             # 导出页面结构，用于校准选择器
python run.py --ask "单选 1+1=?
A. 1
B. 2"                            # 不开浏览器，直接问题目
python run.py --mode dom --yes --duration 1800   # 自动作答，跑 30 分钟
```

| 参数 | 说明 |
| --- | --- |
| `--site` | `changjiang`（默认）/ `standard` / `pro` |
| `--mode` | `manual` / `dom` / `api` |
| `--dry-run` | 只推理，不下发任何页面动作 |
| `--duration 秒` | 到点自动退出 |
| `--yes` | 自动作答时跳过二次确认 |
| `--host` / `--port` / `--no-open` | 控制台地址、端口、不自动开浏览器 |
| `-v` | 调试日志 |

## 配置

`config.yaml`（完整注释见 `config.example.yaml`）。优先级：
**控制台设置 > 命令行 > 环境变量(.env) > config.yaml > 内置默认值**。
注意控制台里存过的值会覆盖 config.yaml——改了 yaml 没生效时先看这里。

常改的几项：

```yaml
site: changjiang
deepseek:
  provider: deepseek             # 换服务商只动这三项
  model: deepseek-flash
  base_url: https://api.deepseek.com
answer:
  mode: manual                   # manual / dom / api
  min_confidence: 0.0            # 低于该置信度只提示，不自动作答
  auto_submit: true
solver:
  heartbeat_interval: 30         # 0 关闭
  auto_enter_lesson: false       # 检测到正在上课时自动跳转
browser:
  window_width: 800              # 启动监听时缩窗口，0 = 不调整
  window_height: 600
```

**DOM 选择器失效**时（雨课堂改版）：`python run.py --dump` 导出页面结构和扫描结果，
把选项/题干容器的 class 填进 `answer.option_selectors` / `question_selectors`。

每次运行在 `records/session-<时间戳>.jsonl` 逐题记录：题目、答案、解析、置信度、耗时、提交结果。

## 测试

```powershell
python -m pytest -q                # 195 个离线单元测试
python tools/smoke_browser.py      # 浏览器层：注入仿真答题卡，验证扫题与点击
python tools/smoke_console.py      # 控制台：真浏览器里量自动滚动/暂停/回到底部
python tools/smoke_live.py         # 端到端：Chrome + 真实模型，一路验到事件流
```

三个冒烟测试都开真 Chrome、都不访问雨课堂、不修改任何线上数据。
`smoke_live.py` 需要有效的 API Key。

## 常见问题

**调试端口起不来 / 等待超时**
该 `user_data_dir` 已被另一个 Chrome 占用。关掉对应窗口，或换一个目录。
也可手动启动后把 `browser.auto_launch` 设为 `false`：

```powershell
& "C:\Program Files\Google\Chrome\Application\chrome.exe" `
  --remote-debugging-port=9222 --user-data-dir="D:\chrome-debug"
```

**401 / 鉴权失败**
`.env` 里的 Key 无效或已失效。注意要用**当前服务商**的变量
（切到千问就得填 `DASHSCOPE_API_KEY`）。

**抓到题了但答案总是空**
模型没按 JSON 输出。程序已做降级解析（支持 `答案：X` 老格式）；仍失败就换个模型，
或在 `deepseek.extra_prompt` 里强调"只输出 JSON"。

**不想让它自动点提交**
`answer.auto_submit: false`，只点选项，提交交给你。

**答案提交后没反应**
`api` 模式依赖的接口字段可能已变，属于实验性。改用 `dom`，那条路径走页面自己的代码。

## 已知限制

- `api` 模式的作答接口字段结构可能有版本差异，实验性。
- DOM 扫描依赖「选项以 `A.` / `B、` / `C）` 开头」这个视觉约定，改版后需用
  `option_selectors` 校准。
- 主观题和纯填空题的页面落点无法靠文字可靠定位，`dom` 模式下不自动填写，只打印答案。
- 使用 `manual` 以外的模式前，请确认当前页面确实是你本人的课堂。
