---
status: accepted
---

# 台账 journal：台账文件独立成 `Journal`，结果投影留在 `RunState`

> 注记（ADR-0027）：决定 6 的 `next_skill_feedback`/`learning_warning` 两键接缝
> 随学习线退役，决定 4 的键表随之收缩（成功/error 键清单由实施切片重述）；
> `Journal` 本身与其余决定不动。详见 ADR-0027。

> 补记（#113 ④journal 实施前提订正，team-lead 批准；**前提订正**，非新决定）：
> 决定 2「现行代码里这种调用本不存在」属前提笔误——预检期领养（existing/revive
> 分流）在台账 `open` 前确实调用 `state.phase("adopting")` / `state.phase("adopt_done")`，
> 只是被基线 `ledger=None` 的 no-op 守卫吞掉。编程错误的 raise 落在 `Journal.phase`
> 层（open 前调用抛 `RuntimeError`，Journal 单测钉死）；`RunState.phase` 保留 open 前
> no-op 守卫、open 后委托 `Journal.phase`，以守住基线预检期零写入行为与「管线调用点
> 不动」。接口与外部契约零变化。

## 背景

评审 #53 候选④：台账（`runs/<放行id>.json`）的写入散在 `main()` 与 `do_send`、
`do_create`、`adopt` 等辅助函数里，约 35 处「改 dict、再 `write_json_atomic`」。ADR-0020
决定 12 把这些写入与 ctx 写入收口到 `RunState` 上三个方法——`state.phase(name, **fields)`、
`state.fact(key, value, *, ledger=True)`、`state.project(ok)`——并明确「只预留、不设计」：
方法体先是现有 dict 加原子落盘的薄包装，真正的实现与 interface 留给本票。

本 ADR 回答六件事：这三个方法是不是最终 interface；台账未开时的写入怎么办；写失败各归谁；
成功与 error 两份键清单如何归一；「台账内容与写入时序不变」如何证明；与 ⑤ 学习 session
的接缝。

核对现行代码得到几条事实，决定都建立在它们之上：

1. 台账只在「发送前再核文件未变」之后才首次落盘；此前整轮零落盘，故放行 id 直到那一刻才
   消耗（`duplicate_release` 只看台账与回读原料是否存在）。
2. 预检期的领养（既有路径、复活路径）在台账未开时只写 ctx：首版台账只带 `target_address`
   与 `workspace` 两个领养产物，`space_created`、`space_selected`、`pane_adopted`、
   `space_landed`、`space_restore`、`gift_cleanup_warning` 等都不进台账。
3. ctx 与台账是两个键空间，只部分重叠：`next_skill_feedback`、`learning_warning` 只进 ctx；
   `launch_send`、各 `*_done_at`、`sent_at` 等只进台账。error 载荷还会从台账读回
   `run_record`、`complete`、`complete_reason`。
4. 台账中途写失败（`OSError`）不是 `DispatchError`，会冒出 `main`，没有 result 也没有
   notify；只有终态 `error:<kind>` 那次写入吞 `OSError`。
5. 两份手抄的 ctx 键清单：成功 19 键、error 21 键（多 `launch_sent`、`adopt_reason`），
   其余键相对次序相同。ADR-0020 决定 12 写的「20 键」是不精确的概数。
6. 约 5 处 `ledger.update` 只改内存、延后到下一次写才落盘：领养失败时的 `adopt_reason`，
   领养里的 `pane_adopted` / `space_landed` / `space_target_warning`，抽取后的
   `rewritten` / `run_record` / `reply_chars`。

ADR-0002（脚本做机械）与 ADR-0014（通知投影）不冲突；ADR-0020 的 15 阶段与 `RunState`
三方法的调用点不动。

## 决定

（grill 裁定，2026-11-25；纯内部结构，零外部契约变更——flag、error kind、`result.json`、
`notify.json` 形状与台账终态内容均不动。唯一的有意偏离见决定 5。）

