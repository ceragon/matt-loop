---
name: loop
description: 走管道：每轮要回复执行者的信息都经 loop 推回——把用户放行的提示词发进 herdr 目标会话并取回结果（裁剪＋抽取成 JSON，默认直连 API、pi 备选）；监听只读随时可，仅报错时可直推（仍须放行）。可选 --reset 先发 /new /clear /compact；调会话起始 skill（wayfinder/grill-with-docs/implement/implement-spec/prototype）必须带 --reset。手里有放行 id、提示词文件、目标地址，要发送并取回新结果时用。
---

# loop：走管道

**走管道**：每一轮要发给执行者的回复，都经本 skill 的 `scripts/dispatch.py` 推回。和用户讨论定稿后，写放行文件、调 dispatch 发送＋取回、判读结果、再讨论——下一轮回复仍走管道。监听（`hc get`/`hc read` 只读）随时可；仅当 dispatch 报错才可直推，直推内容仍须用户放行。

dispatch 做发送、等待、取回、落盘这些机械活，抽取器（默认直连 API，pi 备选，见「抽取后端」）读整屏原料做裁剪＋抽取；异常由脚本直接返回 error，不进抽取。架构见 `docs/adr/0002`、`docs/adr/0005`，用词见根目录 `GLOSSARY.md`；error kind 表、机械保证、发布前检查见 `REFERENCE.md`。

## 每轮循环（走管道）

1. **讨论定稿**：和用户定出要回复执行者的全文。完成判据：用户对全文说「发」，拿到唯一放行 id（对内容方向说「行」不算放行）。
2. **写放行文件**：全文写成 UTF-8 文件，放 `$TMPDIR/loop/releases/<放行id>.txt`（目录不存在就建），放行后一字未改。完成判据：文件就绪（dispatch 核对 sha256，改动即拒发）。
3. **走管道推回**：调 dispatch 发送＋取回（见「调用」「交付」）。完成判据：dispatch 返回、`.result.json` 生成。用 `--reset` 时该放行一并授权重置前置＋主任务（前置是枚举里可预知的固定命令）。
4. **判读结果**：读 `.result.json`＋`run_record`，整理成选项卡给用户。完成判据：用户看到结果、给出下一轮方向。
5. 回到 1，下一轮回复仍走管道。

## 调用前

本机装有 `hc`、`uv` 与 Python ≥ 3.10 的解释器（脚本要求 `>=3.10`）；抽取默认直连 API，需 `LOOP_API_BASE/KEY/MODEL` env（见「抽取后端」），选 pi 备选才需要 `pi`。工具先按 PATH 找，PATH 没有时脚本自动探 `~/.local/bin`、homebrew、nvm node bin——后台 job 缺 PATH 也能起；探不到才报 `missing_tool`。PATH 由脚本兜底，**不需手写 export PATH 包装脚本**；后台直接用调用方自己的 bg 机制（见「交付」）。

## 调用

```bash
python3 scripts/dispatch.py \
  --target <终端地址> --release-id <放行id> --prompt-file <提示词文件> \
  --expect-proc <前台进程子串>
```

