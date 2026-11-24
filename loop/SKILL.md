---
name: loop
description: 走管道：每轮要回复执行者的信息都经 loop 推回——把用户放行的提示词发进 herdr 目标会话并取回结果（裁剪＋抽取成 JSON，默认直连 API、pi 备选）；监听只读随时可，仅报错时可直推（仍须放行）。命名会话 get-or-create（ADR-0009）：--target 推荐传会话名，目标缺失/已死/空闲 shell 时同一放行内创建或复活（--launch shell 启动命令＋--cwd 成对必填，--display-name 新鲜路径必传，缺参 launch_required/display_name_required 补参同 id 重试）；名字被占呈报 session_occupied，--rebuild 经用户显式授权只杀 idle/done 占位者；命名会话恒可见于 herdr-app 工作区（领养失败 adopt_failed 停轮，herdr-app 必须常开）；换 kind 换 --target 到另一命名会话。可选 --reset 先发 /new /clear /compact；可选 --target-model 先发 /model <名字> 切目标模型（独立于 --reset，顺序 重置→切模型→主任务）；调会话起始 skill（wayfinder/grill-with-docs/implement/implement-spec/prototype）时，全新空会话（turn == 0）或刚创建/复活的新鲜会话自动豁免，复用旧会话（turn > 0）必须带 --reset。手里有放行 id、提示词文件、目标地址，要发送并取回新结果时用。
---

# loop：走管道

**走管道**：每一轮要发给执行者的回复，都经本 skill 的 `scripts/dispatch.py` 推回。和用户讨论定稿后，写放行文件、调 dispatch 发送＋取回、判读结果、再讨论——下一轮回复仍走管道。监听（`hc get`/`hc read` 只读）随时可；仅当 dispatch 报错才可直推，直推内容仍须用户放行。

dispatch 做发送、等待、取回、落盘这些机械活，抽取器（默认直连 API，pi 备选，见「抽取后端」）读整屏原料做裁剪＋抽取；异常由脚本直接返回 error，不进抽取。架构见 `docs/adr/0002`、`docs/adr/0005`，用词见根目录 `GLOSSARY.md`；error kind 表、机械保证、发布前检查见 `REFERENCE.md`。

## 每轮循环（走管道）

1. **讨论定稿**：和用户定出要回复执行者的全文。同 target 上一轮结果的 `next_command` 非空时，本轮放行开场行**应照用该预测指令**（可编辑；偏离须向用户明示理由，ADR-0008）。`--target` 推荐传**会话名**（一名一 live，名字恒解析到最新 live 终端，不必追踪 t 号漂移）；目标可能不存在/已死/空闲 shell（首轮、agent 退出后）就备好 `--launch`（shell 启动命令）＋`--cwd`，**讨放行时把创建参数（会话名、启动命令、cwd、显示名）随全文一并贴出**，一声「发」授权创建＋主任务，绝无静默创建；需强制重建时贴「将 kill <名字>（占位者摘要）并重建」，用户显式授权后才传 `--rebuild`。若准备调用会话起始 skill，先 `hc get` 看一眼目标 `turn`：`turn == 0`（全新空会话）或本轮将创建/复活（新鲜会话视同 turn 0）不用带 `--reset`；`turn > 0`（复用旧会话）就在这一步向用户讨放行时**一并给出推荐的 `--reset` 方式**，一次放行授权完，首调直带 `--reset`，别先拿 `none` 去撞 `reset_required` 再回来二轮请示。本轮若要切目标模型，也在这一步一并确认 `--target-model` 与模型名（见「调用」）。完成判据：用户对全文（及需要时的创建参数、重置方式、模型名）说「发」，拿到唯一放行 id（对内容方向说「行」不算放行）。
2. **写放行文件**：全文写成 UTF-8 文件，放 `$TMPDIR/loop/releases/<放行id>.txt`（目录不存在就建），放行后一字未改。完成判据：文件就绪（dispatch 核对 sha256，改动即拒发）。
3. **走管道推回**：调 dispatch 发送＋取回（见「调用」「交付」）。完成判据：dispatch 返回、`.result.json` 生成。用 `--reset`/`--target-model` 时该放行一并授权全部前置＋主任务（重置是枚举里可预知的固定命令，切模型是字面 `/model <名字>`）。
4. **判读结果**：读 `.result.json`＋`run_record`，整理成选项卡给用户；`next_command` 非空时一并呈出（它是预测不是事实，参数可能有错，下一轮放行前用户会审）。完成判据：用户看到结果、给出下一轮方向。
5. 回到 1，下一轮回复仍走管道。

