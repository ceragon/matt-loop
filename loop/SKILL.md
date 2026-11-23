---
name: loop
description: 走管道：每轮要回复执行者的信息都经 loop 推回——把用户放行的提示词发进 herdr 目标会话并取回结果（裁剪＋抽取成 JSON，默认直连 API、pi 备选）；监听只读随时可，仅报错时可直推（仍须放行）。可选 --reset 先发 /new /clear /compact；可选 --target-model 先发 /model <名字> 切目标模型（独立于 --reset，顺序 重置→切模型→主任务）；可选 --switch-agent <启动命令> 换执行者 kind（与 reset/target-model 互斥；shell 包装终端同地址换、直接起 agent 的终端显式新地址并回报 target_new）；调会话起始 skill（wayfinder/grill-with-docs/implement/implement-spec/prototype）时，全新空会话（turn == 0）自动豁免，复用旧会话（turn > 0）必须带 --reset。手里有放行 id、提示词文件、目标地址，要发送并取回新结果时用。
---

# loop：走管道

**走管道**：每一轮要发给执行者的回复，都经本 skill 的 `scripts/dispatch.py` 推回。和用户讨论定稿后，写放行文件、调 dispatch 发送＋取回、判读结果、再讨论——下一轮回复仍走管道。监听（`hc get`/`hc read` 只读）随时可；仅当 dispatch 报错才可直推，直推内容仍须用户放行。

dispatch 做发送、等待、取回、落盘这些机械活，抽取器（默认直连 API，pi 备选，见「抽取后端」）读整屏原料做裁剪＋抽取；异常由脚本直接返回 error，不进抽取。架构见 `docs/adr/0002`、`docs/adr/0005`，用词见根目录 `GLOSSARY.md`；error kind 表、机械保证、发布前检查见 `REFERENCE.md`。

## 每轮循环（走管道）

