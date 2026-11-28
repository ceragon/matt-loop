---
status: accepted
---

# 一轮管线：`run(request, deps) → Outcome`，直线阶段加 `RunState`

> 注记（ADR-0027）：15 阶段中 `learning_prepare`、`learning_settle` 与
> `extract_verify` 的抽取/校验/注入语义退役（回读→断轮→剥回声→投递的直线不变）；
> 决定 13 的学习缝与 `deps.extract`/`Extraction` 退役；决定 10 模块布局中
> `learning.py` 移除。管线结构（`run`/`RunState`/`classify`/`gate_route` 等）
> 继续有效。详见 ADR-0027。

## 背景

评审 #53 候选①：`dispatch.py` 的 `main()` 约 570 行，把一轮的全部编排内联在一个
函数里：

- main 里编号的步骤约 16 个（1 到 7，中间还插着 1.1、2.45 这类插入步），五路分流
  （创建／复活／强制重建／既有／占位）用 if 链铺在中段。
- 状态有三套并行：十余个局部变量、`ctx` dict、`ledger` dict；台账写入 34 处，每处
  是「改 dict、再原子落盘」的三行仪式。
- 20 个 flag 的交叉不变量只有 `main` 知道：`--launch` 与 `--cwd` 成对、新鲜路径必传
  `--display-name`、`--rebuild` 只许占位分支、前置次序「重置 → 切模型 → 主任务」、
  新鲜会话免重置。
- `adopt` 约 230 行、六层嵌套，一体承担「恒可见」与「空间定位」两个不变量；它用
  `ledger=None` 暗中编码「台账还没开」的预检模式。
- 八个手写轮询循环（两个在 `adopt` 里、六个是等待目标的循环）；`extract_via_*` 与
  `main` 往 `args` 上暗渡 `learning_next_skill_invalid`、`learning_injection`。
- 阶段次序与各阶段 error kind 的先后，本身就是对调用方冻结的外部契约，重构不得改动
  其中任何一处。

地图 #71 的主干顺序是「测试缝 → herdr 领域网关 → 一轮管线」，前两步已落定：ADR-0016
立 `Deps` 注入与命令级 `FakeHerdr`，并预留「一轮管线落地后在其下加 `run(request,
deps)`，`main` 退成薄壳，测试入口不变」；ADR-0018 立 `Herdr` 网关，并把七项交接留给
本票。本 ADR 回答管线本身怎么做。ADR-0002（脚本做机械、模型做抽取）不冲突，本 ADR
是同一决议的深化。

## 决定

（grill 裁定，2026-11-25；纯内部结构，零外部契约变更——flag、error kind、
`result.json`、`notify.json` 形状与 ADR-0001…0018 均不动，台账内容与写入时序亦不变。）

1. **入口与 `Request`**：测试入口仍是 `main(argv=None, *, deps=None)`（ADR-0016 决定 1），
   `main` 退成薄壳：`parse_args` → 构造 `Request` → `run` → `emit_result` → 返回退出码。
   `Request` 是冻结 dataclass，字段是 20 个 flag 的类型化值；七个时间旋钮（`wall_clock`、
   `check_in`、`settle_seconds`、`settle_checks`、`adopt_timeout`、`adopt_poll`、
   `extract_timeout`）收成嵌套的 `Timing`。argparse 只留在 `main` 这层薄 adapter，
   只管语法层（必填、choices、正数）；`Namespace` 不进管线，`args` 上暗渡的属性在管线内
   不再存在（见决定 13）。
2. **`Outcome` 与交付**：`run(request, deps) -> Outcome`，
   `Outcome = {returncode, payload, result_path, notify_path}`。`payload` 就是现在的
   result／error 对象；受理前的早期 error 两个路径为 `None`，保住「早期 error 不建通知
   记录」。管线内部不 print、不 POST，交付由 `main` 调现有 `emit_result`。`run` 永不向外
   抛 `DispatchError`：顶层统一捕获、转成 error `Outcome`，并写台账的 `error:<kind>`
   终态。**只捕 `DispatchError`**，非预期异常照旧冒泡，与现状一致。
