---
name: loop
description: 走管道：每轮要回复执行者的信息都经 loop 推回——把用户放行的提示词发进 herdr 目标会话并取回结果（裁剪＋抽取成 JSON，默认直连 API、pi 备选）；监听只读随时可，仅报错时可直推（仍须放行）。命名会话 get-or-create（ADR-0009）：--target 推荐传会话名（约定 <bot>-<machine>，一 bot 一机器恒一条、任务区分靠显示名，ADR-0015），目标缺失/已死/空闲 shell 时同一放行内创建或复活（--launch shell 启动命令＋--cwd 成对必填，--display-name 新鲜路径必传，缺参 launch_required/display_name_required 补参同 id 重试）；名字被占呈报 session_occupied，--rebuild 经用户显式授权只杀 idle/done 占位者；命名会话恒可见于 herdr-app 工作区（领养失败 adopt_failed 停轮，herdr-app 必须常开），--space 必传指定领养落点空间（名/id/路径三态解析，ADR-0011）；换 kind 同名 --rebuild（显式授权、只杀 idle/done），换 --target 降为约定外逃生门（ADR-0015）。可选 --reset 先发 /new /clear /compact；可选 --target-model 先发 /model <名字> 切目标模型（独立于 --reset，顺序 重置→切模型→主任务）；调会话起始 skill（wayfinder/grill-with-docs/implement/implement-spec/prototype）时，全新空会话（turn == 0）或刚创建/复活的新鲜会话自动豁免，复用旧会话（turn > 0）必须带 --reset；命令行必须单独单行一轮（超体积或多行 → split_required 静态拒发，ADR-0017）。手里有放行 id、提示词文件、目标地址，要发送并取回新结果时用。
---

# loop：走管道

**走管道**：每一轮要发给执行者的回复，都经本 skill 的 `scripts/dispatch.py` 推回。和用户讨论定稿后，写放行文件、调 dispatch 发送＋取回、判读结果、再讨论——下一轮回复仍走管道。监听（`hc get`/`hc read` 只读）随时可；仅当 dispatch 报错才可直推，直推内容仍须用户放行。

dispatch 做发送、等待、回读、断轮、落盘这些机械活，抽取器（默认直连 API，pi 备选，见「抽取后端」）读本轮原料做裁剪＋抽取；异常由脚本直接返回 error，不进抽取。架构见 `docs/adr/0002`、`docs/adr/0005`、`docs/adr/0013`，用词见根目录 `GLOSSARY.md`；error kind 表、机械保证、发布前检查见 `REFERENCE.md`。

## 每轮循环（走管道）

1. **讨论定稿**：和用户定出要回复执行者的全文。同 target 上一轮结果的 `next_skill` 非空时，本轮主任务**应照用该 skill 作开场**，由调用方拼成 `/<next_skill> <自填参数>`；换 skill 须在放行卡明示理由（ADR-0008），卡片四状态见「预测可见性」。`--target` 推荐传**会话名**（一名一 live，名字恒解析到最新 live 终端，不必追踪 t 号漂移）；目标可能不存在/已死/空闲 shell（首轮、agent 退出后）就备好 `--launch`（shell 启动命令）＋`--cwd`，**讨放行时把创建参数（会话名、启动命令、cwd）、显示名（每轮贴出：同任务各轮同名、新任务换名）与目标空间（`--space`，每轮必传）随全文一并贴出**，一声「发」授权创建＋主任务，绝无静默创建；新任务首轮三件套（ADR-0015）：换显示名＋推荐 `--reset`（不限会话起始 skill，建议不是闸）＋放行卡预测标「已编辑：新任务，上轮预测不适用」；需强制重建时贴「将 kill <名字>（占位者摘要）并重建」，用户显式授权后才传 `--rebuild`。若准备调用会话起始 skill，**命令行必须单独单行一轮**（只带短参数，长正文下一轮，ADR-0017；超体积或多行会被 `split_required` 静态拒发，别先撞了再回来二轮请示），先 `hc get` 看一眼目标 `turn`：`turn == 0`（全新空会话）或本轮将创建/复活（新鲜会话视同 turn 0）不用带 `--reset`；`turn > 0`（复用旧会话）就在这一步向用户讨放行时**一并给出推荐的 `--reset` 方式**，一次放行授权完，首调直带 `--reset`，别先拿 `none` 去撞 `reset_required` 再回来二轮请示。本轮若要切目标模型，也在这一步一并确认 `--target-model` 与模型名（见「调用」）。完成判据：用户对全文（及需要时的创建参数、重置方式、模型名）说「发」，拿到唯一放行 id（对内容方向说「行」不算放行）。
2. **写放行文件**：全文写成 UTF-8 文件，放 `$TMPDIR/loop/releases/<放行id>.txt`（目录不存在就建），放行后一字未改。完成判据：文件就绪（dispatch 核对 sha256，改动即拒发）。
3. **走管道推回**：调 dispatch 发送＋取回（见「调用」「交付」）。完成判据：dispatch 返回、`.result.json` 生成。用 `--reset`/`--target-model` 时该放行一并授权全部前置＋主任务（重置是枚举里可预知的固定命令，切模型是字面 `/model <名字>`）。
4. **判读结果**：先读 `.notify.json`（通知投影，成功 13 字段／error 全量），需要完整诊断、审计或回看再读 `.result.json`＋`run_record`（统一读取契约，ADR-0014），整理成选项卡给用户；`next_skill` 非空时以「本轮新预测 skill」一并呈出（它是预测不是事实，参数由下一轮调用方自填）。若带 `next_skill_feedback`，把本轮刚结算的照用／偏离与关键计数变化一并呈出。完成判据：用户看到结果、给出下一轮方向。
5. 回到 1，下一轮回复仍走管道。