1. **讨论定稿**：和用户定出要回复执行者的全文。若准备调用会话起始 skill，先 `hc get` 看一眼目标 `turn`：`turn == 0`（全新空会话）不用带 `--reset`；`turn > 0`（复用旧会话）就在这一步向用户讨放行时**一并给出推荐的 `--reset` 方式**，一次放行授权完，首调直带 `--reset`，别先拿 `none` 去撞 `reset_required` 再回来二轮请示。本轮若要切目标模型，也在这一步一并确认 `--target-model` 与模型名（见「调用」）。完成判据：用户对全文（及需要时的重置方式、模型名）说「发」，拿到唯一放行 id（对内容方向说「行」不算放行）。
2. **写放行文件**：全文写成 UTF-8 文件，放 `$TMPDIR/loop/releases/<放行id>.txt`（目录不存在就建），放行后一字未改。完成判据：文件就绪（dispatch 核对 sha256，改动即拒发）。
3. **走管道推回**：调 dispatch 发送＋取回（见「调用」「交付」）。完成判据：dispatch 返回、`.result.json` 生成。用 `--reset`/`--target-model` 时该放行一并授权全部前置＋主任务（重置是枚举里可预知的固定命令，切模型是字面 `/model <名字>`）。
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
- `--reset {none,new,clear,compact}`（默认 `none`）：主任务前的重置前置。选 `new`/`clear`/`compact` 就在同一 target 先 `--if idle` 发字面命令 `/new`/`/clear`/`/compact`，等落定（idle/done＋屏幕静置）再发主任务；`none` 即单 prompt，向后兼容。**发哪条由调用方按目标 agent 认哪个挑**（`agy`/antigravity、`pi` 认 `/new`＋`/compact`；`claude` 认 `/clear`＋`/compact`，不认 `/new`；`codex` 认 `/new`/`/clear`/`/compact`；`grok` 的 `/new`≡`/clear` 都认），脚本不按 agent 映射。前置不抽取、不落 run_record；前置或主任务任一步发不出即整轮停，error 带 `reset_sent` 标明部分状态。`/clear` 是本地命令、可能不进 working，前置落定用屏幕静置而非 turn 判据。**提示词行首调用会话起始 skill 时：`turn == 0`（全新空会话）可不传 `--reset` 直接发；`turn > 0` 或未知时必须带 `new`/`clear`/`compact`，`none` 会被脚本拒发**（见「会话起始 skill」）。
- `--target-model <名字>`（默认不传，ADR-0006）：切模型前置。重置（如有）之后、主任务之前，在同一 target 先 `--if idle` 发字面命令 `/model <名字>`，等落定（idle/done＋屏幕静置）再发主任务。**与 `--reset` 独立**：可单独传（任意一轮都能随下一条消息带模型切换），同传时顺序固定 **重置 → 切模型 → 主任务**。名字**原样透传**：脚本不认识模型、不按 agent 映射、不核实生效、不预读跳过；`target_model` 是请求值非验证值，名字对错归调用方纪律。**哪些 agent 认行内 `/model <名字>`**（已核实）：`agy`/antigravity 认 name/slug/label 并存为默认（已是该模型报 `Model already set`、名字不认打印有效列表，都不弹框；注意 `/model <名字> <提示词>` 是其一次性语义，多词值按字面发出可能触发，别传多词名字）；`claude` 认别名或完整模型 ID（如 `/model sonnet`）；`pi` 认 `/model [provider/model]`；`grok` 认 `/model <名字> [window] [effort]`；**`codex` 的裸 `/model` 开选择器、行内名字未实证——别对 codex 传 `--target-model`**（选择器 → blocked → `dialog_blocked` 停轮），换 codex 模型用启动旗标 `-m` 重开会话。前置不抽取、不落 run_record；任一步发不出即整轮停，error 带 `model_sent` 标明部分状态。会话起始 skill 纪律不变：`turn > 0` 且 `--reset=none` 仍拒发 `reset_required`，切模型不能替代重置。
- `--switch-agent <完整启动命令>`（默认不传，ADR-0007）：换 kind 前置，与 `--reset`/`--target-model` **互斥**（同传报 `bad_args`，退出码 2、放行 id 不消耗）——换 kind 起全新 turn 0 会话、模型写进启动命令，无需再重置/切模型。启动命令**字面透传**（如 `--switch-agent "claude --dangerously-skip-permissions"`），脚本不映射 kind→命令；`--expect-proc` 须**同步改成新 kind**。按 `hc get` 启动 argv 判终端形态分流：
  - **持久 shell 包装终端**（程序是 `zsh -l` 等、agent 是子进程）→ **同地址换**：调用方传 `--exit-command <文本>`（走 `hc send`，如 claude 的 `/exit`）或 `--exit-keys <按键>`（走 `hc keys`，如 pi 的 `ctrl+d`，二者互斥）退旧 agent → 等前台回 shell → send 启动命令 → 等新 agent 就绪 → 核身份 → 发主任务。**地址不变**，结果无 `target_new`。前台已是空闲 shell（无 agent）则跳过退旧；有 agent 在跑却没传退出旗标 → `switch_exit_required`。
  - **直接起 agent 的终端**（程序=agy/pi 等，如 t24）→ **显式新地址**：kill 旧终端 → `hc run` 新终端（沿用旧 cwd）→ 等就绪 → 核身份 → 主任务发到新终端。结果 `target` 恒为原地址、另报 **`target_new`（新 t 号），调用方据此更新 `--target`**。
  - **安全**：shell 分支任一环节失败即 blocked、**绝不静默换地址**（退旧后终端 finished → `switch_recovery_failed`；超时 → `switch_timeout`）；direct 分支新地址是显式回报。换 kind 起全新会话，会话起始 skill 纪律预检跳过。**实证**：keep＋信号恢复对真实 agent 不可行（agent 接 SIGINT/esc、干净退出→finished），同地址靠 shell 包装而非 keep；退旧方式按旧 agent 认哪个挑（pi=ctrl+d 走 keys、claude=/exit 走 send），归调用方纪律。
- 等待与抽取默认值一般不用动：墙钟 3600 秒（主任务发送成功起计）、check-in 180 秒、落定静置 10 秒 × 2 次、抽取护栏 1200 秒（`--wall-clock` / `--check-in` / `--settle-seconds` / `--settle-checks` / `--extract-timeout` 可覆盖）。读取固定 `hc read --lines 0`。
- `--loop-dir` 默认 `$TMPDIR/loop`（随 OS 临时清理，记录不持久）；`--prompt-file` 传 `$TMPDIR/loop/releases/<放行id>.txt`；`--pi-model` 透传 `pi --model`（仅 pi 备选后端生效）。