3. **受理（`admit`）收全部静态闸**，次序照旧：`--launch` 与 `--cwd` 成对 → 显示名机械
   校验 → `--space` 机械校验 → `check_structure_and_tools` → 抽取后端预检
   （`resolve_extract_backend`、`api_config`）→ 放行 id 正则 → 提示词读取（不可读、非
   UTF-8、全空行）→ 锚点与 sha → 路径构造 → `duplicate_release` → `split_required`。
   这些闸之间的 error kind 先后（例如 `split_required` 先于 `reset_required`）属冻结契约，
   所以放在同一处、用一张表驱动测试覆盖全部先后与双命中；静态闸不放进 CLI adapter。
   后端预检与工具结构检查夹在静态闸之间，次序同样不动，故一并进 `admit`。
4. **五路分流是纯函数**：`classify(session | None, request) -> Route`，`Route` 为
   create／revive／rebuild／existing／occupied。输入只有网关的 `Session`（ADR-0018）加
   `expect_proc` 与 `rebuild`，无 I/O、不碰 ctx；状态既非 live 也非 finished 时仍抛
   `not_idle`，`--rebuild` 误传校验与 occupied → rebuild 升格并入同一函数。`fresh` 不再是
   可变局部变量，改为派生属性 `Route.is_fresh`（create／revive／rebuild）。分流后闸由
   `gate_route(route, session, request)` 统一给，原次序逐字保留：占位报 `session_occupied`
   （四种后缀文案在此构造）→ `launch_required` → `display_name_required` → 非法会话名
   （create／rebuild）→ `rebuild_busy`。`classify` 与 `gate_route` 各用一张表驱动测试。
5. **直线 `run` 加 `RunState`，不做阶段框架**：`run` 是一条直线函数，调用具名阶段函数，
   共享一个 `RunState`，取代三套并行状态。阶段次序本身就是契约、状态高度共享，框架只会
   把次序藏进配置。分流只发生一次：由 `Route` 选「创建／复活／既有」三种前置之一，此后
   纪律预检 → 写台账 → 前置发送 → 主任务 → 等待 → 回读 → 抽取是五路共用的一条直线，不再
   if 链。15 个具名阶段，次序一步不动：
   1. `admit`（决定 3）
   2. `locate`：`require_hc`、`pi` 备选后端时的 `pi` 查找（紧跟 `hc`，`missing_tool`
      先后不变）、`session`、`classify`、`gate_route`
   3. `preflight`：revive 与 existing 先做 `ensure_visible`（台账未开），existing 再核 idle
   4. `discipline`：`reset_required` 闸，抽成纯函数
   5. `open_ledger`：核 `prompt_changed` 并写首版台账
   6. `bring_up`：由 `Route` 三选一——create／rebuild 为 [kill] → `create_session` →
      `label_session` → `await_shell` → `send_launch` → `ensure_visible` → `await_agent`
      （领养夹在启动命令与等就绪之间，保住「卡弹框也先可见」的次序）；revive 为
      `send_launch` → `await_agent`；existing 为空
   7. `name_tab`
   8. `learning_prepare`（⑤ 预留位）
   9. `send_prestep`（0 到 2 次）
   10. `send_main`
   11. `learning_settle`（⑤ 预留位）
   12. `await_result`
   13. `readback`：读取、断轮、完整性判据、落盘 raw／turn（完整性判据的内容不属本 ADR，
       #79 正在改它；本 ADR 只定它住在这个阶段、失败路径与其余阶段一致）
   14. `extract_verify`（⑤ 在此注入并记录预测）
   15. `finish`：投影出 `Outcome`

   `Herdr(deps)` 由 `run` 构造；`require_app()` 仍在 `ensure_visible`、`name_tab`、
   `learning_prepare` 三处之前先调（ADR-0018 决定 10）。
6. **重置与切模型前置合一**：一个 `send_prestep(spec)` 阶段，`spec` 由纯函数
   `prestep_specs(request, route)` 按「重置 → 切模型」产出；新鲜会话在这个纯函数里就不产出
   重置 spec，并记 `reset_skipped: fresh_launch`。「前置次序」这条不变量因此住在一个纯函数
   里，可直接表驱动测，现在两份约 15 行的孪生块消失。
