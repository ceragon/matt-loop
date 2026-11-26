# loop REFERENCE（按需查阅）

SKILL.md 的披露参考：只在对应分支才读。error kind 表在拿到 error 时查；机械保证是脚本兜底细节；发布前检查在把 `loop/` 公开前跑。用词见根目录 `GLOSSARY.md`。

> ADR-0015（会话命名约定）是纯约定＋文档改动：error kind 表、机械保证、result 字段、发布前检查零改动；会话名式样不进机械校验，违约兜底沿用既有 not_idle/session_occupied/reset_required。

## error 返回契约

失败退出码 1；参数/配置类错误退出码 2（argparse 报错无 JSON；`reset_required`、`split_required`、`launch_required`、`display_name_required`、`bad_args`、`api_not_configured`、`bad_extract_backend`、`bad_api_max_tokens` 是预检拒发，退出码 2、有 JSON）。stdout 一行、同一字符串写 `.result.json`（全量结果）；已建立安全运行路径的 error 另写 `.notify.json`（通知记录＝发送前完整 error 对象，不套成功白名单，ADR-0014）：

```json
{"ok": false, "release_id": "…", "target": "…", "error": {"kind": "…", "message": "…", "hints": ["…"]}}
```

`release_id` 与 `target` 必带；在放行 id 校验后算出 `task_name` 的，也一并带顶层 `task_name`（含发给 webhook 的同一份 JSON）。发送后失败的 error 另带 `elapsed_s`；原文已落盘的另带 `run_record`。用了 `--reset` 的 error 另带 `reset`（类型）与 `error.reset_sent`（前置是否已发出，标明「前置已发／主任务未发」这类部分状态）；用了 `--target-model` 的 error 另带 `target_model`（请求值非验证值）与 `error.model_sent`（切模型前置是否已发出）；两个前置都用时 `reset_sent`/`model_sent` 一起标明部分状态。命名会话部分状态（ADR-0009）适用即带：`target_address`（解析 t 号）、`launched`（本轮已建终端）、`launch_sent`（启动命令已发）、`rebuilt`/`rebuilt_old_target`（已 kill 占位者＋旧 t 号）、`workspace`（`adopted`/`failed`）、`adopt_reason`（`app_closed`/`keep_error`/`no_pane`/`space_not_found`/`space_ambiguous`/`select_error`）、`space_created`/`pane_adopted`（建空间与 pane 落地部分状态；#46 起 `pane_adopted` 在 keep 落地复核通过即报，不限建空间分支）、`gift_cleanup_warning`（赠送 shell 清理降级告警，#46，reason：`tab_close_error`/`finish_timeout`/`finish_poll_error`/`rm_error`；真终端已可见，整轮仍 adopted、不丢放行）、`reset_skipped`；显示名（ADR-0010）适用即带：`display_name`（本轮传入显示名，尝试过 rename 就带、成功与 error 都带）、`display_name_warning`（rename 失败告警，reason：`rename_error`/`rename_mismatch`/`no_tab`）；空间定位（ADR-0011，#43）适用即带：`space`（本轮传入 `--space` 请求值，必传、成功与 error 都带）、`space_selected`（解析出的目标空间 id，解析成功才带）、`space_landed`（pane 实际落进的空间 id，落地复核通过才带）、`space_target_warning`（pane 落进非目标空间告警，reason：`landed_elsewhere`，带 `actual`）、`space_restore`（还原选中结果：`restored`/`user_switched`/`not_needed`/`failed`）、`space_restore_warning`（还原失败告警，reason：`restore_error`/`restore_mismatch`）。放行 id 本身非法（`bad_release_id`）或参数错误的 error 只有 stdout（及配置了时的 webhook）——没有可安全落盘的文件名，也不构造通知记录（不用非法 id 拼路径，不虚构记录已存在，ADR-0014）。

中途本地 IO 失败（磁盘满、权限变更、目录被清理等）由顶层兜底映射 `io_failed`（ADR-0024，#111）：退出码恒 1，走与其他 error 完全相同的出口——尽力写终态台账（`error:<kind>`＋`failed_at`）、通知记录/结果文件各自降级、webhook 照尝试、stdout 照输出。hints 按台账是否已开（首版台账成功落盘）机械分两态：**未开**（runs 创建/首写失败）→放行 id 未消耗、环境修复后**同放行 id** 重试；**已开**→id 已消耗、部分状态如实带出、修复后**新放行 id**（同 id 重试撞 `duplicate_release`）。message 带操作（异常类名）＋路径＋`OSError` 原文。有现成语义的读取点不归 `io_failed`：发送前 sha 复核读提示词失败→`prompt_unreadable`、抽取提示词读失败→`missing_prompt`。不重试、不缓冲补写；IO 失败只停本轮，不杀不重发目标会话。

主任务已发送且有上一轮预测可结算时，即使后续抽取失败，error JSON 也带 `next_skill_feedback`（机械反馈）并更新本地统计；发送前失败不结算。学习降级的 `learning_warning` 可附在成功或 error JSON，均不新增 error kind、不改变退出码。

## error kind 表