## 调用前

本机装有 `hc`、`uv`、`herdr-app`（恒可见不变量的硬依赖，**窗口必须常开**，ADR-0009）与 Python ≥ 3.10 的解释器（脚本要求 `>=3.10`）；抽取默认直连 API，需 `LOOP_API_BASE/KEY/MODEL` env（见「抽取后端」），选 pi 备选才需要 `pi`。工具先按 PATH 找，PATH 没有时脚本自动探 `~/.local/bin`、homebrew、nvm node bin——后台 job 缺 PATH 也能起；探不到才报 `missing_tool`。PATH 由脚本兜底，**不需手写 export PATH 包装脚本**；后台直接用调用方自己的 bg 机制（见「交付」）。

## 调用

```bash
python3 scripts/dispatch.py \
  --target <会话名（推荐）或t号> --release-id <放行id> --prompt-file <提示词文件> \
  --expect-proc <前台进程子串> \
  --launch "<shell 启动命令>" --cwd <工作目录>   # 首轮/目标可能缺失时带，get-or-create 兜底
```

- `python3` 直跑：脚本无第三方依赖、头部声明 `requires-python = ">=3.10"`。版本门 `>=3.10` 直跑；`<3.10` 自动 `find_tool` 探 uv 并经 `uv run --no-python-downloads` 用已有 3.10+ 重跑（不下载），uv 探不到才报 `error(kind=python_too_old)`。后台精简 PATH 里 python3 也在，uv 退居内部兜底，调用方不用管 PATH、不需手写包装。
- `--expect-proc`：身份预检，强烈建议传（grok 传 `grok`、claude 传 `claude`）；只认 foreground.argv[0]/status.app（OSC 7501 自报）任一，不符即拒发。**foreground.name 不可靠**（claude 实测为版本号），不作判据；标题易变，也不作判据。创建时该值写进 `loop-agent` 标签作身份绑定凭据。
- `--reset {none,new,clear,compact}`（默认 `none`）：主任务前的重置前置。选 `new`/`clear`/`compact` 就在同一 target 先 `--if idle` 发字面命令 `/new`/`/clear`/`/compact`，等落定（idle/done＋屏幕静置）再发主任务；`none` 即单 prompt，向后兼容。**发哪条由调用方按目标 agent 认哪个挑**（`agy`/antigravity、`pi` 认 `/new`＋`/compact`；`claude` 认 `/clear`＋`/compact`，不认 `/new`；`codex` 认 `/new`/`/clear`/`/compact`；`grok` 的 `/new`≡`/clear` 都认），脚本不按 agent 映射。前置不抽取、不落 run_record；前置或主任务任一步发不出即整轮停，error 带 `reset_sent` 标明部分状态。`/clear` 是本地命令、可能不进 working，前置落定用屏幕静置而非 turn 判据。**提示词行首调用会话起始 skill 时：`turn == 0`（全新空会话）可不传 `--reset` 直接发；`turn > 0` 或未知时必须带 `new`/`clear`/`compact`，`none` 会被脚本拒发**（见「会话起始 skill」）。
- `--target-model <名字>`（默认不传，ADR-0006）：切模型前置。重置（如有）之后、主任务之前，在同一 target 先 `--if idle` 发字面命令 `/model <名字>`，等落定（idle/done＋屏幕静置）再发主任务。**与 `--reset` 独立**：可单独传（任意一轮都能随下一条消息带模型切换），同传时顺序固定 **重置 → 切模型 → 主任务**。名字**原样透传**：脚本不认识模型、不按 agent 映射、不核实生效、不预读跳过；`target_model` 是请求值非验证值，名字对错归调用方纪律。**哪些 agent 认行内 `/model <名字>`**（已核实）：`agy`/antigravity 认 name/slug/label 并存为默认（已是该模型报 `Model already set`、名字不认打印有效列表，都不弹框；注意 `/model <名字> <提示词>` 是其一次性语义，多词值按字面发出可能触发，别传多词名字）；`claude` 认别名或完整模型 ID（如 `/model sonnet`）；`pi` 认 `/model [provider/model]`；`grok` 认 `/model <名字> [window] [effort]`；**`codex` 的裸 `/model` 开选择器、行内名字未实证——别对 codex 传 `--target-model`**（选择器 → blocked → `dialog_blocked` 停轮），换 codex 模型用启动旗标 `-m` 重开会话。前置不抽取、不落 run_record；任一步发不出即整轮停，error 带 `model_sent` 标明部分状态。会话起始 skill 纪律不变：`turn > 0` 且 `--reset=none` 仍拒发 `reset_required`，切模型不能替代重置。
- `--launch <shell 启动命令>`＋`--cwd <目录>`（ADR-0009，成对必填、无默认，单传报 `bad_args`）：命名会话 **get-or-create** 的创建参数。`--target` 推荐传会话名，预检 `hc get` 报错驱动分流：
  - **not_found / finished** → **创建**：`hc run --bg --keep --name <名字> --cwd <目录> -- zsh -l`（单形态 **shell 包装**：持久 shell 终端＋send 启动命令）→ 打 `loop=1`＋`loop-agent=<expect-proc>` 标签 → 等 shell 就绪 → 裸 send 启动命令 → 等 agent 就绪 → 核身份 → 领养 → 主任务。finished 旧条目按 t 号留审计，名字恒解析到最新 live。
  - **live＋空闲 shell（前台 shell＋status null）＋loop 标签符** → **复活**：不 run、不重写标签，原地重发启动命令；名字/cwd/终端级历史不动，会话是空的。复活同样过预检 pane 闸（app 没开照样停轮）。
  - **live＋别的 agent/程序，或无 loop 标签** → `session_occupied` 停轮呈报占位者（地址、前台、turn、状态），不建不 kill；别人的会话绝不被静默收编。身份核对（argv[0]/status.app）在分流时做：标签符但前台身份不符也算占位，`--rebuild` 可重建。
  - 需创建/复活却没传 `--launch` → `launch_required`（退出码 2、零发送、放行 id 不消耗），补参后**同 id 重试**即可。启动命令**字面透传**（如 `--launch "claude --dangerously-skip-permissions"`），脚本不映射 kind→命令、不感知 herdr 模板；启动命令是命令不是提示词：不抽取、不落 run_record，占前置发送槽。
  - **新鲜豁免**：刚创建/复活的会话自动跳过重置发送（result 记 `reset_skipped:"fresh_launch"`），会话起始 skill 豁免视同 turn 0、**不看终端 turn**（turn 是终端级累计，复活会话 turn>0 但会话空）；切模型前置照跑。
  - **恒可见（领养）**：自动创建的终端无条件进 herdr-app 工作区——`herdr-app status` 探测（绝不用裸 `herdr-app`，它会开窗）→ 只读 `ls` 核 pane → 缺则 `do terminal.keep` → `ls` 复核落地；本机无 space 先 `space.new`（focus:false 不抢视图）再清赠送 shell。进不了即 `adopt_failed` 整轮报错、不降级 headless：创建路径保留已建终端、放行 id 消耗（开 app 后**新 id** 重试走既有路径修复）；既有路径每轮预检核 pane、缺则补领养，失败零发送、**同 id** 重试。**herdr-app 必须常开**（app 没开时所有轮次停摆，已接受的代价）；关 tab＝杀终端，走 finished→重建。