- `python3` 直跑：脚本无第三方依赖、头部声明 `requires-python = ">=3.10"`。版本门 `>=3.10` 直跑；`<3.10` 自动 `find_tool` 探 uv 并经 `uv run --no-python-downloads` 用已有 3.10+ 重跑（不下载），uv 探不到才报 `error(kind=python_too_old)`。后台精简 PATH 里 python3 也在，uv 退居内部兜底，调用方不用管 PATH、不需手写包装。
- `--expect-proc`：身份预检，强烈建议传（grok 传 `grok`、claude 传 `claude`）；匹配 foreground.name/argv[0]/status.app 任一（claude 的 name 可能是版本号，认 argv[0]/app），不符即拒发。标题易变（默认「Claude Code」→跑起来才改），不作预检判据。
- `--reset {none,new,clear,compact}`（默认 `none`）：主任务前的重置前置。选 `new`/`clear`/`compact` 就在同一 target 先 `--if idle` 发字面命令 `/new`/`/clear`/`/compact`，等落定（idle/done＋屏幕静置）再发主任务；`none` 即单 prompt，向后兼容。**发哪条由调用方按目标 agent 认哪个挑**（claude/grok 的 `/new`≡`/clear` 都认，只认 `/new` 的挑 `new`），脚本不按 agent 映射。前置不抽取、不落 run_record；前置或主任务任一步发不出即整轮停，error 带 `reset_sent` 标明部分状态。`/clear` 是本地命令、可能不进 working，前置落定用屏幕静置而非 turn 判据。**提示词行首调用会话起始 skill 时必须带 `new`/`clear`/`compact`，`none` 会被脚本拒发**（见「会话起始 skill」）。
- 等待与抽取默认值一般不用动：墙钟 3600 秒（主任务发送成功起计）、check-in 180 秒、落定静置 10 秒 × 2 次、抽取护栏 1200 秒（`--wall-clock` / `--check-in` / `--settle-seconds` / `--settle-checks` / `--extract-timeout` 可覆盖）。读取固定 `hc read --lines 0`。
- `--loop-dir` 默认 `$TMPDIR/loop`（随 OS 临时清理，记录不持久）；`--prompt-file` 传 `$TMPDIR/loop/releases/<放行id>.txt`；`--pi-model` 透传 `pi --model`（仅 pi 备选后端生效）。

## 会话起始 skill（工程纪律）

五个 skill 是**会话起始 skill**：`wayfinder`、`grill-with-docs`、`implement`、`implement-spec`、`prototype`。它们只能作为空会话的开场任务：（继续）调用时**必须带 `--reset new/clear/compact`** 先清目标执行者会话，不能带旧上下文接着跑；调用方自己的会话不动。出处是 `/ask-matt` 工作流（默认调用方已装 Matt 全套 skill）：连续性在工件里——地图 issue、GLOSSARY/ADR、票、handoff 文件——不在聊天里，清会话不丢东西。

- **调用独占一行**：放行提示词里，会话起始 skill 的斜杠命令写在行首（可带参数，如 `/wayfinder #7`）。脚本按行首匹配做机械预检：命中且 `--reset=none` → 写台账前拒发（`reset_required`，退出码 2），零发送、放行 id 不消耗，补 `--reset` 同 id 重试。
- **中途不触发**：清完以 `/grill-with-docs` 开场后，同会话接着跑 `to-spec`/`to-tickets` 不再清（ask-matt 的 Context hygiene：grill→spec→tickets 一个不间断窗口）；`tdd`、`code-review`、`retro` 等中途 skill、回答提问的普通回复、行文中提及（如「答完再跑 /implement」）都不触发。
- **prototype 的桥是 handoff**：旧会话写 handoff 文件，新会话以 `/prototype` 对着该文件开场。
- `compact` 也算重置（留摘要可接受），唯一拒发值是 `none`；选哪条命令仍按目标 agent 认哪个挑。

## 抽取后端（默认直连 API）

裁剪＋抽取默认**直连 OpenAI 兼容 chat completions**（ADR-0005），pi 降为显式备选。后端是部署级配置，走 env：