## 会话起始 skill（工程纪律）

五个 skill 是**会话起始 skill**：`wayfinder`、`grill-with-docs`、`implement`、`implement-spec`、`prototype`。它们只能作为空会话的开场任务：若目标 `hc get` 的 `turn == 0`（尚未跑过任何轮次的全新空会话），脚本**自动豁免**，不带 `--reset` 直接发（省去空等重置）；若复用已有轮次的旧会话（`turn > 0` 或 `turn` 未知），则**必须带 `--reset new/clear/compact`** 先清目标执行者会话，且应在 Step 1 讨放行时一并把重置选项与推荐值带给用户确认；调用方自己的会话不动。出处是 `/ask-matt` 工作流（默认调用方已装 Matt 全套 skill）：连续性在工件里——地图 issue、GLOSSARY/ADR、票、handoff 文件——不在聊天里，清会话不丢东西。

- **调用独占一行**：放行提示词里，会话起始 skill 的斜杠命令写在行首（可带参数，如 `/wayfinder #7`）。脚本在 `hc get`＋身份预检之后、写台账之前做机械检查：命中且 `--reset=none` 且 `turn != 0`（或 `turn` 缺失）→ 写台账前拒发（`reset_required`，退出码 2，message 带当前 `turn` 值），零发送、放行 id 不消耗，补 `--reset` 同 id 重试；`turn == 0` 直接放行。
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

结果文件永远是事实源；webhook 只是通知，发失败不连累整轮（result 记 `webhook: off/sent/failed`，失败另记 `webhook_error`：HTTP 状态码或异常类名，以及 `webhook_error_detail` 脱敏诊断；都不含 URL/密钥）。stderr 打印同一份诊断。webhook POST 默认跟随本机系统代理：标准 `http_proxy`/`https_proxy` 显式配置时优先，否则读系统代理；系统例外表或 `no_proxy` 命中则直连（内网、localhost 不受影响）。选定出口对传输异常与 `HTTP 5xx` 最多试 3 次（退避 0.5s、1s），若走了代理且 3 次全败再强制 `ProxyHandler({})` 真直连兜底 1 次（`4xx` 立刻失败不重不退）。拿到 `failed` 时先看 `webhook_error_detail` 再判断，别把 DNS、TLS、代理、连接任一直接当已证实根因。短超时包装器（`hc exec` 默认 60 秒/上限 600 秒、超时不够的 bash）会到点把 dispatch 连同子进程杀掉、留下 `phase=waiting` 台账——给不了足够超时就用上面的后台路径。

## 返回契约（每轮判读）

stdout 恰好一行 JSON，同一字符串写入 `$TMPDIR/loop/runs/<放行id>.result.json`（成功与 error 都写，逐字一致，是**最终交付**）。成功（退出码 0）：

```json
{"ok": true, "release_id": "<放行id>", "task_name": "loop-…", "target": "t7", "reset": "none", "turn": 3, "status": "idle",
 "complete": true, "complete_reason": [], "anchor_found": true, "elapsed_s": 690.5, "chars": 1834,
 "run_record": "$TMPDIR/loop/runs/<放行id>.md", "raw_record": "$TMPDIR/loop/runs/<放行id>.raw.md",
 "prompt_sha256": "…", "rewritten": {}, "extract_backend": "api", "webhook": "off",
 "needs_user": false, "questions": [], "conclusion": "…", "blocked": ""}
```