7. **`adopt` 拆成纯函数加阶段**：
   - `plan_space(spec, layout) -> SpacePlan`：纯函数，结果为 reuse(id)／create(name,
     folder)／错误（`space_ambiguous`、`space_not_found`、`bad_args`）。它是地图外实施票
     `parse_space` 的匹配半边，领养与学习共用，`resolve_space_id_for_learning` 消失、
     成为它的只读投影。
   - 阶段 `ensure_visible`：读 `layout`，已有 placement 就返回（恒可见，不抢）；否则
     `plan_space` → 需要时 `create_space` → `with herdr.focus(space)`：`keep` → 核落点
     （`landed_elsewhere` 告警）→ 退出自动还原 → 若新建了空间再 `discard_shell_tabs`。两个
     不变量并不对称——pane 已在即提前返回、不看空间——所以不合成一个函数，也不平行拆成
     两个。
   - 阶段产出 `AdoptFacts`（`workspace`、`adopt_reason`、`space_*`、各 warning、
     `f.restore`），由管线在阶段边界写进 ctx 与台账。去掉 `ledger=None` 的暗中编码：阶段不知道
     台账是否已开，facts 先落 ctx，台账打开时补写。
   - 失败映射「网关 stage → `adopt_failed`／`keep_error`／`select_error`／`app_closed`／
     `no_pane`」集中成它自己的一张规则表（决定 11）。
8. **等待循环只抽两个小件**：`poll(step, *, deadline, interval, on_timeout)`（走 `Deps`
   的虚拟时钟）与 `guard(session, *, exited, error)`（把重复六次的「终端已结束／blocked →
   `dialog_blocked`／error → `target_error`」收成一处，kind 与文案由各阶段传入）。六个
   就绪判据——turn 过基线加落定复核、前台不再是 shell、屏幕静置、shell 就绪——留在六个
   具名函数里，外部 kind 逐字不变。`wait_for_result` 是事件驱动（`wait_change`），保持自己
   的循环，只复用 `guard`。
9. **传话 helper 的去留**：`send_launch`、`await_agent`（原 `send_launch_command`、
   `wait_launch_agent`）各有 create 与 revive 两个调用点，保留为阶段函数；`check_identity`
   只有一个调用点，内联进 `await_agent`，写作 `session.identity_ok(expect)` 加 raise；
   `select_and_verify`、`find_pane_space` 随网关的 `focus` 与 `Layout.placement` 消失
   （ADR-0018 已定）；`do_create` 拆成 `create_session` 与 `label_session` 两个网关调用，
   外部 kind 映射留在 `bring_up`。
10. **模块布局**：`DispatchError` 沉到底层——`HerdrFailure` 继承它而 `herdr.py` 要被
    `dispatch.py` 引用，不沉则成环。
    - `dispatch.py` 收成 CLI adapter：`parse_args`、`main`、`Deps` 默认适配器、
      `emit_result` 与 webhook、抽取后端。
    - 新增兄弟模块 `pipeline.py`：`Request`、`Timing`、`Outcome`、`RunState`、`run`、全部
      阶段、`classify`、`gate_route`、`plan_space`、`prestep_specs`。它不 import
      `dispatch`，`Deps` 靠鸭子类型、不设 Protocol。
    - 新增 `turn.py`：回读与校验的纯文本函数（`cut_turn`、`verify_*`、
      `find_session_start_trigger`、`normalize_paste`、`utf16_units`、
      `generate_short_task_name`）自 `dispatch.py` 迁入，函数体不变。
    - 新增 `errors.py`，只放 `DispatchError`。
    - 部署单元仍是 `loop/scripts/` 一个目录（`dispatch.py`、`pipeline.py`、`turn.py`、
      `errors.py`、`herdr.py`、`learning.py`），依赖方向 `dispatch → pipeline →
      {herdr, turn, learning, errors}`。
