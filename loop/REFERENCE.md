# loop REFERENCE（按需查阅）

SKILL.md 的披露参考：只在对应分支才读。error kind 表在拿到 error 时查；机械保证是脚本兜底细节；发布前检查在把 `loop/` 公开前跑。用词见根目录 `GLOSSARY.md`。

## error 返回契约

失败退出码 1；参数/配置类错误退出码 2（argparse 报错无 JSON；`reset_required`、`launch_required`、`display_name_required`、`bad_args`、`api_not_configured`、`bad_extract_backend`、`bad_api_max_tokens` 是预检拒发，退出码 2、有 JSON）。stdout 一行、同一字符串写 `.result.json`：

```json
{"ok": false, "release_id": "…", "target": "…", "error": {"kind": "…", "message": "…", "hints": ["…"]}}
```

`release_id` 与 `target` 必带；在放行 id 校验后算出 `task_name` 的，也一并带顶层 `task_name`（含发给 webhook 的同一份 JSON）。发送后失败的 error 另带 `elapsed_s`；原文已落盘的另带 `run_record`。用了 `--reset` 的 error 另带 `reset`（类型）与 `error.reset_sent`（前置是否已发出，标明「前置已发／主任务未发」这类部分状态）；用了 `--target-model` 的 error 另带 `target_model`（请求值非验证值）与 `error.model_sent`（切模型前置是否已发出）；两个前置都用时 `reset_sent`/`model_sent` 一起标明部分状态。命名会话部分状态（ADR-0009）适用即带：`target_address`（解析 t 号）、`launched`（本轮已建终端）、`launch_sent`（启动命令已发）、`rebuilt`/`rebuilt_old_target`（已 kill 占位者＋旧 t 号）、`workspace`（`adopted`/`failed`）、`adopt_reason`（`app_closed`/`keep_error`/`no_pane`）、`space_created`/`pane_adopted`（无 space 清理舞中途失败时如实报）、`reset_skipped`；显示名（ADR-0010）适用即带：`display_name`（本轮传入显示名，尝试过 rename 就带、成功与 error 都带）、`display_name_warning`（rename 失败告警，reason：`rename_error`/`rename_mismatch`/`no_tab`）。放行 id 本身非法（`bad_release_id`）或参数错误的 error 只有 stdout（及配置了时的 webhook）——没有可安全落盘的文件名。

## error kind 表