| kind | 含义 | 调用方动作 |
| --- | --- | --- |
| `python_too_old` | 解释器低于 3.10 且 uv 自举失败（uv 探不到或重 exec 失败） | 装 3.10+ 解释器，或装 uv（脚本自动经 `uv run --no-python-downloads` 重跑） |
| `duplicate_release` | 该放行 id 已有台账/落盘 | 查台账；重发须用户重新放行、新 id |
| `reset_required` | 提示词行首调用会话起始 skill（wayfinder/grill-with-docs/implement/implement-spec/prototype）、`--reset=none` 且目标 `turn != 0`（或缺失）；在 `hc get`＋身份检查之后、写台账前拒发，零发送（ADR-0004） | 补 `--reset new/clear/compact`（agy/pi 认 `new`，claude 认 `clear`，codex 认 `new`/`clear`）用**同一放行 id** 重试（id 不消耗）；清单与边界见 SKILL.md「会话起始 skill」 |
| `split_required` | 提示词行首命中会话起始 skill 且归一化 UTF-16 码元 >700 或含内部换行（尾部换行不算）：Claude Code 折叠超体积/多行粘贴致 slash 命令不展开；`hc get`/领养前静态拒发，零发送零副作用（退出码 2，id 不消耗，ADR-0017） | 改提示词文件为单行命令（只带短参数）用**同一放行 id** 重试；长正文请用户重新放行、新 id 下一轮发；纪律与证据见 SKILL.md「会话起始 skill」 |
| `bad_args` | 静态旗标错误（ADR-0009）：`--launch`/`--cwd` 单传（须成对）；`--rebuild` 在非占位分流误传；创建时 `--target` 不是合法会话名（1–32 位 a-z0-9-_，t 号保留）；`--display-name` 非法（空、超 64 字符、含换行或控制字符，ADR-0010）；`--space` 空/纯空白、路径形态（`/` 或 `~` 开头）不是已存在目录、名重名命中多个空间（ADR-0011；`--space` 缺失由 argparse 拒，无 JSON）。写台账前拒发（退出码 2，id 不消耗；名重名在领养解析时发现，预检路径同样零发送、id 不消耗） | 改正旗标用同一放行 id 重试；`--target` 推荐传会话名（`<bot>-<machine>`，ADR-0015） |
| `launch_required` | 目标 not_found/finished/空闲 shell，需创建/复活却没传 `--launch`（＋`--cwd`）；写台账前拒发，零发送（退出码 2，id 不消耗，与 `reset_required` 同构，ADR-0009） | 补 `--launch <shell 启动命令> --cwd <目录>` 用**同一放行 id** 重试；创建参数须随放行一并贴出（SKILL.md） |
| `display_name_required` | 创建/复活/重建新鲜路径缺 `--display-name`；写台账前拒发，零发送（退出码 2，id 不消耗，与 `launch_required` 同构，ADR-0010） | 补 `--display-name <显示名>` 用**同一放行 id** 重试；显示名须随放行一并贴出（SKILL.md） |
| `session_occupied` | 同名 live 终端被别的 agent/程序占用或无 loop 标签（ADR-0009）；message 呈报占位者地址/前台/turn/状态，不建不 kill、零发送、id 不消耗 | 呈报占位者给用户；确需重建经用户显式授权后带 `--rebuild`，或换 `--target` 到另一命名会话 |
| `rebuild_busy` | `--rebuild` 但占位者状态是 working/blocked/未自报，不是 idle/done，拒杀（ADR-0009）；零 kill、id 不消耗 | 等占位者落定或请用户到屏幕前处理；在跑的工作与等人决定的弹框绝不被杀 |
| `adopt_failed` | 领养失败，恒可见不变量破缺（ADR-0009）：`adopt_reason` 为 `app_closed`（herdr-app 没开/不可达）/`keep_error`（space.new/keep 写步失败；#46 起赠送清理失败不再 `adopt_failed`，降级 `gift_cleanup_warning`）/`no_pane`（keep 后轮询窗口耗尽仍无 pane，#40）/`space_not_found`（`--space` 名/id 在 ls 里找不到，或命中空间缺 id；名/id 不自动建空间）/`space_ambiguous`（`--space` 路径按 folder 命中多个空间）/`select_error`（space.select 失败或复核选中不符，keep 未发；复核不符时先走还原护栏，select 已生效也不把焦点留在目标空间；ADR-0011）。**创建路径**：保留已建终端、`launched:true`/`target_address`/`workspace:"failed"`、放行 id 消耗；**既有路径预检**：零发送、id 不消耗 | 创建路径：开 app 后**新放行 id** 重试走既有路径补领养修复；预检路径：开 app 后**同 id** 重试。`space_not_found`/`space_ambiguous`：核对目标空间名/id/路径（路径多匹配改传 id 或唯一名）。部分状态（空间已建/pane 已落地/选中已还原）如实报 |
| `launch_failed` | 创建/复活失败（ADR-0009）：hc run 失败（含 `name_taken` 并发竞态映射）、打标签失败、启动命令发送失败、等 shell/等 agent 超时或终端死亡；带部分状态（`launched`/`launch_sent`/`rebuilt`＋旧 t 号）。创建路径等就绪在领养之后（#46），超时/死亡同样带 `workspace:"adopted"`/`pane_adopted:true`（会话已可见） | 告知用户；经放行后另起一轮。`name_taken`＝同名 live 终端存在，不建不 kill |
| `api_not_configured` / `bad_extract_backend` / `bad_api_max_tokens` | 抽取后端启动预检不过（ADR-0005）：backend=api 缺 `LOOP_API_BASE/KEY/MODEL`；`LOOP_EXTRACT_BACKEND` 值非法；`LOOP_API_MAX_TOKENS` 非正整数。未发送 | 配齐/改正 env 用同一放行 id 重试，或设 `LOOP_EXTRACT_BACKEND=pi`；见 SKILL.md「抽取后端」 |
| `not_idle` / `identity_mismatch` | 预检不过，未发送 | 整理成选项卡告知用户 |
| `send_refused` | 前置或主任务 hc 拒发且 done 补发不适用或仍失败；看 `reset_sent`/`model_sent` 判断哪些前置已发 | 同上 |
| `dialog_blocked` | 目标停在弹框（审批/提问/认证）。创建路径等 agent 就绪时卡弹框（#46）：领养先于等就绪，会话已可见（`workspace:"adopted"`＋`pane_adopted:true`），主任务未发、放行 id 消耗 | 告知用户去目标屏幕前处理（会话已可见于 herdr-app 目标空间）；不按键、不代答；处理后用**新放行 id** 重新发起（走既有路径） |
| `timeout` | 墙钟到点（前置或主任务），目标仍未落定 | 告知用户；前置超时则主任务未发、原文未取回 |
| `not_started` / `target_exited` / `target_error` | 发出后未开工 / 程序退出 / 自报 error | 告知用户 |
| `extraction_rewritten` | 抽取字段全部未通过本轮原料逐字校验；message 按实际后端（api/pi）取值 | hints 逐字段给「字段名＋最接近原文整行」（零截断）＋本轮原料＋回读全文路径；可人工重做抽取 |
| `extraction_failed` / `extraction_invalid` / `tool_timeout` | 抽取失败或子进程超护栏 | 原文已落盘，可人工重做抽取 |
| `io_failed` | 中途本地 IO 失败（ADR-0024，#111）：runs 目录创建、台账 open＋中途写、回读原料/本轮原料/干净回复全文落盘、api 读本轮原料等整类 `OSError` 顶层兜底；退出码恒 1，message 带操作＋路径＋原文。hints 按台账已开与否分两态（未开→id 未消耗；已开→id 已消耗、部分状态如实带出） | 修本机环境（磁盘空间/权限/目录），**不是去目标屏幕前查看**；台账未开→环境修复后**同一放行 id** 重试，已开→修复后**新放行 id** 重新放行（同 id 重试撞 `duplicate_release`） |