- `--display-name <显示名>`（ADR-0010）：命名会话标签页上给人看的名字。**创建/复活/重建三条新鲜路径必传**（缺则 `display_name_required`，退出码 2、零发送、id 不消耗、补参同 id 重试，与 `launch_required` 同构）；复用轮可选——传了就更新 tab 名、不传保持旧值。机械校验：非空、1–64 字符、禁换行与控制字符（非法 `bad_args`）；**不校验式样**。落地：领养复核 pane 落地后、主任务发送前 `tab.rename`＋`ls` 复核名；**失败不丢放行**，result 记 `display_name_warning`，选项卡如实呈报。**传了就覆盖**（放行已授权名字本身）；复用轮传显示名前，调用方若知用户手工改过名，先向用户确认。
- `--rebuild`（默认不传，ADR-0009）：**强制重建**，仅用户明示授权 kill 重建后传，只在 `session_occupied` 占位分支生效（其余分流误传 `bad_args`）。只杀 idle/done 占位者，working/blocked/未自报 → `rebuild_busy` 拒杀；kill 后同名新建，result `rebuilt:true`＋旧 t 号，kill 后创建失败也带部分状态可审计。放行必贴「将 kill <名字>（占位者摘要）并重建」。
- **换 kind＝换 `--target`** 到另一命名会话（get-or-create 兜底），旧会话保留可切回；`--expect-proc` 同步改新 kind。`--switch-agent`/`--exit-command`/`--exit-keys` 已整条移除（ADR-0007 superseded by ADR-0009）。
- **命名约定**（推荐，不写死进脚本）：`<kind>-<repo>`（如 `claude-matt-loop`）或 bot 名/会话 id 类 slug，1–32 位 a-z0-9-_。**herdr 模板退居调用方侧持久配置仓库**：用时把启动命令抄出来字面传 `--launch`，loop 不感知模板、不自建配置文件。
- **显示名约定**（推荐，不写死进脚本，ADR-0010）：`<bot 族>_<任务描述>`（如 `LoopEng_命名层设计`）、可中文，任务描述用短语（GUI 约 170px 截断、十几个汉字）。bot 族前缀表示「谁派的活」——GUI 本就能看到哪个 agent 在跑，kind 前缀冗余。**撞名警示**：显示名恰等于程序名（如 `claude`）会被 tabs mod 当自动名、继续跟随 live title；`<bot 族>_` 前缀天然避开。显示名不是会话名：寻址/复用仍用会话名。
- 等待与抽取默认值一般不用动：墙钟 3600 秒（主任务发送成功起计）、check-in 180 秒、落定静置 10 秒 × 2 次、抽取护栏 1200 秒（`--wall-clock` / `--check-in` / `--settle-seconds` / `--settle-checks` / `--extract-timeout` 可覆盖）。读取固定 `hc read --lines 0`。
- `--loop-dir` 默认 `$TMPDIR/loop`（随 OS 临时清理，记录不持久）；`--prompt-file` 传 `$TMPDIR/loop/releases/<放行id>.txt`；`--pi-model` 透传 `pi --model`（仅 pi 备选后端生效）。