11. **失败映射不做全局大表**：同一网关操作在不同阶段映射到不同外部 kind（`send` 在启动
    命令阶段是 `launch_failed`，在前置与主任务阶段是 `send_refused` 或 `hc_failed`）。每个
    阶段就近放一张小规则表，每条规则是 `(op, stage) → (外部 kind, 文案前缀, 附加
    hints)`；全管线共用一个 `translate(failure, rules)` 拼文案，保证 `.kind`、`hints`
    与现状逐字一致。两处「原生 kind 穿透」是显式规则 `PASS`，不是漏写：`locate` 里
    `session` 抛的非 `NotFound` 失败，以及 `await_result` 里未包装的 `get`（ADR-0018 背景
    第 4 点）。每张小表配一个表驱动测试，覆盖「网关 stage → 外部 kind」。
12. **与 ④ 台账 journal 的预留缝（只预留、不设计）**：34 处台账与 ctx 写入全部收口到
    `RunState` 上三个方法：`state.phase(name, **fields)`（写台账 phase 与字段、原子落盘）、
    `state.fact(key, value, *, ledger=True)`（写 ctx，可选镜像进台账）、
    `state.project(ok) -> payload`（把 ctx 投影成成功或 error 的 payload）。main 里现有两份
    20 键清单（成功与 error 略有差异）逐字搬进 `project` 一处；error 路径额外带的键
    （`task_name`、`extract_backend`、`reset` / `reset_sent`、`target_model` /
    `model_sent`、`elapsed_s`、`run_record`）也归 `project`，值从 `RunState` 取，不再是
    `main` 里的局部变量。台账未开时 `phase` 与 `fact` 先缓冲，`open_ledger` 时补写。本 ADR
    只定三个方法的存在与语义，方法体先是现有 dict 加 `write_json_atomic` 的薄包装，行为一字
    不变；④ 以后换掉方法体，管线调用点不动。会失败的阶段（`ensure_visible`、`bring_up`）
    用 `try/finally` 在阶段出口调 `state.fact`，所以 `f.restore` 在成功与失败时都进 ctx 与
    台账——ADR-0018 交接的「`focus` 退出结局如何写进 ctx 与台账」在此一并定了。

    > 补记（ADR-0022，#83）：三个方法的存在与调用点不变，其真正实现已由 ADR-0022 定写：
    > 台账文件独立成 `Journal`（`RunState` 持有），`project` 留在 `RunState`。本决定与
    > 决定 7 里「台账未开时先缓冲、`open_ledger` 时补写」改为不做通用缓冲——facts 即预检期
    > 缓冲，首版台账只取 `target_address` 与 `workspace`（现行行为）。「两份 20 键清单」
    > 订正为成功 19 键、error 21 键（error 多 `launch_sent`、`adopt_reason`）。
13. **与 ⑤ 学习 session 的预留缝（只堵两处）**：
    - `learning.enabled(env)` 加一个参数、默认 `os.environ`，只改这一个函数签名；`admit` 里
      调一次 `learning.enabled(deps.env)`，结果放进 `RunState`。
    - 抽取不再往 `args` 暗渡属性：`deps.extract(request, turn_path, injection) ->
      Extraction`，`injection` 是显式入参取代 `args.learning_injection`，`Extraction` 带
      `fields`、`reply`、`next_skill_invalid` 取代 `args.learning_next_skill_invalid`；不改
      外部契约。
    - 管线在 `learning_prepare`、`learning_settle`、`extract_verify` 的注入处与记录预测处
      共四个点通过 `state.learning` 调用；关闭时它是空对象，调用点不写 `if learning_on`。
      `LearningStore` 内部、`begin` / `after_extract` / `finish` 的形状、空对象本身的形状
      仍归 ⑤。

    > 补记（ADR-0023，#84）：这些形状已由 ADR-0023 定写——`state.learning` 是四个动词
    > （`begin(resolve_space)` / `settle(first_line)` / `inject()` / `record(extraction,
    > complete)`），不设 `finish`；会话持有 `state.fact`，动词不返回 feedback / warning；
    > 关闭时的空对象是独立的 `Disabled` 类；`learning_prepare` 的 space id 解析走管线构造的
    > 回调，`resolve_space_id_for_learning` 退役。四个调用点的位置与 `Extraction` 不变。