完整性标记（ADR-0019，#79）：**无完整性硬闸**。折叠判据（词条见 GLOSSARY）只查**本轮原料**（与提示词行去首尾空白后精确相同的回声行不参与），只认 claude 实证形态、两独立分支：① 去前导空白后行首 `… +N lines`（… 恒 U+2026，允许 `(~K KB)` 等后缀与单数 `+1 line`）；② 行内含 `ctrl+o to expand`（覆盖 `› Message from @X (…)` 与 `⎿ Backgrounded agent (…)` 形态）。命中只标记 `complete:false`＋`complete_reason:["fold_markers"]`，抽取与 result 照常，供人复核；`incomplete_result` 退役。旧轮已折叠内容不误报本轮；锚点找不到而整段使用时，本轮原料就是全部回读，旧轮折叠仍可命中——诚实标记。判据只覆盖 claude code 2.1.331 实证形态，其他 kind（grok「首行 + …」、qwen 等）与键位重映射场景可能漏检，不为未实证形态加猜测性判据（ADR-0019 缺口声明）。锚点（提示词第一个非空行前 24 字符）降级为信息字段 `anchor_found`：完整性检查在 turn>基线＋落定之后，本轮确已被处理，超过 1000 行历史时回声仍可能不可得，故锚点搜不到不再阻断（PAW 实测多次误判后降级，ADR-0013 维持信息字段、不得恢复为硬判据）。hc read 自己的 `complete` 字段只记台账、不作判据。

监听（`hc get`/`hc read` 只读）随时可；直推（绕过 dispatch 主动发）仅在报错时，且内容仍须用户放行。正常回复一律走管道。

## 机械保证（脚本兜底，调用方无需自查）

原文照发、一个放行至多发送成功三次（重置或启动前置＋切模型前置＋主任务，各一次；新鲜会话跳过重置）、永不 `--force`、不碰 `hc keys`、身份预检（`--expect-proc` 匹配 foreground.argv[0]/status.app 任一；foreground.name 不可靠——claude 实测为版本号，不作判据）、sha256 记账并随成功输出回报、不完整不进抽取、改写字段标 `rewritten`。重置前置按枚举校验、与主任务共用同一发送门，任一步发不出即整轮停。

回读与断轮（ADR-0013，supersede 裁定 A3「只取当前屏」）：主任务落定后 `hc read --lines 1000` 回读最近 1000 行拼接历史＋当前屏（hc stitched 历史上限），不再只抓当前屏——长回复滚出当前屏的部分仍可取回。**超过 1000 行历史的回复开头仍不可得**，回读全文不等于无限完整历史，本轮原料与干净回复全文也不提供超出该范围的保证（如实声明，不自动滚动/按键/补发）。回读全文逐字落 `.raw.md`（审计，含旧轮）；再按锚点（提示词第一个非空行前 24 字符，匹配沿用字面＋去空白折叠容错）做一次确定性**断轮**：优先取「去前导空白后以 `❯` 开头且含锚点」的最后一行往后切（回复正文引用锚点但无 `❯` 前缀时不误切回复开头），无 `❯` 形态回声时退化为含锚点的最后一行，锚点完全找不到则整段使用；选中的回声行保留在段内。断轮是机械步，不用模型判轮次边界、不按回复长度/句首形态猜完整性、不因断轮失败新增错误。本轮段逐字落 `.turn.md`（抽取器输入＋逐字校验语料）。result `raw_record` 指回读全文、新增 `turn_record` 指本轮原料、`run_record` 仍指干净全文；台账记 `echo_found`（是否找到锚点并切片）、`turn_cut_line`（1 起切点行号）、`turn_chars`（本轮段字符数），整段使用时 `echo_found:false`、切点 1、字符数＝全文。折叠判据与逐字校验都只对本轮原料（见下）。hc 自报 `complete`/`how`/`why` 只作台账元信息。