- `run_record`（`.md`）是抽取器从整屏**裁剪出的干净回复全文**（剔 logo/思考计数/工具痕迹/转圈行/空行/输入框/状态栏，逐字保留正文），是回看对象；`raw_record`（`.raw.md`）是整屏原料（逐字读回，制表符按终端制表位展开为空格，审计用）。error 时尚无干净全文，`run_record` 退回指整屏原料。
- `needs_user`（布尔）：回复里是否有需用户决定的事。三个选段字段是抽取器从原文**逐字摘出**的句子（语义见 `prompts/extract.md`）：`questions` 每条问题/审批一个元素（问题＋选项＋推荐整块）、`conclusion` 结论段（无则 `""`）、`blocked` 弹框原句（无则 `""`）。干净全文 `reply` 不进本 JSON，落在 `run_record`；要展示回复内容直接用 `run_record` 原文。
- `rewritten`：脚本对选段字段逐一做原文子串校验，搜不到的在此标出、值为最接近原文片段；`{}` 即全部逐字命中。**非空时对应字段是抽取器的改写，不可当原话引用；`reply` 被标时回看 `raw_record`。**
- `reset`：本轮前置类型（`none`/`new`/`clear`/`compact`）；`target_model`：仅传了 `--target-model` 才出现，本轮请求切换到的模型名（**请求值非验证值**，error 时也带，`error.model_sent` 标明切模型前置是否已发出）；`switch_agent`/`switch_form`：仅传了 `--switch-agent` 才出现（启动命令与终端形态 `shell`/`direct`，error 时也带）；`target_new`：仅 direct 分支换 kind 才出现，新终端地址——**调用方须把 `--target` 更新成它**（`target` 恒为传入的原地址，shell 分支地址不变、无此字段）；`extract_backend`：本轮抽取后端（`api`/`pi`，error 时也带）；`complete`/`complete_reason`：完整性判定，只看折叠标记（成功恒 `complete:true`）；`anchor_found`：提示词回声是否还在当前屏——仅信息，长任务回声滚出屏为 `false`，不影响 `complete`；`elapsed_s`：主任务发送成功到取回落定的秒数，不含前置与抽取；`webhook`：完成通知状态（鉴权走 `Authorization: Bearer`）；`webhook_error`：仅失败时出现，HTTP 状态码（如 `"HTTP 401"`）或异常类名（如 `"URLError"`）。
- `webhook_error_detail`：仅失败时出现，与 stderr 同一份脱敏诊断。字段：`type`（外层异常类名）、`reason_type`（`URLError.reason` 是异常时的类名）、`errno`/`errno_name`（可用时，如 `61`/`ECONNREFUSED`；DNS 给 `EAI_*`）、`is_timeout`（布尔）、`ssl_verify_code`（`SSLCertVerificationError.verify_code`，可用时）、`http_status`（仅 HTTPError）、`proxy`（`{"configured": "yes|no|unknown", "matches": "yes|no|unknown", "source": "env|system|bypass|none|unknown"}`：是否配置代理、本请求是否命中、代理来自环境变量还是系统配置）、`attempts`（总尝试次数），走过代理全败直连兜底仍失败时另带 `fallback_direct: true` 与 `fallback_error`（直连尝试的类名或 `"HTTP <码>"`；`webhook_error` 与主体字段保留主路由最后一次错误以对齐 `proxy`）。不含 URL、密钥、主机名、请求头或字符串形式的 reason；判断不了就标 `unknown`。
- **三个选段只摘原文片段、不保证覆盖全文**；要不要批准、要不要改接口的判断，必须回看 `run_record`，不能只凭 JSON 选段。

拿到结果整理成**选项卡**交给用户，原文不直接转给用户。失败（退出码 1；参数错误 2）返回 `{"ok": false, "release_id": "<放行id>", "target": "<终端地址>", "error": {"kind", "message", "hints"}}`（已算出 `task_name` 时一并带上）；kind 含义与调用方动作见 `REFERENCE.md`。监听只读随时可；仅报错时可直推（仍须放行）。

## 本地记录（默认 `$TMPDIR/loop`，随 OS 临时清理）

- `releases/<放行id>.txt`：放行提示词（调用方写，dispatch 原文照发）。
- `runs/<放行id>.raw.md`：整屏原料（审计）。
- `runs/<放行id>.md`：干净回复全文（回看对象 `run_record`）。
- `runs/<放行id>.json`：台账，先记账后发送；一个放行只跑一轮，至多发送成功三次（重置＋切模型前置＋主任务各一次），台账存在即拒同 id 重发。
- `runs/<放行id>.result.json`：最终交付，后台调用靠读它收结果；重发拒绝仍以台账为准。

记录与台账都随 OS 临时清理、不持久：事后审计与拒重发保护止于重启（放行 id 每次唯一，跨重启复用风险低）。要长期留存某轮，自行另存。

## 护栏

调用方要守的两条：**先放行，后调用**；**复用旧会话（`turn > 0`）调会话起始 skill 必须 `--reset`**（见「会话起始 skill」，`turn == 0` 全新空会话自动豁免，其余由脚本对行首调用机械兜底）。其余机械保证由脚本兜底，细节见 `REFERENCE.md`。