| kind | 含义 | 调用方动作 |
| --- | --- | --- |
| `python_too_old` | 解释器低于 3.10 且 uv 自举失败（uv 探不到或重 exec 失败） | 装 3.10+ 解释器，或装 uv（脚本自动经 `uv run --no-python-downloads` 重跑） |
| `duplicate_release` | 该放行 id 已有台账/落盘 | 查台账；重发须用户重新放行、新 id |
| `reset_required` | 提示词行首调用会话起始 skill（wayfinder/grill-with-docs/implement/implement-spec/prototype）、`--reset=none` 且目标 `turn != 0`（或缺失）；在 `hc get`＋身份检查之后、写台账前拒发，零发送（ADR-0004） | 补 `--reset new/clear/compact`（agy/pi 认 `new`，claude 认 `clear`，codex 认 `new`/`clear`）用**同一放行 id** 重试（id 不消耗）；清单与边界见 SKILL.md「会话起始 skill」 |
| `bad_args` | 静态旗标错误（ADR-0009）：`--launch`/`--cwd` 单传（须成对）；`--rebuild` 在非占位分流误传；创建时 `--target` 不是合法会话名（1–32 位 a-z0-9-_，t 号保留）；`--display-name` 非法（空、超 64 字符、含换行或控制字符，ADR-0010）。写台账前拒发（退出码 2，id 不消耗） | 改正旗标用同一放行 id 重试；`--target` 推荐传会话名（`<kind>-<repo>` 或 bot slug） |
| `launch_required` | 目标 not_found/finished/空闲 shell，需创建/复活却没传 `--launch`（＋`--cwd`）；写台账前拒发，零发送（退出码 2，id 不消耗，与 `reset_required` 同构，ADR-0009） | 补 `--launch <shell 启动命令> --cwd <目录>` 用**同一放行 id** 重试；创建参数须随放行一并贴出（SKILL.md） |
| `display_name_required` | 创建/复活/重建新鲜路径缺 `--display-name`；写台账前拒发，零发送（退出码 2，id 不消耗，与 `launch_required` 同构，ADR-0010） | 补 `--display-name <显示名>` 用**同一放行 id** 重试；显示名须随放行一并贴出（SKILL.md） |
| `session_occupied` | 同名 live 终端被别的 agent/程序占用或无 loop 标签（ADR-0009）；message 呈报占位者地址/前台/turn/状态，不建不 kill、零发送、id 不消耗 | 呈报占位者给用户；确需重建经用户显式授权后带 `--rebuild`，或换 `--target` 到另一命名会话 |
| `rebuild_busy` | `--rebuild` 但占位者状态是 working/blocked/未自报，不是 idle/done，拒杀（ADR-0009）；零 kill、id 不消耗 | 等占位者落定或请用户到屏幕前处理；在跑的工作与等人决定的弹框绝不被杀 |
| `adopt_failed` | 领养失败，恒可见不变量破缺（ADR-0009）：`adopt_reason` 为 `app_closed`（herdr-app 没开/不可达）/`keep_error`（space.new/keep/清理写步失败）/`no_pane`（keep 后轮询窗口耗尽仍无 pane，#40）。**创建路径**：保留已建终端、`launched:true`/`target_address`/`workspace:"failed"`、放行 id 消耗；**既有路径预检**：零发送、id 不消耗 | 创建路径：开 app 后**新放行 id** 重试走既有路径补领养修复；预检路径：开 app 后**同 id** 重试。部分状态（空间已建/pane 已落地）如实报 |
| `launch_failed` | 创建/复活失败（ADR-0009）：hc run 失败（含 `name_taken` 并发竞态映射）、打标签失败、启动命令发送失败、等 shell/等 agent 超时或终端死亡；带部分状态（`launched`/`launch_sent`/`rebuilt`＋旧 t 号） | 告知用户；经放行后另起一轮。`name_taken`＝同名 live 终端存在，不建不 kill |
| `api_not_configured` / `bad_extract_backend` / `bad_api_max_tokens` | 抽取后端启动预检不过（ADR-0005）：backend=api 缺 `LOOP_API_BASE/KEY/MODEL`；`LOOP_EXTRACT_BACKEND` 值非法；`LOOP_API_MAX_TOKENS` 非正整数。未发送 | 配齐/改正 env 用同一放行 id 重试，或设 `LOOP_EXTRACT_BACKEND=pi`；见 SKILL.md「抽取后端」 |
| `not_idle` / `identity_mismatch` | 预检不过，未发送 | 整理成选项卡告知用户 |
| `send_refused` | 前置或主任务 hc 拒发且 done 补发不适用或仍失败；看 `reset_sent`/`model_sent` 判断哪些前置已发 | 同上 |
| `dialog_blocked` | 目标停在弹框（审批/提问/认证） | 告知用户去目标屏幕前处理；不按键、不代答 |
| `timeout` | 墙钟到点（前置或主任务），目标仍未落定 | 告知用户；前置超时则主任务未发、原文未取回 |
| `not_started` / `target_exited` / `target_error` | 发出后未开工 / 程序退出 / 自报 error | 告知用户 |
| `incomplete_result` | 折叠标记命中（▲▼ 藏了内容，当前屏看不到完整回复） | 原文已落盘，请用户回看 `run_record` |
| `extraction_rewritten` | 抽取字段全部未通过逐字校验 | 原文已落盘，请用户回看；可人工重做抽取 |
| `extraction_failed` / `extraction_invalid` / `tool_timeout` | 抽取失败或子进程超护栏 | 原文已落盘，可人工重做抽取 |