1. **拆成两个模块，调用点不变**：
   - 新增兄弟模块 `journal.py`，内含 `Journal`，**只管台账文件**。interface 五个方法：
     `open(initial)`、`phase(name, **fields)`、`fact(key, value)`、`get(key, default=None)`、
     `fail(kind, message)`。不依赖 herdr，也不认识学习与结果载荷。
   - `RunState` 持有 `Journal`，并保留 ADR-0020 的三个方法作为管线的**唯一调用点**：
     `state.phase` 委托 `journal.phase`；`state.fact(key, value, *, ledger=True)` 写自己的
     facts（即 ctx），`ledger=True` 时再委托 `journal.fact`；`state.project(ok)` 是纯函数式
     投影，不进 `Journal`。管线各阶段的调用点不因本 ADR 改动。
   - 删除测试：删掉 `Journal`，约 35 处原子落盘与「台账存在即拒同 id 重发」的不变量会重新
     散回各阶段，故它不是转发壳；`project` 则不属于台账，并入 `Journal` 只会让一个模块同时
     懂「磁盘上的过程记录」与「交付给调用方的结果形状」。
2. **台账未开时不做通用缓冲**：
   - `RunState` 的 facts 本身就是预检期的缓冲。`phase()` 在 `open` 之前调用是编程错误，
     直接抛异常（不是 `DispatchError`）；现行代码里这种调用本不存在，因为 `ledger=None`
     时 phase 根本不写。
   - `open` 之前的 `fact` 只进 facts、不写盘。
   - 首版台账快照由 `open_ledger` 阶段显式取 `target_address` 与 `workspace`，与现行一致；
     其余预检期领养产物只进 ctx。
   - `Journal.open` 是消耗放行 id 的唯一动作：此前整轮零落盘（对齐「预检失败零写入」），
     `runs` 目录也在 `open` 里建。`open` 只调一次。
3. **写失败三块各归其位**：
   - 台账中途写失败：`phase` 与 `fact` 原样让 `OSError` 冒出，行为冻结。
   - 终态写失败：`Journal.fail` 内部吞 `OSError`；它是 `Journal` 里唯一的尽力而为写，
     `run` 顶层捕获 `DispatchError` 后调它写 `error:<kind>` 终态。
   - 交付层写失败（通知记录写失败记 `notify_record_error`、结果文件写失败只打 stderr、
     webhook、stdout）留在 `emit_result`，不进 `Journal`。
   - 台账中途写失败让调用方什么都收不到，是外部契约上的洞；按地图硬约束 1 不在本图里
     修，另开独立 issue 登记。
     > 补记（ADR-0024，#111）：该洞已经独立 issue 修复——`OSError` 仍原样冒出至 run
     > 顶层、由顶层映射 `io_failed` 走同一 error 出口；`phase`/`fact` 行为冻结口径不变，
     > `Journal` 设计零改动，journal 切片继承修后行为。
4. **投影：一张有序键表，两个手写构造器**：
   - `project` 用一张有序键表覆盖 ctx 键那一段，共 21 个键，其中 `launch_sent` 与
     `adopt_reason` 标记为「仅 error」；成功投影过滤这两个键（即便它们在 ctx 里）。成功
     键序是 error 键序去掉这两个后的子序列，输出键序（快照逐字比对会碰）与现状逐字一致。
   - 成功与 error 的固定头部不同，各自手写；不合并成一个构造器。
   - `NOTIFY_SUCCESS_FIELDS` 白名单不并入这张表，`Journal` 对它无感。依赖方向是 ctx 键表 →
     结果载荷 → 通知白名单，通知投影仍只吃载荷（ADR-0014 不动）。
   - error 载荷多带的 `run_record`、`complete`、`complete_reason` 经 `Journal.get` 读，不另存
     一份；`complete` 的判据是「键存在」而非真值（`False` 是合法值），所以 `get` 带 `default`
     参数，`project` 用哨兵区分缺键，台账未开时 `get` 恒返回 `default`。
5. **「内容与时序不变」靠快照加 `Journal` 单测证明**：
   - 特征化快照（ADR-0020 决定 14）覆盖终态台账（时间戳归一化后逐字比对）；`FakeHerdr`
     每收到一条命令，harness 读一次磁盘上的台账并记下，由此照出「先记账后发送」的次序，
     不给 `Deps` 加字段。
   - `Journal` 自己的表驱动单测钉死：每次 `phase` / `fact` 调用是一次原子落盘；`fail` 吞
     `OSError`、`phase` 不吞；`open` 之前零写入；`phase` 在 `open` 之前抛异常。
   - 事实 6 的约 5 处延后落盘，收进 `Journal` 后统一改为立即落盘，作为**有意的小偏离**：
     只改变中间态，不改变终态与 phase 次序，台账是崩溃取证用的，早落盘只多信息。落地片的
     PR 须按 ADR-0021 决定 5 显式列出对应的快照 diff，写明「有意的契约变化」。