## 会话起始 skill（工程纪律）

五个 skill 是**会话起始 skill**：`wayfinder`、`grill-with-docs`、`implement`、`implement-spec`、`prototype`。它们只能作为空会话的开场任务：若目标 `hc get` 的 `turn == 0`（尚未跑过任何轮次的全新空会话），脚本**自动豁免**，不带 `--reset` 直接发（省去空等重置）；**刚创建/复活/重建的新鲜会话视同 turn 0 豁免、不看终端 turn**（turn 是终端级累计计数，复活会话 turn>0 但会话是空的，ADR-0009）；若复用已有轮次的旧会话（`turn > 0` 或 `turn` 未知），则**必须带 `--reset new/clear/compact`** 先清目标执行者会话，且应在 Step 1 讨放行时一并把重置选项与推荐值带给用户确认；调用方自己的会话不动。出处是 `/ask-matt` 工作流（默认调用方已装 Matt 全套 skill）：连续性在工件里——地图 issue、GLOSSARY/ADR、票、handoff 文件——不在聊天里，清会话不丢东西。

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
{"ok": true, "release_id": "<放行id>", "task_name": "loop-…", "target": "claude-matt-loop", "target_address": "t7",
 "reset": "none", "turn": 3, "status": "idle",
 "complete": true, "complete_reason": [], "anchor_found": true, "elapsed_s": 690.5, "chars": 1834,
 "run_record": "$TMPDIR/loop/runs/<放行id>.md", "raw_record": "$TMPDIR/loop/runs/<放行id>.raw.md",
 "prompt_sha256": "…", "rewritten": {}, "extract_backend": "api", "webhook": "off",
 "needs_user": false, "questions": [], "conclusion": "…", "blocked": "", "next_command": "/to-spec"}