完整性硬判据只剩 `fold_markers`（回读含 ▲ 或 ▼，目标 TUI 折叠了内容，当前屏看不到完整回复）：命中即落盘后返回 `error(kind=incomplete_result)`，不进抽取。锚点（提示词第一个非空行前 24 字符）降级为信息字段 `anchor_found`：完整性检查在 turn>基线＋落定之后，本轮确已被处理，长任务提示词回声滚出当前屏（`--lines 0`）是常态、回复仍在屏底，故锚点搜不到不再阻断（PAW 实测多次误判后降级）。hc read 自己的 `complete` 字段只记台账、不作判据。

监听（`hc get`/`hc read` 只读）随时可；直推（绕过 dispatch 主动发）仅在报错时，且内容仍须用户放行。正常回复一律走管道。

## 机械保证（脚本兜底，调用方无需自查）

原文照发、一个放行至多发送成功三次（重置或启动前置＋切模型前置＋主任务，各一次；新鲜会话跳过重置）、永不 `--force`、不碰 `hc keys`、身份预检（`--expect-proc` 匹配 foreground.argv[0]/status.app 任一；foreground.name 不可靠——claude 实测为版本号，不作判据）、sha256 记账并随成功输出回报、不完整不进抽取、改写字段标 `rewritten`。重置前置按枚举校验、与主任务共用同一发送门，任一步发不出即整轮停。

切模型前置（`--target-model`，ADR-0006）：重置（如有）之后、主任务之前发字面 `/model <名字>`，等落定（idle/done＋屏幕静置，复用重置的落定机械）再发主任务。名字原样透传：脚本不认识模型、不按 agent 映射、不核实生效、不预读跳过；与 `--reset` 独立可单独带，同传顺序固定 重置→切模型→主任务。`target_model` 是请求值非验证值（仅传了才出现在结果，成功与 error 都带）；前置不抽取、不落 run_record，任一步发不出即整轮停（`error.model_sent` 标明部分状态）。会话起始 skill 纪律不变：`turn > 0` 且 `--reset=none` 仍拒发 `reset_required`，切模型不能替代重置；`turn == 0` 豁免时切模型前置照跑。agent 支持对照见 SKILL.md「调用」（codex 别传：裸 `/model` 开选择器会 blocked 停轮）。

命名会话 get-or-create（ADR-0009，supersede ADR-0007）：`--target` 推荐传会话名（一名一 live，herdr 原生保证；名字恒解析到最新 live 终端），result `target` 恒为传入地址、另报 `target_address`（解析 t 号）。预检分流一律 `hc get`（不用 `hc ls` 的 `agent` 派生字段）：**not_found/finished→创建**（finished 同名重建、旧条目按 t 号留审计）；**live＋前台 agent 身份符＋loop 标签符→既有路径**（核 pane/补领养→idle 核对→发送）；**live＋空闲 shell（前台 shell＋status null）＋loop 标签符→复活**（同样过预检 pane 闸）；**live＋别的 agent/程序（含标签符但前台身份不符）或无 loop 标签→`session_occupied`** 停轮呈报（地址/前台/turn/状态），`--rebuild` 走重建。身份核对（argv[0]/status.app）在分流时做。标签绑定：创建成功即打 `loop=1`＋`loop-agent=<expect-proc>`（无 `--expect-proc` 时只打 `loop=1`）；无标签同名终端拒收编。创建序列（单形态 shell 包装，无直接起 agent 分支）：`hc run --bg --keep --name <名字> --cwd <目录> -- zsh -l` → 打标签 → 等 shell 就绪 → 裸 send 启动命令（shell 不报 idle，不带 `--if`）→ 等 agent 就绪（前台非 shell 且 idle/done）→ 核身份 → 领养 → 主任务。复活序列：预检 pane 闸（写台账前，与既有路径同级）→ 不 run、不重写标签，send 启动命令 → 等就绪 → 核身份 → 主任务（名字/cwd/终端级历史不动）。`--launch` 永远只收 shell 命令字符串、字面透传、零映射；herdr 模板退居调用方持久配置仓库，loop 不感知模板、不自建配置文件。`--launch`/`--cwd` 成对必填无默认；需创建/复活缺参→`launch_required`（退出码 2、零发送、id 不消耗、补参同 id 重试）。强制重建：`--rebuild` 仅占位分支生效（其余分流误传 `bad_args`），只杀 idle/done（working/blocked/未自报→`rebuild_busy`），kill 后同名新建，台账/result 记 `rebuilt`＋旧 t 号。并发创建竞态由 herdr `name_taken` 仲裁、映射 `launch_failed`，脚本不做第二套一名一 live 实现。换 kind＝换 `--target` 到另一命名会话；`--switch-agent`/`--exit-command`/`--exit-keys` 已整条移除。**turn 是终端级累计计数**（status 每次离开 working +1、无清零；复活会话 turn>0 但会话空），修正 ADR-0007「hc 按前台进程追 turn」。

