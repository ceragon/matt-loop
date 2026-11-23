# loop REFERENCE（按需查阅）

SKILL.md 的披露参考：只在对应分支才读。error kind 表在拿到 error 时查；机械保证是脚本兜底细节；发布前检查在把 `loop/` 公开前跑。用词见根目录 `GLOSSARY.md`。

## error 返回契约

失败退出码 1；参数/配置类错误退出码 2（argparse 报错无 JSON；`reset_required`、`api_not_configured`、`bad_extract_backend`、`bad_api_max_tokens` 是预检拒发，退出码 2、有 JSON）。stdout 一行、同一字符串写 `.result.json`：

```json
{"ok": false, "release_id": "…", "target": "…", "error": {"kind": "…", "message": "…", "hints": ["…"]}}
```

`release_id` 与 `target` 必带；在放行 id 校验后算出 `task_name` 的，也一并带顶层 `task_name`（含发给 webhook 的同一份 JSON）。发送后失败的 error 另带 `elapsed_s`；原文已落盘的另带 `run_record`。用了 `--reset` 的 error 另带 `reset`（类型）与 `error.reset_sent`（前置是否已发出，标明「前置已发／主任务未发」这类部分状态）。放行 id 本身非法（`bad_release_id`）或参数错误的 error 只有 stdout（及配置了时的 webhook）——没有可安全落盘的文件名。

## error kind 表

| kind | 含义 | 调用方动作 |
| --- | --- | --- |
| `python_too_old` | 解释器低于 3.10 且 uv 自举失败（uv 探不到或重 exec 失败） | 装 3.10+ 解释器，或装 uv（脚本自动经 `uv run --no-python-downloads` 重跑） |
| `duplicate_release` | 该放行 id 已有台账/落盘 | 查台账；重发须用户重新放行、新 id |
| `reset_required` | 提示词行首调用会话起始 skill（wayfinder/grill-with-docs/implement/implement-spec/prototype）、`--reset=none` 且目标 `turn != 0`（或缺失）；在 `hc get`＋身份检查之后、写台账前拒发，零发送（ADR-0004） | 补 `--reset new/clear/compact`（agy/pi 认 `new`，claude 认 `clear`，codex 认 `new`/`clear`）用**同一放行 id** 重试（id 不消耗）；清单与边界见 SKILL.md「会话起始 skill」 |
| `api_not_configured` / `bad_extract_backend` / `bad_api_max_tokens` | 抽取后端启动预检不过（ADR-0005）：backend=api 缺 `LOOP_API_BASE/KEY/MODEL`；`LOOP_EXTRACT_BACKEND` 值非法；`LOOP_API_MAX_TOKENS` 非正整数。未发送 | 配齐/改正 env 用同一放行 id 重试，或设 `LOOP_EXTRACT_BACKEND=pi`；见 SKILL.md「抽取后端」 |
| `not_idle` / `identity_mismatch` | 预检不过，未发送 | 整理成选项卡告知用户 |
| `send_refused` | 前置或主任务 hc 拒发且 done 补发不适用或仍失败；看 `reset_sent` 判断前置是否已发 | 同上 |
| `dialog_blocked` | 目标停在弹框（审批/提问/认证） | 告知用户去目标屏幕前处理；不按键、不代答 |
| `timeout` | 墙钟到点（前置或主任务），目标仍未落定 | 告知用户；前置超时则主任务未发、原文未取回 |
| `not_started` / `target_exited` / `target_error` | 发出后未开工 / 程序退出 / 自报 error | 告知用户 |
| `incomplete_result` | 折叠标记命中（▲▼ 藏了内容，当前屏看不到完整回复） | 原文已落盘，请用户回看 `run_record` |
| `extraction_rewritten` | 抽取字段全部未通过逐字校验 | 原文已落盘，请用户回看；可人工重做抽取 |
| `extraction_failed` / `extraction_invalid` / `tool_timeout` | 抽取失败或子进程超护栏 | 原文已落盘，可人工重做抽取 |

完整性硬判据只剩 `fold_markers`（回读含 ▲ 或 ▼，目标 TUI 折叠了内容，当前屏看不到完整回复）：命中即落盘后返回 `error(kind=incomplete_result)`，不进抽取。锚点（提示词第一个非空行前 24 字符）降级为信息字段 `anchor_found`：完整性检查在 turn>基线＋落定之后，本轮确已被处理，长任务提示词回声滚出当前屏（`--lines 0`）是常态、回复仍在屏底，故锚点搜不到不再阻断（PAW 实测多次误判后降级）。hc read 自己的 `complete` 字段只记台账、不作判据。