```

- `run_record`（`.md`）是抽取器从整屏**裁剪出的干净回复全文**（剔 logo/思考计数/工具痕迹/转圈行/空行/输入框/状态栏，逐字保留正文），是回看对象；`raw_record`（`.raw.md`）是整屏原料（逐字读回，制表符按终端制表位展开为空格，审计用）。error 时尚无干净全文，`run_record` 退回指整屏原料。
- `needs_user`（布尔）：回复里是否有需用户决定的事。三个选段字段是抽取器从原文**逐字摘出**的句子（语义见 `prompts/extract.md`）：`questions` 每条问题/审批一个元素（问题＋选项＋推荐整块）、`conclusion` 结论段（无则 `""`）、`blocked` 弹框原句（无则 `""`）。干净全文 `reply` 不进本 JSON，落在 `run_record`；要展示回复内容直接用 `run_record` 原文。
- `next_command`（字符串，ADR-0008）：抽取器**推测**的下一轮完整斜杠指令（七个打法 skill 候选，参数取自原文上下文）；`""`＝无预测。唯一推测字段，豁免逐字校验、缺失按 `""` 不连累整轮。**建议不是闸**：下一轮放行应照它作开场行（可编辑，偏离明示理由）；它是起始 skill 且 turn>0 时，讨放行一并推荐 `--reset`。
- `rewritten`：脚本对选段字段逐一做原文子串校验，搜不到的在此标出、值为最接近原文片段；`{}` 即全部逐字命中。**非空时对应字段是抽取器的改写，不可当原话引用；`reply` 被标时回看 `raw_record`。**
- `target` 恒为**传入地址**（会话名或 t 号，寻址稳定）；`target_address`：解析出的 **t 号**（审计定位，名字恒解析到最新 live）；`turn` 是**终端级累计计数**（复活会话 turn>0 但会话是空的，ADR-0009）。
- `launched`：仅本轮 `hc run` 创建了终端才出现（`true`，创建/重建路径，error 时也带）；`rebuilt`/`rebuilt_old_target`：仅强制重建才出现（`true`＋被 kill 占位者的旧 t 号）；`workspace`：领养状态（`adopted`/`failed`，创建与既有路径都报）；`reset_skipped:"fresh_launch"`：新鲜会话跳过了重置发送（传了 `--reset` 但创建/复活/重建时）。这些字段成功与 error 都带适用项，部分状态（终端已建/启动已发/旧会话已死/空间已建）如实报。
- `reset`：本轮前置类型（`none`/`new`/`clear`/`compact`）；`target_model`：仅传了 `--target-model` 才出现，本轮请求切换到的模型名（**请求值非验证值**，error 时也带，`error.model_sent` 标明切模型前置是否已发出）；`extract_backend`：本轮抽取后端（`api`/`pi`，error 时也带）；`complete`/`complete_reason`：完整性判定，只看折叠标记（成功恒 `complete:true`）；`anchor_found`：提示词回声是否还在当前屏——仅信息，长任务回声滚出屏为 `false`，不影响 `complete`；`elapsed_s`：主任务发送成功到取回落定的秒数，不含前置与抽取；`webhook`：完成通知状态（鉴权走 `Authorization: Bearer`）；`webhook_error`：仅失败时出现，HTTP 状态码（如 `"HTTP 401"`）或异常类名（如 `"URLError"`）。
- `webhook_error_detail`：仅失败时出现，与 stderr 同一份脱敏诊断。字段：`type`（外层异常类名）、`reason_type`（`URLError.reason` 是异常时的类名）、`errno`/`errno_name`（可用时，如 `61`/`ECONNREFUSED`；DNS 给 `EAI_*`）、`is_timeout`（布尔）、`ssl_verify_code`（`SSLCertVerificationError.verify_code`，可用时）、`http_status`（仅 HTTPError）、`proxy`（`{"configured": "yes|no|unknown", "matches": "yes|no|unknown", "source": "env|system|bypass|none|unknown"}`：是否配置代理、本请求是否命中、代理来自环境变量还是系统配置）、`attempts`（总尝试次数），走过代理全败直连兜底仍失败时另带 `fallback_direct: true` 与 `fallback_error`（直连尝试的类名或 `"HTTP <码>"`；`webhook_error` 与主体字段保留主路由最后一次错误以对齐 `proxy`）。不含 URL、密钥、主机名、请求头或字符串形式的 reason；判断不了就标 `unknown`。
- **三个选段只摘原文片段、不保证覆盖全文**；要不要批准、要不要改接口的判断，必须回看 `run_record`，不能只凭 JSON 选段。

拿到结果整理成**选项卡**交给用户，原文不直接转给用户。失败（退出码 1；参数错误 2）返回 `{"ok": false, "release_id": "<放行id>", "target": "<终端地址>", "error": {"kind", "message", "hints"}}`（已算出 `task_name` 时一并带上）；kind 含义与调用方动作见 `REFERENCE.md`。监听只读随时可；仅报错时可直推（仍须放行）。

## 本地记录（默认 `$TMPDIR/loop`，随 OS 临时清理）

- `releases/<放行id>.txt`：放行提示词（调用方写，dispatch 原文照发）。
- `runs/<放行id>.raw.md`：整屏原料（审计）。
- `runs/<放行id>.md`：干净回复全文（回看对象 `run_record`）。
- `runs/<放行id>.json`：台账，先记账后发送；一个放行只跑一轮，至多发送成功三次（重置或启动前置＋切模型前置＋主任务各一次，新鲜会话跳过重置），台账存在即拒同 id 重发。台账记创建/复活/领养/重建各 phase，失败带部分状态。
- `runs/<放行id>.result.json`：最终交付，后台调用靠读它收结果；重发拒绝仍以台账为准。

记录与台账都随 OS 临时清理、不持久：事后审计与拒重发保护止于重启（放行 id 每次唯一，跨重启复用风险低）。要长期留存某轮，自行另存。

## 护栏

调用方要守的三条：**先放行，后调用**；**复用旧会话（`turn > 0`）调会话起始 skill 必须 `--reset`**（见「会话起始 skill」，`turn == 0` 全新空会话与刚创建/复活的新鲜会话自动豁免，其余由脚本对行首调用机械兜底）；**创建参数（会话名、启动命令、cwd、显示名）与强制重建意图随放行一并贴出、一并授权**——kill 永远是用户的显式授权，不是脚本的自主动作。其余机械保证由脚本兜底，细节见 `REFERENCE.md`。