## 预测可见性（ADR-0008）

每轮放行卡固定展示上一轮同 target 的预测 skill，并在卡片开场标四态之一：`照用预测`（沿用同一 skill，自填参数不算编辑）、`已编辑：<理由>`（换 skill，理由必写）、`上轮无预测`、`预测不可得`。没有可取得的上一轮结果时不猜测、不补造。新任务首轮（见 GLOSSARY「新任务」）即使沿用同一 skill 也标 `已编辑：新任务，上轮预测不适用`——同 target 跨任务后学习序列与案例池已混任务，上轮预测不具参考性（ADR-0015）；建议层标注，不改配对键、不新增机械拒发。**状态标签只给用户看，不写进主任务放行文件**；主任务首个非空行仍是实际 `/skill 参数` 或普通任务文本，保住行首预检与 ground truth。卡片上的「上轮预测」与结果选项卡上的「本轮新预测 skill」必须分开标注。以上是调用方硬纪律，但建议不是闸：不新增机械拒发，不强制改写用户放行。

## 调用前

本机装有 `hc`、`herdr-app`（恒可见不变量的硬依赖，**窗口必须常开**，ADR-0009）与 Python ≥ 3.10 的解释器（脚本要求 `>=3.10`）；抽取默认直连 API，需 `LOOP_API_BASE/KEY/MODEL` env（见「抽取后端」）。`pi` 只在选 pi 备选时需要；`uv` 只在解释器 `<3.10` 的自举路径里需要（见「调用」）。工具先按 PATH 找，PATH 没有时脚本自动探 `~/.local/bin`、homebrew、nvm node bin——后台 job 缺 PATH 也能起；`hc`（及选了 pi 备选时的 `pi`）探不到才报 `missing_tool`。PATH 由脚本兜底，**不需手写 export PATH 包装脚本**；后台直接用调用方自己的 bg 机制（见「交付」）。

## 调用

```bash
python3 scripts/dispatch.py \
  --target <会话名（推荐）或t号> --release-id <放行id> --prompt-file <提示词文件> \
  --space <目标空间：名/id/路径> \
  --expect-proc <前台进程子串> \
  --launch "<shell 启动命令>" --cwd <工作目录>   # 首轮/目标可能缺失时带，get-or-create 兜底
```