- `LOOP_EXTRACT_BACKEND=api|pi`，默认 `api`；非法值启动即报错。
- `api` 需三个 env **全必填、无默认**：`LOOP_API_BASE`（基址到 `/v1`，脚本拼 `/chat/completions`）、`LOOP_API_KEY`（Bearer，零回显）、`LOOP_API_MODEL`（模型 id 原样透传）。缺任何一个**启动预检**即报 `api_not_configured`（只列变量名），不发主任务、放行 id 不消耗。与 pi 网关复用同一把钥匙时，值指过去即可（如 `LOOP_API_KEY="$PAW_GATEWAY_KEY"`），名字归 loop。
- 请求形状：system=extract.md，user=指令＋内联整屏原文；temp 0、max_tokens 默认 32768（`LOOP_API_MAX_TOKENS` 可覆盖，非法值启动报错）、超时复用 `--extract-timeout`、不重试、不流式、不发 response_format。出网代理与 webhook 同一套决议（ADR-0003）。
- 失败映射沿用既有 kind：HTTP → `extraction_failed`（带 `HTTP <码>`，不读响应体）；超时 → `tool_timeout`；其它网络异常 → `extraction_failed`（类名）。密钥/URL 零回显；原文已落盘，可人工重做。
- `pi` 备选：行为照旧（@文件 附件、`--pi-model` 透传），机器上要有 pi；`api` 路径不依赖 node/pi。
- result 与台账带 `extract_backend`（api|pi）标明本轮走的哪条路。
- **迁移**：旧部署没配 `LOOP_API_*` 的，跟新代码后要么配齐三个变量，要么设 `LOOP_EXTRACT_BACKEND=pi` 维持现状，否则启动即 `api_not_configured`。

## 交付：后台跑＋完成通知

一轮最长约 80 分钟（抽取实测每轮 5–11 分钟，pi 路径数据）。用调用方自己的后台任务跑 dispatch、靠完成通知收结果，再读 `$TMPDIR/loop/runs/<放行id>.result.json`：

- **DS Bot（默认）**：`run_in_background` 跑 dispatch → job 完成通知唤醒 → 读结果文件。
- **Grok Bot**：配 `LOOP_WEBHOOK_URL`＋`LOOP_WEBHOOK_SECRET`（示例见 `webhook.env.example`），dispatch 完成时 POST 结果 JSON 唤醒它 → 读结果文件。
- **其他调用方**：仿 DS Bot（后台任务＋完成通知）。

结果文件永远是事实源；webhook 只是通知，发失败不连累整轮（result 记 `webhook: off/sent/failed`，失败另记 `webhook_error`：HTTP 状态码或异常类名，以及 `webhook_error_detail` 脱敏诊断；都不含 URL/密钥）。stderr 打印同一份诊断。webhook POST 默认跟随本机系统代理：标准 `http_proxy`/`https_proxy` 显式配置时优先，否则读系统代理；系统例外表或 `no_proxy` 命中则直连（内网、localhost 不受影响）。拿到 `failed` 时先看 `webhook_error_detail` 再判断，别把 DNS、TLS、代理、连接任一直接当已证实根因。短超时包装器（`hc exec` 默认 60 秒/上限 600 秒、超时不够的 bash）会到点把 dispatch 连同子进程杀掉、留下 `phase=waiting` 台账——给不了足够超时就用上面的后台路径。

## 返回契约（每轮判读）

stdout 恰好一行 JSON，同一字符串写入 `$TMPDIR/loop/runs/<放行id>.result.json`（成功与 error 都写，逐字一致，是**最终交付**）。成功（退出码 0）：

```json
{"ok": true, "target": "t7", "reset": "none", "turn": 3, "status": "idle",
 "complete": true, "complete_reason": [], "anchor_found": true, "elapsed_s": 690.5, "chars": 1834,
 "run_record": "$TMPDIR/loop/runs/<放行id>.md", "raw_record": "$TMPDIR/loop/runs/<放行id>.raw.md",
 "prompt_sha256": "…", "rewritten": {}, "extract_backend": "api", "webhook": "off",
 "needs_user": false, "questions": [], "conclusion": "…", "blocked": ""}
```