监听（`hc get`/`hc read` 只读）随时可；直推（绕过 dispatch 主动发）仅在报错时，且内容仍须用户放行。正常回复一律走管道。

## 机械保证（脚本兜底，调用方无需自查）

原文照发、一个放行至多发送成功两次（重置前置＋主任务，各一次）、永不 `--force`、不碰 `hc keys`、身份预检（`--expect-proc` 匹配 foreground.name/argv[0]/status.app 任一）、sha256 记账并随成功输出回报、不完整不进抽取、改写字段标 `rewritten`。重置前置按枚举校验、与主任务共用同一发送门，任一步发不出即整轮停。

会话起始 skill 纪律（ADR-0004）：提示词**行首**出现 `/wayfinder`、`/grill-with-docs`、`/implement`、`/implement-spec`、`/prototype` 之一（允许行前空白；命令后须非词字符且非连字符，`/implementing`、`/implement-foo` 不误中，`/wayfinder#7` 算调用；`implement-spec` 优先报长名）且 `--reset=none` 时，在 `hc get`＋身份预检之后、写台账与任何发送之前校验目标 `turn`：`turn == 0`（精确整数 `0`，尚未跑过任何轮次的全新空会话）直接豁免放行；`turn > 0` 或缺失时拒发（`reset_required`，退出码 2，message 带 skill 名、行号与当前 `turn` 值）。行文中提及不触发；放行 id 不消耗，补 `--reset` 同 id 可重试。`new`/`clear`/`compact` 都算重置。「调用独占一行」是 SKILL.md 的文字纪律，机械检查只兜行首这一层；`not_idle`/`identity_mismatch` 先于 `reset_required`。

抽取后端（ADR-0005）：`LOOP_EXTRACT_BACKEND=api|pi`，默认 `api`。`api` 走 OpenAI 兼容 chat completions：`LOOP_API_BASE`（到 `/v1`，容忍尾斜杠，脚本拼 `/chat/completions`）、`LOOP_API_KEY`（Bearer）、`LOOP_API_MODEL` 全必填无默认，启动预检缺失即 `api_not_configured`（只列变量名）；`LOOP_API_MAX_TOKENS` 可选、默认 32768、非正整数启动即 `bad_api_max_tokens`。请求 system=extract.md、user=指令＋内联整屏原文，temp 0、不流式、不重试、不发 response_format，超时复用 `--extract-timeout`；代理决议与出口和 webhook 同源（ADR-0003）。失败只记状态码/类名（HTTP → `extraction_failed` 带 `HTTP <码>`、不读响应体；超时 → `tool_timeout`；其它网络异常 → `extraction_failed` 带类名），密钥/URL 零回显。`pi` 备选路径照旧（@文件、`--pi-model`），`api` 路径不找 pi、不依赖 node。result 与台账 params 带 `extract_backend`（error 也带）。

发送门：首发只 `--if idle`；被拒（`status_mismatch`，零字节进入，不算重发）后 `hc get` 复核确认 `done`，补发前再核一次非 working/blocked，才不带 `--if` 补发一次；仍失败即 error。

抽取逐字校验：选段字段逐一做原文子串校验（含去空白折叠），搜不到标 `rewritten` 附最接近片段，全部搜不到则 `error(extraction_rewritten)`；`reply` 干净全文先整体、再逐行校验，未过逐字校验的行标 `rewritten["reply"]`。

可选 webhook：env `LOOP_WEBHOOK_URL`＋`LOOP_WEBHOOK_SECRET` 都配才发，成功/报错都 POST 结果 JSON，鉴权走 `Authorization: Bearer <密钥>`，不进日志、不进 stdout，~10 秒超时；发失败不连累整轮，result 记 `webhook: off/sent/failed`，失败另记 `webhook_error`（HTTP 状态码如 `"HTTP 401"`，或异常类名如 `"URLError"`）与 `webhook_error_detail`（脱敏诊断，stderr 打印同一份），结果文件仍是事实源。

webhook 出网默认跟随本机系统代理（见 ADR-0003）：标准 `http_proxy`/`https_proxy` 显式配置时优先（source=env）；否则读系统代理（macOS SystemConfiguration，source=system）；系统例外表或 `no_proxy` 命中则直连（source=bypass）；都没配是 none；探测失败为 unknown。不能用 `getproxies()` 直接判——只设了 `no_proxy` 或非标准 `*_proxy`（如 `JEV_UPSTREAM_PROXY`）时它也返回非空字典，却没有任何 http/https 键，urllib 实际会直连。该选择只作用于 webhook POST，不改进程全局代理、不关证书校验。