- `python3` 直跑：脚本无第三方依赖、头部声明 `requires-python = ">=3.10"`。版本门 `>=3.10` 直跑；`<3.10` 自动 `find_tool` 探 uv 并经 `uv run --no-python-downloads` 用已有 3.10+ 重跑（不下载），uv 探不到才报 `error(kind=python_too_old)`。后台精简 PATH 里 python3 也在，uv 退居内部兜底，调用方不用管 PATH、不需手写包装。
- `--expect-proc`：身份预检，强烈建议传（grok 传 `grok`、claude 传 `claude`）；只认 foreground.argv[0]/status.app（OSC 7501 自报）任一，不符即拒发。**foreground.name 不可靠**（claude 实测为版本号），不作判据；标题易变，也不作判据。创建时该值写进 `loop-agent` 标签作身份绑定凭据。
- `--reset {none,new,clear,compact}`（默认 `none`）：主任务前的重置前置。选 `new`/`clear`/`compact` 就在同一 target 先 `--if idle` 发字面命令 `/new`/`/clear`/`/compact`，等落定（idle/done＋屏幕静置）再发主任务；`none` 即单 prompt，向后兼容。**发哪条由调用方按目标 agent 认哪个挑**（`agy`/antigravity、`pi` 认 `/new`＋`/compact`；`claude` 认 `/clear`＋`/compact`，不认 `/new`；`codex` 认 `/new`/`/clear`/`/compact`；`grok` 的 `/new`≡`/clear` 都认），脚本不按 agent 映射。前置不抽取、不落 run_record；前置或主任务任一步发不出即整轮停，error 带 `reset_sent` 标明部分状态。`/clear` 是本地命令、可能不进 working，前置落定用屏幕静置而非 turn 判据。**提示词行首调用会话起始 skill 时：`turn == 0`（全新空会话）可不传 `--reset` 直接发；`turn > 0` 或未知时必须带 `new`/`clear`/`compact`，`none` 会被脚本拒发**（见「会话起始 skill」）。
- `--target-model <名字>`（默认不传，ADR-0006）：切模型前置。重置（如有）之后、主任务之前，在同一 target 先 `--if idle` 发字面命令 `/model <名字>`，等落定（idle/done＋屏幕静置）再发主任务。**与 `--reset` 独立**：可单独传（任意一轮都能随下一条消息带模型切换），同传时顺序固定 **重置 → 切模型 → 主任务**。名字**原样透传**：脚本不认识模型、不按 agent 映射、不核实生效、不预读跳过；`target_model` 是请求值非验证值，名字对错归调用方纪律。**哪些 agent 认行内 `/model <名字>`**（已核实）：`agy`/antigravity 认 name/slug/label 并存为默认（已是该模型报 `Model already set`、名字不认打印有效列表，都不弹框；注意 `/model <名字> <提示词>` 是其一次性语义，多词值按字面发出可能触发，别传多词名字）；`claude` 认别名或完整模型 ID（如 `/model sonnet`）；`pi` 认 `/model [provider/model]`；`grok` 认 `/model <名字> [window] [effort]`；**`codex` 的裸 `/model` 开选择器、行内名字未实证——别对 codex 传 `--target-model`**（选择器 → blocked → `dialog_blocked` 停轮），换 codex 模型用启动旗标 `-m` 重开会话。前置不抽取、不落 run_record；任一步发不出即整轮停，error 带 `model_sent` 标明部分状态。会话起始 skill 纪律不变：`turn > 0` 且 `--reset=none` 仍拒发 `reset_required`，切模型不能替代重置。
- `--launch <shell 启动命令>`＋`--cwd <目录>`（ADR-0009，成对必填、无默认，单传报 `bad_args`）：命名会话 **get-or-create** 的创建参数。`--cwd` 仅创建时生效（`hc run` 消费）：复用轮传了不改工作目录，复活沿用终端原 cwd（ADR-0015）；跨仓库任务靠提示词恒带目标仓库绝对路径，不靠 `--cwd`。`--target` 推荐传会话名，预检 `hc get` 报错驱动分流：
  - **not_found / finished** → **创建**：`hc run --bg --keep --no-parent --name <名字> --cwd <目录> -- zsh -l`（单形态 **shell 包装**：持久 shell 终端＋send 启动命令；`--no-parent` 会话独立于调用方终端，调用方背景终端退出不连带杀，#46）→ 打 `loop=1`＋`loop-agent=<expect-proc>` 标签 → 等 shell 就绪 → 裸 send 启动命令 → **领养（先于等就绪，#46：卡弹框也先可见）** → 等 agent 就绪 → 核身份 → 主任务。等就绪卡弹框→`dialog_blocked` 停轮但 `workspace:"adopted"`（会话可见，用户到屏幕前处理后新 id 重试）。finished 旧条目按 t 号留审计，名字恒解析到最新 live。
  - **live＋空闲 shell（前台 shell＋status null）＋loop 标签符** → **复活**：不 run、不重写标签，原地重发启动命令；名字/cwd/终端级历史不动，会话是空的。复活同样过预检 pane 闸（app 没开照样停轮）。
  - **live＋别的 agent/程序，或无 loop 标签** → `session_occupied` 停轮呈报占位者（地址、前台、turn、状态），不建不 kill；别人的会话绝不被静默收编。身份核对（argv[0]/status.app）在分流时做：标签符但前台身份不符也算占位，`--rebuild` 可重建。
  - 需创建/复活却没传 `--launch` → `launch_required`（退出码 2、零发送、放行 id 不消耗），补参后**同 id 重试**即可。启动命令**字面透传**（如 `--launch "claude --dangerously-skip-permissions"`），脚本不映射 kind→命令、不感知 herdr 模板；启动命令是命令不是提示词：不抽取、不落 run_record，占前置发送槽。
  - **新鲜豁免**：刚创建/复活的会话自动跳过重置发送（result 记 `reset_skipped:"fresh_launch"`），会话起始 skill 豁免视同 turn 0、**不看终端 turn**（turn 是终端级累计，复活会话 turn>0 但会话空）；切模型前置照跑。
  - **恒可见（领养）**：自动创建的终端无条件进 herdr-app 工作区——`herdr-app status` 探测（绝不用裸 `herdr-app`，它会开窗）→ 只读 `ls` 核 pane → 缺则 `do terminal.keep` → `ls` 复核落地；`--space` 路径零匹配建空间（focus:false 不抢视图）时清赠送 shell——轮询等终端 finished 再 rm，清理失败降级 `gift_cleanup_warning` 不丢放行（#46）。领养进不了即 `adopt_failed` 整轮报错、不降级 headless：创建路径保留已建终端、放行 id 消耗（开 app 后**新 id** 重试走既有路径修复）；既有路径每轮预检核 pane、缺则补领养，失败零发送、**同 id** 重试。**herdr-app 必须常开**（app 没开时所有轮次停摆，已接受的代价）；关 tab＝杀终端，走 finished→重建。