14. **外部契约零变化靠特征化快照守，并成为实施前置**：在抽管线**之前**先加一套特征化快照
    测试，建在 `harness.run` 加 `FakeHerdr` 上：五路分流各跑成功路径与若干关键故障点；
    记录 stdout JSON、`result.json`、`notify.json`、台账终态与 phase 序列；时间戳与路径归一化
    后，抽取前后须逐字相等。因此「抽管线」排在「`Deps` 与 harness、`FakeHerdr`、快照落地」
    之后（也在网关之后，因为管线建立在网关之上），这是实施切片的硬前置；具体怎么切成实施票
    不在本 ADR。

## 与 ADR-0016、ADR-0018 的关系

两份 ADR 的判断一概不推翻：

- ADR-0016 决定 1 预留的 `run(request, deps)` 由本 ADR 定形；其决定 3 里 `extract` 字段的
  签名改为显式（决定 13）；决定 7 末尾「待 ⑤ 收口」的环境变量洞部分收口（决定 13）。
- ADR-0018 决定 1 里「`main` 内部用 `Herdr(deps)` 构造」改为由 `run` 构造（决定 5）。

ADR-0018「交接：一轮管线」七项均已关闭：

| 交接项 | 落点 |
|---|---|
| 领养策略 | 决定 7 |
| 等待循环如何归一，及各自夹带的外部 kind | 决定 8 |
| 网关失败 → 外部 kind 映射 | 决定 11 |
| `f.restore` 如何写进 ctx 与台账 | 决定 12 |
| 台账与 ctx 的写入点 | 决定 12 |
| `learning.enabled()` 环境变量洞 | 决定 13（部分收口） |
| `--space` 三态解析与 `parse_space` 复用 | 决定 7 |

## Considered Options

- **阶段框架或状态机**（阶段列表、`Stage` 对象、转移表）：否——阶段次序本身就是契约、
  状态高度共享，框架只会把次序藏进配置，读代码的人反而要先读框架。
- **`run` 自己交付（内部 print 与 POST、只返回退出码）**：否——交付（`emit_result`）是候选 ④
  要收敛的对象，且返回 `Outcome` 让 `run` 级测试可以不碰 stdout、文件与 webhook。
- **管线留在 `dispatch.py` 内**：否——省了迁移，但 `dispatch.py` 仍是两千多行的文件，
  deep module 在文件层不可见；`DispatchError` 沉底后依赖方向也更干净。
- **全局失败映射大表**：否——同一网关操作在不同阶段映射到不同外部 kind，大表要么带阶段
  维度而退化成每处一张表的合订本，要么错映射。
- **通用 `await_session(policy)` 大循环**：否——六个就绪判据差别大，策略对象的参数面
  会与六个函数体一样复杂（浅模块）；只抽重复的 `poll` 与 `guard`。
- **两个 adopt 不变量合并成一个函数（现状）或平行拆成两个**：否——它们不对称：pane 已在
  即提前返回，空间定位只在需要 `keep` 时才有意义；拆成纯函数 `plan_space` 加阶段
  `ensure_visible` 才贴合这条依赖。

## Consequences

- 测试入口不变：既有 `main(argv)` 级测试与 `harness.run` 不受影响；管线级测试可直接调
  `run(request, deps)`，网关与 `plan_space`、`classify`、`prestep_specs` 等纯函数各有自己
  的表驱动测试。
- 已接受的代价：多出三个新模块文件（`pipeline.py`、`turn.py`、`errors.py`），`dispatch.py`
  与 `turn.py` 之间有一次纯迁移（函数体不变，只改 import 路径）。
- `RunState` 的三个方法暂是薄包装，journal 的真正收益要等 ④ 落地才兑现；本 ADR 只保证
  调用点不会再散。
- 抽管线必须在特征化快照基线建立之后，实施顺序见决定 14，具体切片留给后续的实施切片票。
- ④（台账 journal）、⑤（学习 session）、⑦（契约单一事实源）仍在地图里，各自的形状不在
  本 ADR。