切模型前置（`--target-model`，ADR-0006）：重置（如有）之后、主任务之前发字面 `/model <名字>`，等落定（idle/done＋屏幕静置，复用重置的落定机械）再发主任务。名字原样透传：脚本不认识模型、不按 agent 映射、不核实生效、不预读跳过；与 `--reset` 独立可单独带，同传顺序固定 重置→切模型→主任务。`target_model` 是请求值非验证值（仅传了才出现在结果，成功与 error 都带）；前置不抽取、不落 run_record，任一步发不出即整轮停（`error.model_sent` 标明部分状态）。会话起始 skill 纪律不变：`turn > 0` 且 `--reset=none` 仍拒发 `reset_required`，切模型不能替代重置；`turn == 0` 豁免时切模型前置照跑。agent 支持对照见 SKILL.md「调用」（codex 别传：裸 `/model` 开选择器会 blocked 停轮）。

命名会话 get-or-create（ADR-0009，supersede ADR-0007）：`--target` 推荐传会话名（一名一 live，herdr 原生保证；名字恒解析到最新 live 终端），result `target` 恒为传入地址、另报 `target_address`（解析 t 号）。预检分流一律 `hc get`（不用 `hc ls` 的 `agent` 派生字段）：**not_found/finished→创建**（finished 同名重建、旧条目按 t 号留审计）；**live＋前台 agent 身份符＋loop 标签符→既有路径**（核 pane/补领养→idle 核对→发送）；**live＋空闲 shell（前台 shell＋status null）＋loop 标签符→复活**（同样过预检 pane 闸）；**live＋别的 agent/程序（含标签符但前台身份不符）或无 loop 标签→`session_occupied`** 停轮呈报（地址/前台/turn/状态），`--rebuild` 走重建。身份核对（argv[0]/status.app）在分流时做。标签绑定：创建成功即打 `loop=1`＋`loop-agent=<expect-proc>`（无 `--expect-proc` 时只打 `loop=1`）；无标签同名终端拒收编。创建序列（单形态 shell 包装，无直接起 agent 分支）：`hc run --bg --keep --no-parent --name <名字> --cwd <目录> -- zsh -l`（`--no-parent` 无父链、独立于调用方终端，#46：调用方背景终端退出/被杀不连带杀会话，跨轮持久）→ 打标签 → 等 shell 就绪 → 裸 send 启动命令（shell 不报 idle，不带 `--if`）→ **领养（先于等就绪，#46）** → 等 agent 就绪（前台非 shell 且 idle/done）→ 核身份 → 主任务。等就绪卡弹框（登录/授权/网络）：会话已领养可见，整轮停、主任务不发，result `workspace:"adopted"`＋`error=dialog_blocked`、放行 id 消耗（部分状态已发生），用户处理后新 id 走既有路径重试。复活序列：预检 pane 闸（写台账前，与既有路径同级）→ 不 run、不重写标签，send 启动命令 → 等就绪 → 核身份 → 主任务（名字/cwd/终端级历史不动）。`--launch` 永远只收 shell 命令字符串、字面透传、零映射；herdr 模板退居调用方持久配置仓库，loop 不感知模板、不自建配置文件。`--launch`/`--cwd` 成对必填无默认；需创建/复活缺参→`launch_required`（退出码 2、零发送、id 不消耗、补参同 id 重试）。强制重建：`--rebuild` 仅占位分支生效（其余分流误传 `bad_args`），只杀 idle/done（working/blocked/未自报→`rebuild_busy`），kill 后同名新建，台账/result 记 `rebuilt`＋旧 t 号。并发创建竞态由 herdr `name_taken` 仲裁、映射 `launch_failed`，脚本不做第二套一名一 live 实现。换 kind＝同名 `--rebuild`（显式授权、只杀 idle/done），换 `--target` 降为约定外逃生门（ADR-0015）；`--switch-agent`/`--exit-command`/`--exit-keys` 已整条移除。**turn 是终端级累计计数**（status 每次离开 working +1、无清零；复活会话 turn>0 但会话空），修正 ADR-0007「hc 按前台进程追 turn」。会话名约定 `<bot>-<machine>`（一 bot 一机器恒一条、任务区分靠显示名）、新任务三件套见 SKILL.md「命名与任务边界（ADR-0015）」。