- `--space <名/id/路径>`（**必传**，所有调用，ADR-0011，#43 grill 定稿）：**空间定位**——领养 keep 的落点空间。keep 无空间定位参数（`place` 只管相对焦点 pane 的位置），无 pane 终端一律落进 herdr-app **当前选中的空间**；落点意图必须每轮显式给出，讨放行时随参数一并贴出。**三态解析**（匹配归一纯函数 `parse_space`，#70，领养与学习共用）：`/` 或 `~` 开头→**路径**（按 `space.folder` 匹配：唯一→复用；零个→领养自动建空间 `name=目录名`，属显式 opt-in；多个→`space_ambiguous`，改传 id 或唯一名；路径须已存在目录否则 `bad_args`）；`sp` 前缀→**id** 直配；其余→**名**（`ls` 唯一匹配；重名→`bad_args`；不存在→`space_not_found`，名不自动建）。学习侧是它的只读投影：零匹配不建空间（`space_id_missing` 禁用该轮 space 学习）。与 `--cwd` 独立（空间 folder 未必等于终端 cwd）。**不传参静默按 cwd 猜仍禁止**（多空间可共享同一 folder，静默路由会拆散既有组织）。只在需要 keep（pane 缺）时生效：目标≠当前 shown 才 `space.select`→keep→复核→还原选中（短暂抢焦点，基本仅创建时 1–2s；窗口内用户接管选中即让位，不抢回）；目标==shown 不 select；pane 已在提前返回不抢。落地复核 pane 所在空间：落进非目标空间（落地窗口内选中被切走）→ `space_target_warning` 如实告警；还原失败降级 `space_restore_warning`——两者都不丢放行。result 带 `space`（请求值）/`space_selected`/`space_landed`/`space_restore`。已落错存量终端不移动（无跨空间挪 pane API），手动挪或 kill 重建。常驻空间约定（ADR-0015）：一 bot 一常驻空间，调用方把固定 `--space` 值写死在自己配置里、每轮恒传同一值；pane 已在提前返回、不跨空间挪，落点与请求恒一致（见 GLOSSARY「常驻空间」）。
- `--display-name <显示名>`（ADR-0010）：命名会话标签页上给人看的名字。**创建/复活/重建三条新鲜路径必传**（缺则 `display_name_required`，退出码 2、零发送、id 不消耗、补参同 id 重试，与 `launch_required` 同构）；复用轮可选——传了就更新 tab 名、不传保持旧值。机械校验：非空、1–64 字符、禁换行与控制字符（非法 `bad_args`）；**不校验式样**。落地：领养复核 pane 落地后、主任务发送前 `tab.rename`＋`ls` 复核名；**失败不丢放行**，result 记 `display_name_warning`，选项卡如实呈报。**传了就覆盖**（放行已授权名字本身）；复用轮传显示名前，调用方若知用户手工改过名，先向用户确认。每轮传纪律（ADR-0015）：同任务各轮传同一显示名（幂等覆盖）、新任务才换名；脚本「新鲜路径必传、复用轮可选」机制不变。
- `--rebuild`（默认不传，ADR-0009）：**强制重建**，仅用户明示授权 kill 重建后传，只在 `session_occupied` 占位分支生效（其余分流误传 `bad_args`）。只杀 idle/done 占位者，working/blocked/未自报 → `rebuild_busy` 拒杀；kill 后同名新建，result `rebuilt:true`＋旧 t 号，kill 后创建失败也带部分状态可审计。放行必贴「将 kill <名字>（占位者摘要）并重建」。
- **换 kind＝同名 `--rebuild`**（ADR-0015）：经用户显式授权 kill idle/done 占位者后同名重建，`--expect-proc` 同步改新 kind；换 `--target` 到另一命名会话降为约定外逃生门（get-or-create 机制仍在，旧会话保留可切回）。`--switch-agent`/`--exit-command`/`--exit-keys` 已整条移除（ADR-0007 superseded by ADR-0009）。
- **命名与任务边界（ADR-0015）**：会话名 `<bot>-<machine>`、显示名每轮覆盖、新任务三件套、cwd/常驻空间/换 kind/存量纪律见下「命名与任务边界」小节。
- 等待与抽取默认值一般不用动：墙钟 3600 秒（主任务发送成功起计）、check-in 180 秒、落定静置 10 秒 × 2 次、抽取护栏 1200 秒（`--wall-clock` / `--check-in` / `--settle-seconds` / `--settle-checks` / `--extract-timeout` 可覆盖）。读取固定 `hc read --lines 1000`（ADR-0013）：回读最近 1000 行拼接历史＋当前屏，再按提示词回声断轮切出本轮段。**超过 1000 行历史的回复开头仍不可得**——回读全文不等于无限完整历史，本轮原料与干净回复全文也不提供超出该范围的保证；hc 自报 `complete` 只记台账、不作完成判据。
- `--loop-dir` 默认 `$TMPDIR/loop`（随 OS 临时清理，记录不持久）；`--prompt-file` 传 `$TMPDIR/loop/releases/<放行id>.txt`；`--pi-model` 透传 `pi --model`（仅 pi 备选后端生效）。

## 命名与任务边界（ADR-0015）

约定靠调用方读本节生效，无机械强制；脚本只校验会话名 1–32 位 a-z0-9-_、不校验式样。违约的机械兜底（`not_idle`、`session_occupied`、`reset_required`）全部沿用既有。

- **会话名形态 `<bot>-<machine>`**：一 bot 一机器恒一条，跨仓库、跨 kind。bot＝派发实例 slug（同族副本实例 slug 不同，如 LoopEng 与 LoopEng2 各一条）；族名只留显示名前缀。machine＝本机恒 `local`；远端＝该机器上的调用方本地取值（该机 herdr 机器名或该机配置），文档只写规则、不写死任何远端名。来源写死调用方配置（DS Bot 用 bot 名；Grok Bot 用账号/配置名＋角色）。超长调用方自截、保证同机唯一。示例：`loopeng-local`；远端 `<bot>-<该机 slug>`（占位式）。
- **任务区分＝显示名每轮覆盖**：`<bot 族>_<任务描述>`（如 `LoopEng_命名层设计`）、可中文，任务描述用短语（GUI 约 170px 截断、十几个汉字）。同任务各轮同名（幂等），新任务才换名。bot 族前缀表示「谁派的活」，kind 前缀冗余。**撞名警示**：显示名恰等于程序名（如 `claude`）会被 tabs mod 当自动名、继续跟随 live title；`<bot 族>_` 前缀天然避开。显示名不是会话名：寻址/复用仍用会话名。
- **新任务**（见 GLOSSARY）：调用方判定任务意图切换后的首轮；同任务多轮不算。新任务首轮三件套：换显示名＋推荐 `--reset`（不限会话起始 skill，建议不是闸）＋放行卡预测标「已编辑：新任务，上轮预测不适用」。
- **cwd 与绝对路径纪律**：`--cwd` 仅创建生效、复用轮传了不改工作目录、复活沿用终端原 cwd；跨仓库任务提示词恒带目标仓库绝对路径，执行者跨目录干活。常驻仓库真的变更时：用户显式关 tab（或授权 kill）→ finished → 下轮 get-or-create 带新 `--cwd` 重建；同 kind idle 时没有 `--rebuild` 可用（它只在 `session_occupied` 占位分支生效）。
- **常驻空间**：一 bot 一常驻空间，`--space` 每轮恒传同一值（见 GLOSSARY「常驻空间」）；pane 已在提前返回、无跨空间挪 pane API。
- **换 kind＝同名 `--rebuild`**（显式授权、只杀 idle/done）；换 `--target` 是约定外逃生门。
- **存量不迁移**：`hc` 无 rename；旧名会话 get-or-create 到自然结束，旧 idle tab 用户手动关，不为改名杀在跑的工作。
- **herdr 模板退居调用方侧持久配置仓库**：用时把启动命令抄出来字面传 `--launch`，loop 不感知模板、不自建配置文件。