- `run_record`（`.md`）是抽取器从整屏**裁剪出的干净回复全文**（剔 logo/思考计数/工具痕迹/转圈行/空行/输入框/状态栏，逐字保留正文），是回看对象；`raw_record`（`.raw.md`）是整屏原料（逐字读回，制表符按终端制表位展开为空格，审计用）。error 时尚无干净全文，`run_record` 退回指整屏原料。
- `needs_user`（布尔）：回复里是否有需用户决定的事。三个选段字段是抽取器从原文**逐字摘出**的句子（语义见 `prompts/extract.md`）：`questions` 每条问题/审批一个元素（问题＋选项＋推荐整块）、`conclusion` 结论段（无则 `""`）、`blocked` 弹框原句（无则 `""`）。干净全文 `reply` 不进本 JSON，落在 `run_record`；要展示回复内容直接用 `run_record` 原文。
- `rewritten`：脚本对选段字段逐一做原文子串校验，搜不到的在此标出、值为最接近原文片段；`{}` 即全部逐字命中。**非空时对应字段是抽取器的改写，不可当原话引用；`reply` 被标时回看 `raw_record`。**
- `reset`：本轮前置类型（`none`/`new`/`clear`/`compact`）；`extract_backend`：本轮抽取后端（`api`/`pi`，error 时也带）；`complete`/`complete_reason`：完整性判定，只看折叠标记（成功恒 `complete:true`）；`anchor_found`：提示词回声是否还在当前屏——仅信息，长任务回声滚出屏为 `false`，不影响 `complete`；`elapsed_s`：主任务发送成功到取回落定的秒数，不含前置与抽取；`webhook`：完成通知状态（鉴权走 `Authorization: Bearer`）；`webhook_error`：仅失败时出现，HTTP 状态码（如 `"HTTP 401"`）或异常类名（如 `"URLError"`）。
- `webhook_error_detail`：仅失败时出现，与 stderr 同一份脱敏诊断。字段：`type`（外层异常类名）、`reason_type`（`URLError.reason` 是异常时的类名）、`errno`/`errno_name`（可用时，如 `61`/`ECONNREFUSED`；DNS 给 `EAI_*`）、`is_timeout`（布尔）、`ssl_verify_code`（`SSLCertVerificationError.verify_code`，可用时）、`http_status`（仅 HTTPError）、`proxy`（`{"configured": "yes|no|unknown", "matches": "yes|no|unknown", "source": "env|system|bypass|none|unknown"}`：是否配置代理、本请求是否命中、代理来自环境变量还是系统配置）。不含 URL、密钥、主机名、请求头或字符串形式的 reason；判断不了就标 `unknown`。
- **三个选段只摘原文片段、不保证覆盖全文**；要不要批准、要不要改接口的判断，必须回看 `run_record`，不能只凭 JSON 选段。

拿到结果整理成**选项卡**交给用户，原文不直接转给用户。失败（退出码 1；参数错误 2）返回 `{"ok": false, "error": {"kind", "message", "hints"}}`；kind 含义与调用方动作见 `REFERENCE.md`。监听只读随时可；仅报错时可直推（仍须放行）。

## 本地记录（默认 `$TMPDIR/loop`，随 OS 临时清理）

- `releases/<放行id>.txt`：放行提示词（调用方写，dispatch 原文照发）。
- `runs/<放行id>.raw.md`：整屏原料（审计）。
- `runs/<放行id>.md`：干净回复全文（回看对象 `run_record`）。
- `runs/<放行id>.json`：台账，先记账后发送；一个放行只跑一轮，至多发送成功两次（前置＋主任务各一次），台账存在即拒同 id 重发。
- `runs/<放行id>.result.json`：最终交付，后台调用靠读它收结果；重发拒绝仍以台账为准。

记录与台账都随 OS 临时清理、不持久：事后审计与拒重发保护止于重启（放行 id 每次唯一，跨重启复用风险低）。要长期留存某轮，自行另存。

## 护栏

调用方要守的两条：**先放行，后调用**；**调会话起始 skill 必须 `--reset`**（见「会话起始 skill」，脚本对行首调用机械兜底）。其余机械保证由脚本兜底，细节见 `REFERENCE.md`。