`webhook_error_detail` 只收类名、数字、布尔和三态字符串，不收 `str(e)`/`repr(e)`/响应体/字符串 reason，避免 URL、密钥、主机名、请求头回显。字段：

| 字段 | 含义 |
| --- | --- |
| `type` | 外层异常类名（如 `URLError`、`HTTPError`、`TimeoutError`） |
| `reason_type` | `URLError.reason` 是异常时的类名（如 `ConnectionRefusedError`、`gaierror`、`SSLCertVerificationError`）；reason 是字符串时不出现 |
| `errno` / `errno_name` | 可用的 errno 与名字（如 `61`/`ECONNREFUSED`；DNS 给 `EAI_NONAME`/`EAI_AGAIN`） |
| `is_timeout` | 是否超时（外层或 reason 为 `TimeoutError`） |
| `ssl_verify_code` | `SSLCertVerificationError.verify_code`（如 `18` 自签、`20` 缺中间证书；含义以 OpenSSL 为准） |
| `http_status` | 仅 `HTTPError`：HTTP 状态码 |
| `proxy.configured` / `proxy.matches` / `proxy.source` | 是否配置了代理 / 本请求是否实际命中 / 代理来源（`env`/`system`/`bypass`/`none`/`unknown`）；不输出代理地址或凭据 |

下一次 `failed` 时按证据读，不预设根因：`http_status` 非空＝已连上端点、按状态码（401 多为密钥；5xx 为服务端）；`is_timeout=true`＝超时；`errno_name` 为 `ECONNREFUSED`/`ECONNRESET`＝连接层被拒/被断；为 `EAI_*`＝DNS 解析失败；`ssl_verify_code` 非空＝证书校验失败；`proxy.source=system` 说明这次走的是本机系统代理，`bypass` 是例外表放行、`none` 是没配代理，`matches=yes` 时代理只是有嫌疑、仍须其它证据。诊断只记录事实，不关证书校验、不改系统代理设置；修法等下一次真实运行的证据再定。

工具定位：`hc`/`uv`（及 `backend=pi` 时的 `pi`）先 `shutil.which`（尊重显式 PATH），找不到再探 `~/.local/bin`、`/opt/homebrew/bin`、`/usr/local/bin`、nvm node bin（`~/.nvm/versions/node/*/bin`）；后台 job 缺 PATH 也能起，探不到才报 `missing_tool`。调用方 `python3` 直跑即可：版本门 `>=3.10` 直跑，`<3.10` 由 `handle_old_python` 探 uv 并 `os.execv` 经 `uv run --no-python-downloads` 自举重跑（env `LOOP_UV_REEXEC` 防循环），uv 退居内部兜底，调用方不用管 PATH。

本地记录归置：`--loop-dir` 默认 `$TMPDIR/loop`（`tempfile.gettempdir()/loop`），`runs/` 放记录（台账/raw/record/result）、`releases/` 放放行提示词；CWD 无关、结果绝对路径回报，在哪跑都落同一处。全部随 OS 临时清理、不留持久审计，台账拒重发保护止于重启（放行 id 每次唯一，跨重启复用风险低）；脚本不加清理垫片。PATH 由 `find_tool` 兜底，调用方不需手写 export PATH 包装脚本。

## 发布前检查

把 `loop/` 公开到 GitHub 前，在仓库根目录跑一遍。第一行的 `-e` 里补上你环境里的机器名、主机名、内网域名、私有组织名、用户名；第二行是通用模式，直接可跑：

```bash
grep -rniE -e '<机器名>' -e '<主机名>' -e '<内网域名>' -e '<组织名>' -e '<用户名>' loop/
grep -rnE '/Users/|/home/|[[:space:]](10|127)\.[0-9]+\.[0-9]+\.[0-9]+|192\.168\.|172\.(1[6-9]|2[0-9]|3[01])\.' loop/
```

命中即改：机器名、主机名换通用表述，绝对路径换占位符（`<工作目录>`、`<放行id>`），会话地址、账号一律不写。`herdr`、`hc`、`pi` 这类公开产品名可以保留。扫描命令自身的模式串会命中自己，属预期误报。