## 会话起始 skill（工程纪律）

五个 skill 是**会话起始 skill**：`wayfinder`、`grill-with-docs`、`implement`、`implement-spec`、`prototype`。它们只能作为空会话的开场任务：若目标 `hc get` 的 `turn == 0`（尚未跑过任何轮次的全新空会话），脚本**自动豁免**，不带 `--reset` 直接发（省去空等重置）；**刚创建/复活/重建的新鲜会话视同 turn 0 豁免、不看终端 turn**（turn 是终端级累计计数，复活会话 turn>0 但会话是空的，ADR-0009）；若复用已有轮次的旧会话（`turn > 0` 或 `turn` 未知），则**必须带 `--reset new/clear/compact`** 先清目标执行者会话，且应在 Step 1 讨放行时一并把重置选项与推荐值带给用户确认；调用方自己的会话不动。出处是 `/ask-matt` 工作流（默认调用方已装 Matt 全套 skill）：连续性在工件里——地图 issue、GLOSSARY/ADR、票、handoff 文件——不在聊天里，清会话不丢东西。

- **调用独占一行**：放行提示词里，会话起始 skill 的斜杠命令写在行首（可带参数，如 `/wayfinder #7`）。脚本在 `hc get`＋身份预检之后、写台账之前做机械检查：命中且 `--reset=none` 且 `turn != 0`（或 `turn` 缺失）→ 写台账前拒发（`reset_required`，退出码 2，message 带当前 `turn` 值），零发送、放行 id 不消耗，补 `--reset` 同 id 重试；`turn == 0` 直接放行。
- **命令行单独单行一轮（ADR-0017，#61）**：Claude Code 把超体积或多行的粘贴折叠成 `[Pasted text #N +M lines]` 块，块内行首斜杠命令不展开（2.1.331 实证：UTF-16 码元 >800 或换行数 >2 即折叠，换行阈值随 pane 高度漂移）。机械闸比实证更严：提示词命中会话起始 skill 触发器且**归一化后 UTF-16 码元 >700 或含内部换行**（尾部换行不算；归一化＝CRLF/CR→`\n`、tab→4 空格），在 `hc get`/领养之前静态拒发（`split_required`，退出码 2，零发送零副作用，放行 id 不消耗）。**命令轮严格单行、只带短参数**（如 `/grill-with-docs #61`），长正文请用户重新放行、新 id 下一轮发；改文件为单行命令后同 id 重试。闸不分 agent kind、与 `--reset` 独立；与 `reset_required` 双命中先报本错。
- **中途不触发**：清完以 `/grill-with-docs` 开场后，同会话接着跑 `to-spec`/`to-tickets` 不再清（ask-matt 的 Context hygiene：grill→spec→tickets 一个不间断窗口）；`tdd`、`code-review`、`retro` 等中途 skill、回答提问的普通回复、行文中提及（如「答完再跑 /implement」）都不触发。
- **prototype 的桥是 handoff**：旧会话写 handoff 文件，新会话以 `/prototype` 对着该文件开场。
- `compact` 也算重置（留摘要可接受），唯一拒发值是 `none`；选哪条命令仍按目标 agent 认哪个挑。
- 延伸纪律（ADR-0015，建议不是闸）：新任务首轮（见 GLOSSARY「新任务」）一律推荐 `--reset` 前置，不限会话起始 skill——避免旧任务上下文渗进新任务；脚本机械拒发范围不变（仍只兜行首起始 skill＋turn>0＋none），普通任务新任务首轮不重置不被拒发。

## 抽取后端（默认直连 API）

裁剪＋抽取默认**直连 OpenAI 兼容 chat completions**（ADR-0005），pi 降为显式备选。后端是部署级配置，走 env：