恒可见／领养（ADR-0009）：命名会话恒有 herdr-app pane，进不了即整轮 `adopt_failed` 报错、不降级 headless。所有 herdr-app 调用经单一 `app_json` 函数（镜像 `hc_json` 形状：子进程＋JSON＋超时＋错误映射，可注入桩点）；app 存活探测用 `herdr-app status` 退出码，**绝不用裸 `herdr-app`**（会打开窗口）。序列：status 探测 → 只读 `ls` 核 pane → 缺则 `do terminal.keep`（参数 terminal＋可选 `place`；`place` 语义是 `tab`(默认)/`right`/`down`——相对焦点 pane 的位置，**非空间定位**；无 pane 终端一律落进 herdr-app **当前选中的空间**，#43 实证）→ **带超时轮询** `ls` 复核 pane 落地（#40：keep 后台异步落地，单次 ls 会跑在落地前误判 `no_pane`；窗口内反复只读 ls，pane 出现即成功、窗口耗尽才报 `no_pane`；窗口 `--adopt-timeout` 默认 30s、间隔 `--adopt-poll` 默认 2s；keep 真失败仍窗口耗尽后如实报）。目标空间按 `--space` 三态解析（见下「空间定位」）；路径零匹配时 `space.new {"name":<目录名>,"folder":<路径>,"focus":false}` → keep → 复核 → `tab.close confirmed:true` 关赠送 shell 标签 → **带超时轮询 `hc get` 等赠送终端 finished**（复用 `--adopt-timeout`/`--adopt-poll`）→ `hc rm` 该 shell 终端（清理绑 `space_created`，废「本机无 space」特殊分支；#46：tab.close 异步结束终端，单次 rm 撞 live 被拒退出码 1——与 #40 同类竞态改轮询；**清理失败降级 `gift_cleanup_warning` 不丢放行**：tab.close 失败/超时仍 live/rm 失败都只告警，真终端 `pane_adopted` 则整轮仍 adopted、照发主任务）；领养任一步失败 `adopt_failed` 带部分状态（空间可能已建、pane 可能已落地，如实报）。**创建路径**领养在写台账后、启动命令发出即跑（先于等 agent 就绪，#46）：失败保留已建终端、`launched:true`/`target_address`/`workspace:"failed"`＋原因、放行 id 消耗，开 app 后新 id 重试走既有路径修复；**既有路径**每轮预检（写台账前）核 pane、缺则补领养（park 等漂移自愈），失败零发送、id 不消耗、同 id 重试（与 `not_idle` 同级）。app 没开时所有轮次停摆（已接受的代价）；关 tab＝杀终端，走 finished→重建。

空间定位（ADR-0011，#43 grill 定稿，supersede ADR-0009「落点不可控已接受」＋「零空间 space.new{name:会话名}」与 ADR-0010「space.new name 维持会话名」）：`--space` **必传**（所有调用，argparse `required=True`，缺失 argparse 拒、无 JSON），把 keep 落点意图变成每轮显式契约——**不传参静默按 cwd 猜仍禁止**（多空间可共享同一 folder，且有的会话故意放 home-folder 空间，静默路由会拆散既有组织）；显式传路径 opt-in 自动建是另一回事。只在**需要 keep**（pane 缺）时生效；pane 已在 adopt 提前返回（不 select、不挪 pane）。**三态解析**对着领养已取的 `ls`，匹配归一纯函数 `parse_space`（#70，唯一实现）：`/` 或 `~` 开头→**路径**（须已存在目录，否则 `bad_args`；按 `space.folder==路径` 匹配：唯一→复用，零个→给出新建提案，多个→`adopt_failed(space_ambiguous)`）；`sp` 前缀→**id** 直配（不存在→`space_not_found`）；其余→**名**（唯一匹配；重名→`bad_args` 退出码 2、id 不消耗；不存在→`space_not_found`，名不自动建）。领养与学习（`resolve_space_id_for_learning` 只读投影）共用该匹配；**建不建空间是调用方策略**——领养路径零匹配执行 `space.new{name:目录名,folder:路径,focus:false}`，学习零匹配返回 `None`（不记录、不建）。`--space` 与 `--cwd` 独立（空间 folder 未必等于终端 cwd）。序列：目标≠当前 shown 才 `space.select {"id"}` → `status` 复核选中（命令成功不算数；不符→先走还原护栏再 `select_error`，keep 不发——select 可能已生效只是复核抖动，焦点不滞留目标空间）；目标==shown 不 select（`space_restore:"not_needed"`）→ keep → 轮询 `ls` 复核 pane（#40 护栏不变）＋**复核 pane 所在空间**（keep 异步落地，窗口内选中被切走时 pane 会落进别的空间：`space_landed` 如实报，非目标空间→`space_target_warning(landed_elsewhere)` 告警不丢放行——pane 已落地恒可见满足，不静默重演 #43）→ **还原选中**。还原竞态护栏：还原前只读 `status` 核当前选中——仍是目标空间（用户没接管）才还原回原选中；用户已切走即跳过（`space_restore:"user_switched"`），绝不抢回用户的视图；keep 失败路径（keep_error/no_pane）也经 finally 还原。**还原失败降级告警不丢放行**：`space_restore_warning`（`restore_error`/`restore_mismatch`），pane 落地仍是硬闸。零空间统一走路径解析（废特殊分支），建空间 name=目录名，赠送 shell 清理绑 `space_created`（#46 起清理轮询等 finished＋失败降级 `gift_cleanup_warning`，见上「恒可见／领养」）；建后复核 shown（零空间新空间即显示，目标==shown 不 select）。result `space` 回显请求值（成功与 error 都带，含空串 `bad_args`）、`space_selected` 解析 id、`space_landed` 实际落点空间 id、`space_target_warning` 落错告警、`space_restore` 还原结果。