恒可见／领养（ADR-0009）：命名会话恒有 herdr-app pane，进不了即整轮 `adopt_failed` 报错、不降级 headless。所有 herdr-app 调用经单一 `app_json` 函数（镜像 `hc_json` 形状：子进程＋JSON＋超时＋错误映射，可注入桩点）；app 存活探测用 `herdr-app status` 退出码，**绝不用裸 `herdr-app`**（会打开窗口）。序列：status 探测 → 只读 `ls` 核 pane → 缺则 `do terminal.keep`（参数仅 terminal，无 space 参数，落点随 herdr-app 规则、不可控，已接受）→ **带超时轮询** `ls` 复核 pane 落地（#40：keep 后台异步落地，单次 ls 会跑在落地前误判 `no_pane`；窗口内反复只读 ls，pane 出现即成功、窗口耗尽才报 `no_pane`；窗口 `--adopt-timeout` 默认 30s、间隔 `--adopt-poll` 默认 2s；keep 真失败仍窗口耗尽后如实报）。本机无 space：`space.new {"name":<会话名>,"folder":<cwd>,"focus":false}` → keep → 复核 → `tab.close confirmed:true` 关赠送 shell 标签＋`hc rm` 该 shell 终端；任一步失败 `adopt_failed` 带部分状态（空间可能已建、pane 可能已落地，如实报）。**创建路径**领养在写台账后：失败保留已建终端、`launched:true`/`target_address`/`workspace:"failed"`＋原因、放行 id 消耗，开 app 后新 id 重试走既有路径修复；**既有路径**每轮预检（写台账前）核 pane、缺则补领养（park 等漂移自愈），失败零发送、id 不消耗、同 id 重试（与 `not_idle` 同级）。app 没开时所有轮次停摆（已接受的代价）；关 tab＝杀终端，走 finished→重建。

显示名层（ADR-0010）：`--display-name` 调用方显式传参，脚本不派生、不拼接、不理解，只机械校验（非空、1–64 字符、禁换行控制字符，非法 `bad_args`）＋落地。新鲜路径（创建/复活/重建）必传，缺则 `display_name_required`；复用轮可选，传了就覆盖更新、不传不动（无记账比对，放行授权名字本身）。落地时序：领养复核 pane 落地后、前置/主任务发送前，`find_tab_id` 拿 tab id → `tab.rename` → `ls` 复核 `tab.name == 显示名`（命令成功不算数）；台账 `naming` phase。**失败降级告警不丢放行**：rename 失败/复核不符/无 tab → result `display_name_warning`（reason：`rename_error`/`rename_mismatch`/`no_tab`），主流程继续，选项卡如实呈报；pane 落地仍是硬闸（`adopt_failed` 不变）。result `display_name` 回显本轮传入值（成功与 error 都带，未传则无）。**不写 summary 标签**：机器可读靠自动跟随 tab 名的 `where` 标签；`space.new` 的 name 维持会话名。