- `LOOP_EXTRACT_BACKEND=api|pi`，默认 `api`；非法值启动即报错。
- `api` 需三个 env **全必填、无默认**：`LOOP_API_BASE`（基址到 `/v1`，脚本拼 `/chat/completions`）、`LOOP_API_KEY`（Bearer，零回显）、`LOOP_API_MODEL`（模型 id 原样透传）。缺任何一个**启动预检**即报 `api_not_configured`（只列变量名），不发主任务、放行 id 不消耗。与 pi 网关复用同一把钥匙时，值指过去即可（如 `LOOP_API_KEY="$PAW_GATEWAY_KEY"`），名字归 loop。
- 请求形状：system=extract.md，user=指令→few-shot 注入（如有）→回声参照→内联本轮原料居末尾（ADR-0013/#127）；回声参照为本轮发送的提示词原文（粘贴归一后），只供认出回声及其软换行续行、不是校验语料。temp 0、max_tokens 默认 32768（`LOOP_API_MAX_TOKENS` 可覆盖，非法值启动报错）、超时复用 `--extract-timeout`、不重试、不流式、不发 response_format。出网代理与 webhook 同一套决议（ADR-0003）。
- 失败映射沿用既有 kind：HTTP → `extraction_failed`（带 `HTTP <码>`，不读响应体）；超时 → `tool_timeout`；其它网络异常 → `extraction_failed`（类名）。密钥/URL 零回显；原文已落盘，可人工重做。
- `pi` 备选：行为照旧（@文件 附件、`--pi-model` 透传；回声参照进 instruction 文本，@文件附件不变），机器上要有 pi；`api` 路径不依赖 node/pi。
- result 与台账带 `extract_backend`（api|pi）标明本轮走的哪条路。
- **迁移**：旧部署没配 `LOOP_API_*` 的，跟新代码后要么配齐三个变量，要么设 `LOOP_EXTRACT_BACKEND=pi` 维持现状，否则启动即 `api_not_configured`。

## 交付：后台跑＋完成通知

一轮最长约 80 分钟（抽取实测每轮 5–11 分钟，pi 路径数据）。用调用方自己的后台任务跑 dispatch、靠完成通知收结果。**双产物（ADR-0014，#52 阶段一）**：dispatch 同时写 `$TMPDIR/loop/runs/<放行id>.notify.json`（通知投影＋通知记录）与 `<放行id>.result.json`（全量结果）。**统一读取契约**：所有调用方都**先读 `notify.json`**（成功严格 13 字段、error 全量），只有需要完整诊断、审计或回看时才读 `result.json`：

- **DS Bot（默认）**：`run_in_background` 跑 dispatch → job 完成通知唤醒 → 先读通知投影、需要细节再读全量结果。
- **Grok Bot**：配 `LOOP_WEBHOOK_URL`＋`LOOP_WEBHOOK_SECRET`（示例见 `webhook.env.example`），dispatch 完成时 POST **通知投影字节**（与 `notify.json` 完全相同）唤醒它 → 先读通知投影、需要细节再读全量结果。云端传话助手实际只取得 webhook 正文；`notify.json` 是发送端通知记录，不是云端可读本机文件。
- **其他调用方**：仿 DS Bot（后台任务＋完成通知，同一读取契约）。

全量结果文件永远是事实源；webhook 只是通知，发失败不连累整轮（result 记 `webhook: off/sent/failed`，失败另记 `webhook_error`：HTTP 状态码或异常类名，以及 `webhook_error_detail` 脱敏诊断；都不含 URL/密钥）。这些发送诊断**只归全量结果**，不进通知投影。stderr 打印同一份诊断。通知投影只序列化一次，通知记录在首次 POST 前落盘、字节与请求体完全相同（不追加换行），所有重试与直连兜底复用同一组字节；无论 webhook 是否启用都生成通知记录。通知记录写失败仍尝试唤醒，全量结果记 `notify_record_error`（异常类名）＋stderr 诊断，不改退出码/`ok`、不递归第二条 error 通知。早期 error（非法放行 id 等未建立安全运行路径）不构造通知记录，保留既有 error 输出与发送行为。webhook POST 默认跟随本机系统代理：标准 `http_proxy`/`https_proxy` 显式配置时优先，否则读系统代理；系统例外表或 `no_proxy` 命中则直连（内网、localhost 不受影响）。选定出口对传输异常与 `HTTP 5xx` 最多试 3 次（退避 0.5s、1s），若走了代理且 3 次全败再强制 `ProxyHandler({})` 真直连兜底 1 次（`4xx` 立刻失败不重不退）。拿到 `failed` 时先看 `webhook_error_detail` 再判断，别把 DNS、TLS、代理、连接任一直接当已证实根因。短超时包装器（`hc exec` 默认 60 秒/上限 600 秒、超时不够的 bash）会到点把 dispatch 连同子进程杀掉、留下 `phase=waiting` 台账——给不了足够超时就用上面的后台路径。

## 返回契约（每轮判读）

stdout 恰好一行 JSON，同一字符串写入 `$TMPDIR/loop/runs/<放行id>.result.json`（全量结果，成功与 error 都写，逐字一致，是**最终交付**）；另写 `<放行id>.notify.json`（通知投影＋通知记录，ADR-0014）。**通知投影**：成功严格只含 13 字段 `ok`/`release_id`/`target`/`needs_user`/`questions`/`conclusion`/`blocked`/`rewritten`/`run_record`/`elapsed_s`/`next_skill`/`complete`/`complete_reason`（逐字来自本轮结果，ADR-0019 增补完整性标记）；error 保留发送前完整对象（含 `error.kind/message/hints` 与全部部分状态），不套白名单。`webhook`/`webhook_error`/`webhook_error_detail` 等发送诊断只在全量结果。调用方先读通知投影、需要细节再读全量结果。成功（退出码 0）：