显示名层（ADR-0010）：`--display-name` 调用方显式传参，脚本不派生、不拼接、不理解，只机械校验（非空、1–64 字符、禁换行控制字符，非法 `bad_args`）＋落地。新鲜路径（创建/复活/重建）必传，缺则 `display_name_required`；复用轮可选，传了就覆盖更新、不传不动（无记账比对，放行授权名字本身）。约定每轮传：同任务各轮同名（幂等覆盖）、新任务才换名（ADR-0015）；脚本必传范围不变。落地时序：领养复核 pane 落地后、前置/主任务发送前，`find_tab_id` 拿 tab id → `tab.rename` → `ls` 复核 `tab.name == 显示名`（命令成功不算数）；台账 `naming` phase。**失败降级告警不丢放行**：rename 失败/复核不符/无 tab → result `display_name_warning`（reason：`rename_error`/`rename_mismatch`/`no_tab`），主流程继续，选项卡如实呈报；pane 落地仍是硬闸（`adopt_failed` 不变）。result `display_name` 回显本轮传入值（成功与 error 都带，未传则无）。**不写 summary 标签**：机器可读靠自动跟随 tab 名的 `where` 标签；`space.new` 的 name 按 ADR-0011 为目录名（supersede ADR-0010 原「维持会话名」）。

预检顺序：`duplicate_release` → `split_required`（静态，先于工具查找）→ 分流（`hc get`，含身份核对）→ `launch_required` → `display_name_required` → 核 pane/补领养（既有/复活路径，写台账前）→ 会话起始 skill 纪律（创建/复活/重建视同 turn 0 豁免）→ 写台账 → 前置 → 主任务。新鲜豁免：刚创建/复活/重建的会话自动跳过重置发送（result `reset_skipped:"fresh_launch"`），切模型前置照跑（ADR-0006 纪律平移）；启动命令是命令不是提示词：不抽取、不落 run_record，占前置发送槽，一轮至多发送成功三次不变。台账新增 launch/adopt/rebuild/naming 各 phase（等 shell、发启动、等 agent、领养中、重建、设显示名各步），失败 error 带部分状态。

会话起始 skill 纪律（ADR-0004）：提示词**行首**出现 `/wayfinder`、`/grill-with-docs`、`/implement`、`/implement-spec`、`/prototype` 之一（允许行前空白；命令后须非词字符且非连字符，`/implementing`、`/implement-foo` 不误中，`/wayfinder#7` 算调用；`implement-spec` 优先报长名）且 `--reset=none` 时，在 `hc get`＋身份预检之后、写台账与任何发送之前校验目标 `turn`：`turn == 0`（精确整数 `0`，尚未跑过任何轮次的全新空会话）直接豁免放行；`turn > 0` 或缺失时拒发（`reset_required`，退出码 2，message 带 skill 名、行号与当前 `turn` 值）。分流判定创建/复活/重建的新鲜会话视同 turn 0 豁免、不看终端 turn（turn 是终端级累计，复活会话 turn>0 但会话空，ADR-0009）。行文中提及不触发；放行 id 不消耗，补 `--reset` 同 id 可重试。`new`/`clear`/`compact` 都算重置。「调用独占一行」是 SKILL.md 的文字纪律，机械检查只兜行首这一层；`not_idle`/`identity_mismatch` 先于 `reset_required`。命令行单行轮纪律（ADR-0017，#61）：命中触发器且归一化（CRLF/CR→`\n`、tab→4 空格，复刻 Claude Code 2.1.331 实证 800 留余量）后 UTF-16 码元 >700 或含内部换行（尾部换行不算）时，在 `hc get` 之前静态拒发 `split_required`，先于 `reset_required`，不分 agent、与 `--reset` 独立；闸值硬编码零旋钮。

抽取后端（ADR-0005）：`LOOP_EXTRACT_BACKEND=api|pi`，默认 `api`。`api` 走 OpenAI 兼容 chat completions：`LOOP_API_BASE`（到 `/v1`，容忍尾斜杠，脚本拼 `/chat/completions`）、`LOOP_API_KEY`（Bearer）、`LOOP_API_MODEL` 全必填无默认，启动预检缺失即 `api_not_configured`（只列变量名）；`LOOP_API_MAX_TOKENS` 可选、默认 32768、非正整数启动即 `bad_api_max_tokens`。请求 system=extract.md、user=指令→few-shot 注入（如有）→回声参照→内联本轮原料居末尾（ADR-0013/#127）；回声参照是本轮发送的提示词原文（粘贴归一后），只供认出回声及其软换行续行、不是校验语料。temp 0、不流式、不重试、不发 response_format，超时复用 `--extract-timeout`；代理决议与出口和 webhook 同源（ADR-0003）。失败只记状态码/类名（HTTP → `extraction_failed` 带 `HTTP <码>`、不读响应体；超时 → `tool_timeout`；其它网络异常 → `extraction_failed` 带类名），密钥/URL 零回显。`pi` 备选路径照旧（@本轮原料文件、`--pi-model`；回声参照进 instruction 文本，@文件附件不变），`api` 路径不找 pi、不依赖 node。两后端都读本轮原料、不再读含旧轮的回读全文。result 与台账 params 带 `extract_backend`（error 也带）。

发送门：首发只 `--if idle`；被拒（`status_mismatch`，零字节进入，不算重发）后 `hc get` 复核确认 `done`，补发前再核一次非 working/blocked，才不带 `--if` 补发一次；仍失败即 error。