预检顺序：`duplicate_release` → 分流（`hc get`，含身份核对）→ `launch_required` → `display_name_required` → 核 pane/补领养（既有/复活路径，写台账前）→ 会话起始 skill 纪律（创建/复活/重建视同 turn 0 豁免）→ 写台账 → 前置 → 主任务。新鲜豁免：刚创建/复活/重建的会话自动跳过重置发送（result `reset_skipped:"fresh_launch"`），切模型前置照跑（ADR-0006 纪律平移）；启动命令是命令不是提示词：不抽取、不落 run_record，占前置发送槽，一轮至多发送成功三次不变。台账新增 launch/adopt/rebuild/naming 各 phase（等 shell、发启动、等 agent、领养中、重建、设显示名各步），失败 error 带部分状态。

会话起始 skill 纪律（ADR-0004）：提示词**行首**出现 `/wayfinder`、`/grill-with-docs`、`/implement`、`/implement-spec`、`/prototype` 之一（允许行前空白；命令后须非词字符且非连字符，`/implementing`、`/implement-foo` 不误中，`/wayfinder#7` 算调用；`implement-spec` 优先报长名）且 `--reset=none` 时，在 `hc get`＋身份预检之后、写台账与任何发送之前校验目标 `turn`：`turn == 0`（精确整数 `0`，尚未跑过任何轮次的全新空会话）直接豁免放行；`turn > 0` 或缺失时拒发（`reset_required`，退出码 2，message 带 skill 名、行号与当前 `turn` 值）。分流判定创建/复活/重建的新鲜会话视同 turn 0 豁免、不看终端 turn（turn 是终端级累计，复活会话 turn>0 但会话空，ADR-0009）。行文中提及不触发；放行 id 不消耗，补 `--reset` 同 id 可重试。`new`/`clear`/`compact` 都算重置。「调用独占一行」是 SKILL.md 的文字纪律，机械检查只兜行首这一层；`not_idle`/`identity_mismatch` 先于 `reset_required`。

抽取后端（ADR-0005）：`LOOP_EXTRACT_BACKEND=api|pi`，默认 `api`。`api` 走 OpenAI 兼容 chat completions：`LOOP_API_BASE`（到 `/v1`，容忍尾斜杠，脚本拼 `/chat/completions`）、`LOOP_API_KEY`（Bearer）、`LOOP_API_MODEL` 全必填无默认，启动预检缺失即 `api_not_configured`（只列变量名）；`LOOP_API_MAX_TOKENS` 可选、默认 32768、非正整数启动即 `bad_api_max_tokens`。请求 system=extract.md、user=指令＋内联整屏原文，temp 0、不流式、不重试、不发 response_format，超时复用 `--extract-timeout`；代理决议与出口和 webhook 同源（ADR-0003）。失败只记状态码/类名（HTTP → `extraction_failed` 带 `HTTP <码>`、不读响应体；超时 → `tool_timeout`；其它网络异常 → `extraction_failed` 带类名），密钥/URL 零回显。`pi` 备选路径照旧（@文件、`--pi-model`），`api` 路径不找 pi、不依赖 node。result 与台账 params 带 `extract_backend`（error 也带）。

发送门：首发只 `--if idle`；被拒（`status_mismatch`，零字节进入，不算重发）后 `hc get` 复核确认 `done`，补发前再核一次非 working/blocked，才不带 `--if` 补发一次；仍失败即 error。

抽取逐字校验：选段字段逐一做原文子串校验（含去空白折叠），搜不到标 `rewritten` 附最接近片段，全部搜不到则 `error(extraction_rewritten)`；`reply` 干净全文先整体、再逐行校验，未过逐字校验的行标 `rewritten["reply"]`。`needs_user`（布尔判断）与 `next_command`（推测，ADR-0008）不参与校验。