6. **与 ⑤ 的接缝只定接收端**：
   - `next_skill_feedback` 与 `learning_warning` 写作 `state.fact(key, value, ledger=False)`，
     进 ctx、不进台账。
   - 后写覆盖先写，不合并、不累积；`learning_warning` 现行有四处写入点、前者被后者覆盖，
     这是现行行为，冻结。
   - 这两个键在决定 4 的键表里，成功与 error 路径都由 `project` 带出。
   - `state.learning` 与 `Journal` 平级，都由 `RunState` 持有、互不引用；`Journal` 对学习
     完全无感，接缝只有 `fact` 一个口。谁来调 `fact`（管线拿返回值后调，还是学习对象内部
     持写入口）归 ⑤ 的设计票。
     > 补记（ADR-0023，#84）：由学习会话内部持写入口——会话构造时拿到 `state.fact`，动词
     > 内部写 `fact(..., ledger=False)`，不返回结果；`Journal` 一侧无需任何改动。
7. **时间戳**：`Journal.fail` 自己盖 `failed_at`；其余 `*_at`（`created_at`、`sent_at`、
   `read_at`、各 `*_done_at`）仍由调用方作为字段传入，`Journal` 不猜字段名。`Journal`
   构造时从 `Deps` 取墙钟 `now`，不直接引用时间；若现有 `Deps` 的时钟只有单调钟与 sleep，
   落地片补一个墙钟字段。
8. **GLOSSARY 新增词条「台账」**：只写领域语义（先记账后发送、存在即拒同放行重发、记过程
   而非交付）；`Journal` 这个代码名不进词表，沿用 ADR-0016 决定 13 的口径。

## 与既有 ADR 的关系

- **ADR-0020**：决定 12 的「`phase` 与 `fact` 先缓冲、`open_ledger` 时补写」由决定 2 修正
  为「不做通用缓冲」，三个方法的存在与调用点不变；「两份 20 键清单」订正为成功 19 键、
  error 21 键。决定 7 里「facts 先落 ctx，台账打开时补写」同样只指首版快照那两个键。
  ADR-0020 顶部已补记。
- **ADR-0021**：④ 的落地仍挂在 `P2` 之后，只换 `RunState` 三个方法的方法体并新增
  `journal.py`；`S` 快照片须带「`FakeHerdr` 每收一条命令读一次磁盘台账」。ADR-0021 已补记。
- **ADR-0014**：通知投影与通知记录的契约不动，边界见决定 4。

## Considered Options

- **通用缓冲（台账未开时 `phase` / `fact` 先缓冲，`open` 时全量补写）**：否——预检路径的
  首版台账会凭空多出 `space_*`、`pane_adopted` 等键，违反「台账内容不变」；ctx 本来就是缓冲。
- **`Journal` 吞所有写失败**：否——会把现行「中途写失败冒出」改成静默，是行为变化；该洞
  另开 issue 处置，不在重构里顺手改。
- **投影并入 `Journal`（journal 接管三个方法）**：否——台账与结果载荷是两件事，合并后一个
  模块同时承载两种变化原因；拆开后 `Journal` 只因「台账文件形态」变，投影只因「载荷形状」变。
- **一个构造器加参数区分成功与 error**：否——两份固定头部与尾部差异不小，合并只会把差异
  藏进条件分支；共用的只有 ctx 键表。
- **保留约 5 处延后落盘（`fact(..., flush=False)`）**：否——为一个没人依赖的偶然行为增加
  旋钮；改为立即落盘只改中间态，PR 里列 diff 即可。
- **给 `Deps` 加台账写入函数以观察落盘序列**：否——`FakeHerdr` 每收一条命令读一次磁盘台账
  已足以照出次序，且不增加 `Deps` 的稳定面。

## Consequences

- 台账写入收口到 `journal.py` 一处，约 35 处落盘不再散在各阶段；换台账形态时调用点不动。
- 代价：多一个兄弟模块文件；约 5 处延后落盘变为立即落盘，中间态与现状不同（终态与次序
  不变）。
- 台账中途写失败的行为洞被如实保留，转为独立 issue，不阻塞本重构。
- 新增台账词条；`journal.py` 须过 ADR-0021 的部署冒烟门。
- 隐私：本 ADR 与相关快照样例同样遵守公开仓库红线。