抽取逐字校验（语料＝本轮原料，ADR-0013）：选段字段逐一做本轮原料子串校验（含去空白折叠），搜不到标 `rewritten` 附最接近原文整行，全部搜不到则 `error(extraction_rewritten)`；`reply` 干净全文先整体、再逐行校验，未过逐字校验的行标 `rewritten["reply"]`。旧轮文本不参与通过判定。`needs_user`（布尔判断）与 `next_skill`（推测，ADR-0008）不参与校验。`extraction_rewritten` 错误载荷（#50）：不新增结构化字段，hints 每个未过校验字段一条「字段名＋最接近的原文整行」（`closest_fragment` 原样整行、零截断，不再对序列化 JSON 切片），另给本轮原料路径＋回读全文路径；message 按本轮实际后端（`api`/`pi`）取值，不写死 pi（pi 专属启动/进程退出路径文案保留）。其余纯文本 stderr/stdout 摘录的 300 字符噪声护栏不变。

下一步预测（ADR-0008，#48 阶段一）：抽取输出第六字段 `next_skill`（字符串）＝调用方下一轮应使用的 **skill 名**，不带斜杠、不含参数；`""`＝无预测。候选固定七项：wayfinder/grill-with-docs/to-spec/to-tickets/implement/implement-spec/prototype。唯一推测字段：extract.md「只依据原文」规则为它开显式例外，豁免逐字校验、不进 `rewritten`；缺失、非字符串、带斜杠、带参数或集外值均按 `""` 投影，不整轮报错，形状门不检查它，学习元数据另记 `invalid_prediction`。仅成功结果带（error 路径不进抽取）；随同一份 JSON 进 result/webhook，不写台账。调用方拼成 `/<next_skill> <自填参数>`，旧 `next_command` 直接替换、不留兼容别名。**建议不是闸**：发送路径零改动、无新 error kind；放行卡固定呈上轮预测与四状态（照用预测／已编辑＋理由／上轮无预测／预测不可得），状态不写进主任务文件，预测是起始 skill 且 turn>0 时同次放行一并推荐 `--reset`。

预测反馈（ADR-0012，#48 阶段二）：按调用方传入的 `target` 字面值分组，同 target 下一次实际完成发送的主任务首个非空行是上一轮预测的唯一 ground truth；`target_address` 不作长期键。发送前失败不消耗待观察；发送成功后立即机械结算，后续抽取失败也把 `next_skill_feedback` 附在 error JSON 增量字段并更新本地统计。每轮成功抽取都创建待观察（含空预测）；异常多条只对齐最近一条，旧记录标 `orphaned`。实际首行只解析成候选 skill／集外斜杠命令／普通文本类型，不把参数或首行全文持久化进反馈或学习案例。

机械指标只有三项：**skill 命中率**（实际属于七项时同 skill 才算命中，空与无效预测均算未命中）、**预测覆盖率**（实际属于七项时合法非空预测所占比例）、**空预测正确率**（实际为普通文本/集外 skill/无斜杠时合法空预测所占比例，无效预测不算正确弃答）；另报 `invalid_prediction` 与样本数。统计含全量、最近 20 条滚动窗口（带样本数）、`examples_used=0/>0` 两队列（专指 few-shot 条数；近期序列使用情况另记），只看趋势、不宣称因果提升、不设硬提升门槛。

近期序列与 few-shot（ADR-0012）：主任务发送成功后记录同 space id＋同 target 最近 8 个主任务的纯 skill 名序列（含本轮、oldest→newest；集外斜杠命令记名，普通文本记 `plain_text`，不记参数正文，前置不进序列）。学习案例存 target/space_id/两轮放行 id/时间/机械分类、预测轮干净回复全文、当时 next_skill、actual_skill／实际类型、当时最多 8 条序列快照；回读原料、本轮原料与下一轮完整放行正文不入案例。每轮最多选 4 条同 space 案例，优先最近纠错、补足命中与正确弃答，同 target 优先再时间倒序；注入「历史近期序列＋历史干净回复＋当时预测＋实际 skill」，历史正文总预算 16,000 字符，超限少选不截断单条，序列不占此预算。无历史保持原 prompt；预测优先级为当前回复明示＞当前近期序列＞few-shot＞空。跨 space 只按 target 结算机械反馈，案例标 `learning_excluded:"space_changed"`，不进任何池，旧 space 正文不随新 space 请求出机。

学习存储（ADR-0012）：默认 `${XDG_DATA_HOME:-~/.local/share}/matt-loop/learning/`，`LOOP_LEARNING_DIR` 可覆盖；目录 `0700`、文件 `0600`，本地 JSONL/JSON，90 天且每 space 最近 100 条，到期/超额先删最旧，只含计数的全局汇总可长期保留。清理由正常读写触发，无后台任务。本地读改写用短临界区锁（不跨 API/pi）、两轮放行 id 去重、统计原子写；锁/I/O/权限/损坏一律降级 `learning_warning`，不新增 error kind、不改退出码、不阻断主轮。api 后端允许同 space 历史干净回复随既有抽取请求发往 `LOOP_API_BASE`（pi 按 pi 配置），案例正文绝不进 result/webhook。`LOOP_LEARNING=off` 关闭学习读写、近期序列与 few-shot，原有 next_skill 预测照旧；其他值默认开启。space id 缺失禁用该轮学习并告警；space 重建得到新 id 即新池，不按 name/folder 继承。不自动回填 `$TMPDIR` 旧数据。