下一步预测（ADR-0008）：抽取输出第六字段 `next_command`（字符串）＝调用方下一轮该发的**完整斜杠指令**（含参数，参数取自原文上下文，如 `/implement pop-xxxx.1`）；`""`＝无预测（下一步是用户答复/自由操作/看不出）。候选集为七个打法 skill：wayfinder/grill-with-docs/to-spec/to-tickets/implement/implement-spec/prototype。唯一推测字段：extract.md「只依据原文」规则为它开显式例外，豁免逐字校验、不进 `rewritten`；缺失或非字符串按 `""` 投影，不整轮报错，形状门不检查它。仅成功结果带（error 路径不进抽取）；随同一份 JSON 进台账与 webhook。**建议不是闸**：发送路径零改动、无新 error kind；反向约束落在 SKILL 纪律——判读时呈给用户，同 target 下一轮放行应照用预测指令作开场行（可编辑，偏离明示理由），预测是起始 skill 且 turn>0 时同次放行一并推荐 `--reset`。

可选 webhook：env `LOOP_WEBHOOK_URL`＋`LOOP_WEBHOOK_SECRET` 都配才发，成功/报错都 POST 结果 JSON，鉴权走 `Authorization: Bearer <密钥>`，不进日志、不进 stdout，单次 ~10 秒超时；发失败不连累整轮，result 记 `webhook: off/sent/failed`，失败另记 `webhook_error`（HTTP 状态码如 `"HTTP 401"`，或异常类名如 `"URLError"`）与 `webhook_error_detail`（脱敏诊断，stderr 打印同一份），结果文件仍是事实源。

webhook 出网默认跟随本机系统代理（见 ADR-0003）：标准 `http_proxy`/`https_proxy` 显式配置时优先（source=env）；否则读系统代理（macOS SystemConfiguration，source=system）；系统例外表或 `no_proxy` 命中则直连（source=bypass）；都没配是 none；探测失败为 unknown。不能用 `getproxies()` 直接判——只设了 `no_proxy` 或非标准 `*_proxy`（如 `JEV_UPSTREAM_PROXY`）时它也返回非空字典，却没有任何 http/https 键，urllib 实际会直连。选定出口对传输层异常与 `HTTP 5xx` 最多试 3 次（首发＋2 次重试，退避 `0.5s`、`1.0s`）；若走了代理且 3 次全败，再传 `ProxyHandler({})` 强制跳过 env/系统代理真直连兜底 1 次（`4xx` 等非 5xx 状态码立刻失败、不重试也不兜底）。该策略只作用于 webhook POST，不改进程全局代理、不关证书校验。

`webhook_error_detail` 只收类名、数字、布尔和三态字符串，不收 `str(e)`/`repr(e)`/响应体/字符串 reason，避免 URL、密钥、主机名、请求头回显。字段：

| 字段 | 含义 |
| --- | --- |
| `type` | 外层异常类名（如 `URLError`、`HTTPError`、`TimeoutError`；以主路由最后一次尝试为准以对齐 `proxy`） |
| `reason_type` | `URLError.reason` 是异常时的类名（如 `ConnectionRefusedError`、`gaierror`、`SSLCertVerificationError`）；reason 是字符串时不出现 |
| `errno` / `errno_name` | 可用的 errno 与名字（如 `61`/`ECONNREFUSED`；DNS 给 `EAI_NONAME`/`EAI_AGAIN`） |
| `is_timeout` | 是否超时（外层或 reason 为 `TimeoutError`） |
| `ssl_verify_code` | `SSLCertVerificationError.verify_code`（如 `18` 自签、`20` 缺中间证书；含义以 OpenSSL 为准） |
| `http_status` | 仅 `HTTPError`：HTTP 状态码 |
| `proxy.configured` / `proxy.matches` / `proxy.source` | 是否配置了代理 / 本请求是否实际命中 / 代理来源（`env`/`system`/`bypass`/`none`/`unknown`）；不输出代理地址或凭据 |
| `attempts` | 本轮 webhook 总尝试次数（`4xx` 为 `1`；直连无代理全败为 `3`；走代理＋直连兜底全败为 `4`） |
| `fallback_direct` / `fallback_error` | 仅当走了代理且 3 次全败后执行直连兜底仍失败时出现：`fallback_direct: true`，`fallback_error` 为直连那次的 `"HTTP <码>"` 或异常类名 |

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