```json
{"ok": true, "release_id": "<放行id>", "task_name": "loop-…", "target": "claude-matt-loop", "target_address": "t7",
 "reset": "none", "turn": 3, "status": "idle",
 "complete": true, "complete_reason": [], "anchor_found": true, "elapsed_s": 690.5, "chars": 1834,
 "run_record": "$TMPDIR/loop/runs/<放行id>.md", "raw_record": "$TMPDIR/loop/runs/<放行id>.raw.md",
 "turn_record": "$TMPDIR/loop/runs/<放行id>.turn.md",
 "prompt_sha256": "…", "rewritten": {}, "extract_backend": "api", "webhook": "off",
 "needs_user": false, "questions": [], "conclusion": "…", "blocked": "", "next_skill": "to-spec"}
```

- `run_record`（`.md`）是抽取器从本轮原料**裁剪出的干净回复全文**（剔 logo/思考计数/工具痕迹/转圈行/空行/输入框/状态栏，逐字保留正文），是回看对象；`raw_record`（`.raw.md`）是回读全文（最近 1000 行历史＋当前屏逐字读回，含被断轮切掉的旧轮，制表符按终端制表位展开为空格，审计用）；`turn_record`（`.turn.md`）是本轮原料（断轮选中的本轮段，抽取器输入＋逐字校验语料，ADR-0013）。error 时尚无干净全文，`run_record` 退回指回读全文。
- `needs_user`（布尔）：回复里是否有需用户决定的事。三个选段字段是抽取器从原文**逐字摘出**的句子（语义见 `prompts/extract.md`）：`questions` 每条问题/审批一个元素（问题＋选项＋推荐整块）、`conclusion` 结论段（无则 `""`）、`blocked` 弹框原句（无则 `""`）。干净全文 `reply` 不进本 JSON，落在 `run_record`；要展示回复内容直接用 `run_record` 原文。
- `next_skill`（字符串，ADR-0008）：抽取器**推测**的下一轮 skill 名，不带斜杠、不含参数；候选固定为 `wayfinder`/`grill-with-docs`/`to-spec`/`to-tickets`/`implement`/`implement-spec`/`prototype`，`""`＝无预测。唯一推测字段，豁免逐字校验；缺失、非字符串、带斜杠、带参数或集外值都宽容投影为 `""`，不连累整轮。调用方负责拼成 `/<next_skill> <自填参数>`。**建议不是闸**：下一轮放行卡按「预测可见性」呈出照用或编辑状态；它是起始 skill 且 turn>0 时，讨放行一并推荐 `--reset`。
- `next_skill_feedback`（ADR-0012，阶段二）：本轮主任务发送后与上一轮同 target 预测结算出的机械反馈，带上轮预测、本轮实际 skill／类型、skill 判定和上轮放行 id；正常 result 与发送后的 error JSON 都可带。`learning_warning` 只表示学习存储、权限、锁或记录损坏降级，不影响主轮退出码；`learning_excluded:"space_changed"` 表示跨 space 反馈已结算但不进入 few-shot；`learning_excluded:"incomplete_turn"` 表示上一轮带折叠标记（`complete:false`），反馈照旧结算但案例不入 few-shot（ADR-0019）。
- `rewritten`：脚本对选段字段逐一做本轮原料子串校验，搜不到的在此标出、值为最接近原文整行；`{}` 即全部逐字命中。**非空时对应字段是抽取器的改写，不可当原话引用；`reply` 被标时回看 `turn_record`（本轮原料）与 `raw_record`（回读全文）。**
- `target` 恒为**传入地址**（会话名或 t 号，寻址稳定）；`target_address`：解析出的 **t 号**（审计定位，名字恒解析到最新 live）；`turn` 是**终端级累计计数**（复活会话 turn>0 但会话是空的，ADR-0009）。
- `launched`：仅本轮 `hc run` 创建了终端才出现（`true`，创建/重建路径，error 时也带）；`rebuilt`/`rebuilt_old_target`：仅强制重建才出现（`true`＋被 kill 占位者的旧 t 号）；`workspace`：领养状态（`adopted`/`failed`，创建与既有路径都报）；`reset_skipped:"fresh_launch"`：新鲜会话跳过了重置发送（传了 `--reset` 但创建/复活/重建时）；`space`：`--space` 请求值回显（必传，成功与 error 都带，ADR-0011），`space_selected`：解析出的目标空间 id（解析成功才带），`space_landed`：pane 实际落进的空间 id，`space_target_warning`：pane 落进非目标空间告警（`landed_elsewhere`），`space_restore`：还原选中结果（`restored`/`user_switched`＝用户接管跳过/`not_needed`/`failed`），`space_restore_warning`：还原失败告警，`gift_cleanup_warning`：赠送 shell 清理失败降级告警（#46，真终端已可见、整轮仍 adopted；这些告警都不丢放行）。这些字段成功与 error 都带适用项，部分状态（终端已建/启动已发/旧会话已死/空间已建/选中已还原）如实报。
- `reset`：本轮前置类型（`none`/`new`/`clear`/`compact`）；`target_model`：仅传了 `--target-model` 才出现，本轮请求切换到的模型名（**请求值非验证值**，error 时也带，`error.model_sent` 标明切模型前置是否已发出）；`extract_backend`：本轮抽取后端（`api`/`pi`，error 时也带）；`complete`/`complete_reason`：完整性标记，只看本轮原料内的实证折叠形态（与提示词行精确相同的回声行不参与）；成功通常 `complete:true`，折叠命中 `complete:false`——抽取照常、需人复核（ADR-0019）；`anchor_found`：提示词回声是否在回读范围内——仅信息，超过 1000 行历史回声不可得时为 `false`，不影响 `complete`；`elapsed_s`：主任务发送成功到取回落定的秒数，不含前置与抽取；`webhook`：完成通知状态（鉴权走 `Authorization: Bearer`）；`webhook_error`：仅失败时出现，HTTP 状态码（如 `"HTTP 401"`）或异常类名（如 `"URLError"`）。
- `webhook_error_detail`：仅失败时出现，与 stderr 同一份脱敏诊断。字段：`type`（外层异常类名）、`reason_type`（`URLError.reason` 是异常时的类名）、`errno`/`errno_name`（可用时，如 `61`/`ECONNREFUSED`；DNS 给 `EAI_*`）、`is_timeout`（布尔）、`ssl_verify_code`（`SSLCertVerificationError.verify_code`，可用时）、`http_status`（仅 HTTPError）、`proxy`（`{"configured": "yes|no|unknown", "matches": "yes|no|unknown", "source": "env|system|bypass|none|unknown"}`：是否配置代理、本请求是否命中、代理来自环境变量还是系统配置）、`attempts`（总尝试次数），走过代理全败直连兜底仍失败时另带 `fallback_direct: true` 与 `fallback_error`（直连尝试的类名或 `"HTTP <码>"`；`webhook_error` 与主体字段保留主路由最后一次错误以对齐 `proxy`）。不含 URL、密钥、主机名、请求头或字符串形式的 reason；判断不了就标 `unknown`。
- **三个选段只摘原文片段、不保证覆盖全文**；要不要批准、要不要改接口的判断，必须回看 `run_record`，不能只凭 JSON 选段。