通知投影与通知记录（双产物，ADR-0014，#52 阶段一）：dispatch 交付时同时写全量结果 `.result.json`（审计/回看，字段语义不变）与 `.notify.json`（通知投影＋通知记录）。**统一读取契约**：DS Bot 与 Grok Bot 都先读 `notify.json`，只有需要完整诊断、审计或回看时才读 `result.json`；云端传话助手实际只取得 webhook 正文，`notify.json` 是发送端通知记录、不是云端可读本机文件，`run_record` 只给位置、传话助手不读取。成功通知（`ok=true`）严格只含下列 13 字段（ADR-0019 增补完整性标记）（顺序固定，逐字来自本轮结果，不补造）；error 通知不套白名单、保留发送前完整对象（`error.kind/message/hints` 与全部部分状态）：

| 字段 | 含义 |
| --- | --- |
| `ok` | 本轮是否成功（成功通知恒 `true`） |
| `release_id` | 放行 id |
| `target` | 传入地址（会话名或 t 号，寻址稳定） |
| `needs_user` | 回复里是否有需用户决定的事（布尔） |
| `questions` | 逐字摘出的问题/审批句列表 |
| `conclusion` | 逐字摘出的结论段（无则 `""`） |
| `blocked` | 逐字摘出的弹框原句（无则 `""`） |
| `rewritten` | 未过逐字校验的选段字段标记（`{}`＝全部命中） |
| `run_record` | 干净回复全文位置（仅给位置，传话助手不读） |
| `elapsed_s` | 主任务发送成功到取回落定的秒数 |
| `next_skill` | 抽取器推测的下一轮 skill 名（`""`＝无预测） |
| `complete` | 完整性标记（折叠命中 `false`，抽取照常、需人复核，ADR-0019） |
| `complete_reason` | 命中原因列表（`["fold_markers"]` 或 `[]`） |

`webhook`/`webhook_error`/`webhook_error_detail` 是 POST 后才产生的发送诊断，**只归全量结果**，绝不进通知投影/通知记录。投影只序列化一次；通知记录在首次 POST 前落盘，文件字节与实际请求体完全相同（不追加换行），主出口/代理重试/直连兜底复用同一组字节。无论 webhook 是否启用，只要已建立安全运行路径就生成通知投影与通知记录。**唤醒优先**：通知记录写失败仍尝试 POST，全量结果记 `notify_record_error`（异常类名）＋stderr 诊断；全量结果也不可写时 stderr/stdout 不声称记录完整；写失败不改退出码/`ok`、不递归生成第二条 error 通知。早期 error（非法放行 id 等）不构造通知记录。不新增 webhook 环境开关。

可选 webhook：env `LOOP_WEBHOOK_URL`＋`LOOP_WEBHOOK_SECRET` 都配才发，成功/报错都 POST **通知投影字节**（与 `notify.json` 完全相同，见上「通知投影与通知记录」），鉴权走 `Authorization: Bearer <密钥>`，不进日志、不进 stdout，单次 ~10 秒超时；发失败不连累整轮，result 记 `webhook: off/sent/failed`，失败另记 `webhook_error`（HTTP 状态码如 `"HTTP 401"`，或异常类名如 `"URLError"`）与 `webhook_error_detail`（脱敏诊断，stderr 打印同一份），结果文件仍是事实源。

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

工具定位：`hc`（任何后端都必需；`backend=pi` 时还有 `pi`）先 `shutil.which`（尊重显式 PATH），找不到再探 `~/.local/bin`、`/opt/homebrew/bin`、`/usr/local/bin`、nvm node bin（`~/.nvm/versions/node/*/bin`）；后台 job 缺 PATH 也能起，探不到才报 `missing_tool`。`uv` 不每轮要求，只在解释器 `<3.10` 的自举路径里探（探不到报 `python_too_old`，ADR-0005 决定 7）。调用方 `python3` 直跑即可：版本门 `>=3.10` 直跑，`<3.10` 由 `handle_old_python` 探 uv 并 `os.execv` 经 `uv run --no-python-downloads` 自举重跑（env `LOOP_UV_REEXEC` 防循环），uv 退居内部兜底，调用方不用管 PATH。

本地记录归置：`--loop-dir` 默认 `$TMPDIR/loop`（`tempfile.gettempdir()/loop`），`runs/` 放记录（台账/raw/record/result/notify）、`releases/` 放放行提示词；CWD 无关、结果绝对路径回报，在哪跑都落同一处。全部随 OS 临时清理、不留持久审计，台账拒重发保护止于重启（放行 id 每次唯一，跨重启复用风险低）；脚本不加清理垫片。PATH 由 `find_tool` 兜底，调用方不需手写 export PATH 包装脚本。

## 发布前检查

把 `loop/` 公开到 GitHub 前，在仓库根目录跑一遍。第一行的 `-e` 里补上你环境里的机器名、主机名、内网域名、私有组织名、用户名；第二行是通用模式，直接可跑：

```bash
grep -rniE -e '<机器名>' -e '<主机名>' -e '<内网域名>' -e '<组织名>' -e '<用户名>' loop/
grep -rnE '/Users/|/home/|[[:space:]](10|127)\.[0-9]+\.[0-9]+\.[0-9]+|192\.168\.|172\.(1[6-9]|2[0-9]|3[01])\.' loop/
```

命中即改：机器名、主机名换通用表述，绝对路径换占位符（`<工作目录>`、`<放行id>`），会话地址、账号一律不写。`herdr`、`hc`、`pi` 这类公开产品名可以保留。扫描命令自身的模式串会命中自己，属预期误报。