拿到结果整理成**选项卡**交给用户，原文不直接转给用户。失败（退出码 1；参数错误 2）返回 `{"ok": false, "release_id": "<放行id>", "target": "<终端地址>", "error": {"kind", "message", "hints"}}`（已算出 `task_name` 时一并带上）；kind 含义与调用方动作见 `REFERENCE.md`。监听只读随时可；仅报错时可直推（仍须放行）。

## 本地记录（默认 `$TMPDIR/loop`，随 OS 临时清理）

- `releases/<放行id>.txt`：放行提示词（调用方写，dispatch 原文照发）。
- `runs/<放行id>.raw.md`：回读全文（审计，含被断轮切掉的旧轮）。
- `runs/<放行id>.turn.md`：本轮原料（断轮段，抽取器输入＋逐字校验语料，`turn_record`）。
- `runs/<放行id>.md`：干净回复全文（回看对象 `run_record`）。
- `runs/<放行id>.json`：台账，先记账后发送；一个放行只跑一轮，至多发送成功三次（重置或启动前置＋切模型前置＋主任务各一次，新鲜会话跳过重置），台账存在即拒同 id 重发。台账记创建/复活/领养/重建各 phase，失败带部分状态。
- `runs/<放行id>.notify.json`：通知投影＋通知记录（ADR-0014），逐字记录实际 POST 正文（成功 13 字段／error 全量），调用方先读它；字节与请求体相同、不追加换行。
- `runs/<放行id>.result.json`：全量结果（最终交付），需要完整诊断/审计/回看时读它；重发拒绝仍以台账为准。

记录与台账都随 OS 临时清理、不持久：事后审计与拒重发保护止于重启（放行 id 每次唯一，跨重启复用风险低）。要长期留存某轮，自行另存。

## 本地学习（ADR-0012，阶段二）

学习数据与上述单轮产物分家：默认落 `${XDG_DATA_HOME:-~/.local/share}/matt-loop/learning/`（`LOOP_LEARNING_DIR` 可覆盖），目录 `0700`、文件 `0600`，90 天且每 space 最近 100 条；公开仓库不放真实案例。`LOOP_LEARNING=off` 时完全关闭学习读写、近期序列与 few-shot，原有 `next_skill` 预测照常。

脚本机械记录同 space、同 target 最近 8 个主任务的纯 skill 名序列（普通文本记 `plain_text`，不存参数正文），并保存含干净回复、预测、实际 skill 与当时序列快照的学习案例。每轮最多选 4 条同 space 案例注入既有抽取调用，历史正文总预算 16,000 字符；不新增模型调用。预测优先级为**当前回复明示＞当前近期序列＞few-shot＞空**。

使用 api 后端时，选中的同 space 历史干净回复会随抽取请求发往 `LOOP_API_BASE`；案例正文绝不进入 result/webhook。反馈按 target 配对，案例池按解析后的 space id 隔离；space 重建得到新 id 即新池，同 target 跨 space 只结算机械反馈、不共享案例。学习 I/O、权限、锁或损坏问题一律降级 `learning_warning`，不阻断主轮。机械指标与统计细节见 `REFERENCE.md` 和 ADR-0012。

## 护栏

调用方要守的四条：**先放行，后调用**；**每轮必传 `--space`**（目标空间名/id/路径，三态解析见「调用」；落点意图是显式契约，不静默按 cwd 猜）；**复用旧会话（`turn > 0`）调会话起始 skill 必须 `--reset`**（见「会话起始 skill」，`turn == 0` 全新空会话与刚创建/复活的新鲜会话自动豁免，其余由脚本对行首调用机械兜底）；**创建参数（会话名、启动命令、cwd）、显示名（每轮贴出：同任务同名、新任务换名）、目标空间（`--space`）与强制重建意图随放行一并贴出、一并授权**——kill 永远是用户的显式授权，不是脚本的自主动作。其余机械保证由脚本兜底，细节见 `REFERENCE.md`。