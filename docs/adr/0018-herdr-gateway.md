---
status: accepted
---

# herdr 领域网关：封装命令字面量，seam 不动

> 补记（#113 G3 在途裁决，用户批准）：`focus` 与 `discard_shell_tabs` 增加**加性**
> 参数，默认行为不变：
> - `focus(space, *, original=<sentinel>)`：调用方若已为策略/复核读过当前选中
>   （基线 `adopt` 的 A/D 句），传入即跳过 `__enter__` 内部那次 `selected_space()`
>   （一条 `app status`），直接 select＋复核；该值同时作还原目标。不传（哨兵）时
>   内部自读，行为与既有完全一致。`original=None` 是合法值（无选中空间），故用
>   哨兵区分「未传」与「传 None」。进入复核不符时，还原结局也挂到 `cm.restore`
>   （此前只还原不保留结局），供管线按现行落点记账。
> - `keep()` 的回执 `Placement` 带上落地那次 `Layout`；`discard_shell_tabs(...,
>   *, layout=None)` 传入即复用该树、不再内部自读 `ls`，不传时行为不变。
> - `discard_shell_tabs` 改为**生成器**（原返回列表）：每条 warning 在其发生点
>   （该条失败命令之后、下一条清理命令之前）yield，管线在 for 循环体里逐条
>   `gift_warning` 立即落盘再推进。基线 `gift_warning` 是「每条告警立即写 ctx＋
>   台账＋原子落盘」，与 `tab.close`/轮询/`rm` 命令交错；整体返回后统一补写会
>   延后写点（同一 tab 多地址逐条 rm 失败、多 tab 清理的中间态告警会丢取证）。
>   生成器让写点回到每条告警的发生点，网关仍对台账失明（决定 7）。消费形态
>   （for 循环逐条落盘）是契约的一部分，P3/journal 不得改回列表。
>
> 动因：命令序列与台账写入时序是冻结契约。网关内部自读会让 `adopt` 多发命令，
> 且把基线「读 original（D 句）→ 写 `space_selected`/`phase=space_selecting` →
> select」之间那条台账写点的缝吞掉；`discard_shell_tabs` 再自读一棵树也会多一条
> `app ls`（基线复用 keep 落地轮询的 `ls2`）。这些都被 S 快照与既有 main 级测试
> 逐字钉死。加性参数让管线把早先读到的事实传回网关，网关的「恒还原」护栏与
> 领域语义不变，外部 kind/文案/台账/命令序列逐字保留。用户已批准（#113 G3 在途
> 裁决）。

> 补记（#113 G2 在途裁决）：决定 5 把「`--if idle` 被拒且 done 才补发一次」收进
> `send`，与决定 7「台账内容与写入时序不变」冲突——基线 `do_send` 在两次 `hc get`
> 复核之间落盘 `phase="resending"`＋`first_refusal`，单操作收编必吞这个中间态。
> 按决定 8「宁可多一个操作，不在单个操作内部吞掉一个台账 phase」，拆成两个操作：
> `send(target, text, guard)` 到「被拒且第一次复核为 done」即返回回执
> （`first_refusal` 非空、`raw` 为空），**不在操作内部补发**；`resend(target, text)`
> 做第二次复核（非 live 或非 idle/done 抛 `StatusMismatch`，
> stage=`refused_before_resend`）＋裸 send。台账写入由管线在两个操作之间完成，
> 外部 kind、文案、hints、`first_refusal` 内容逐字不变。用户已批准此新决定
> （#113 在途裁决）。Considered Options 中「按决定 8 切分后没有哪个台账写入需要
> 发生在单个操作内部」的前提被本次实施证伪，予以订正。

> 补记（ADR-0020，#75）：决定 1 里「`main` 内部用 `Herdr(deps)` 构造」改为由 `run`
> 构造（`main` 退成薄壳，不碰网关）；「交接：一轮管线」七项均已关闭，落点见 ADR-0020
> 决定 7（领养策略、`parse_space` 复用）、决定 8（等待循环）、决定 11（失败到外部 kind
> 的映射）、决定 12（`f.restore` 与台账、ctx 写入点）、决定 13（`learning.enabled()`
> 环境变量洞，部分收口）。网关本身的决定不变。

## 背景

评审 #53 候选③：`dispatch.py` 对 herdr 的依赖停在进程层的细节上。`hc` 与
`herdr-app` 的命令字面量（argv、`do` 命令的 JSON、返回 dict 的键、`status` 语义）散在
约 35 个调用点；同一棵 app 树被遍历四遍（`find_pane`、`find_pane_space`、
`find_tab`，外加 `adopt` 里内联的找空间）；`hc_json` / `app_json` 实现体与
`HcFailure` / `AppFailure` 失败类各复制了一份；`hc` 的 `NotFound` 在五处含义各不相同
（分流＝去创建、等 shell 就绪＝继续等、等 agent 就绪＝`launch_failed`、领养清理＝
已清完、`rm` 时＝视同清完）。

地图 #71 的主干顺序是「测试缝 → herdr 领域网关 → 一轮管线」，测试缝已由 ADR-0016
落定：herdr 一缝停在命令级（`Deps.hc_json` / `Deps.app_json`，fake 说 dict、不说 argv）。
ADR-0016 同时预判「网关定型后这一层会被上移」，把 `Deps.hc_json` / `app_json`、
`FakeHerdr` 的 argv 级入口、契约一致性检查器标为**临时**，并要求网关票重命名故障键。
本 ADR 回答网关到底怎么做，并推翻该预判。

输入事实（研究票 #72 的脱敏结论，采集于 hc 0.1.22 / herdr-app 0.1.18）：

1. 契约不对称：hc 有 OpenRPC，herdr-app 只有入参文档、无返回值与错误 schema；两者
   错误信封不同（hc 为 `{"error":{"kind",…}}`，herdr-app 为 `{"error":"<字符串>"}`）。
2. 两端的 `status` 形状不同（对象 / 字符串或 null），「无 pane」到底是 `pane:null` 还是
   条目被隐藏未实测。
3. `terminal.keep` 的 pane 落地与 `tab.close` 的终端结束都是异步的：命令返回成功
   不算数，须带超时轮询复核。
4. 现有行为里，hc 的原生 error kind 会**穿透**成外部 `error.kind`（分流 `hc get`
   非 `not_found` 时直接 `raise`；`wait_for_result` 里的 `hc get` 未包装）；
   `find_app_bin()` 在 `adopt`、显示名落地、学习解析三处作为实参先求值，所以
   herdr-app 缺失报 `missing_tool`，而不是被 `adopt` 里的 `except DispatchError`
   吞成 `adopt_failed(app_closed)`。二者都是冻结契约。

## 决定

（grill 裁定，2026-11-25；纯内部结构，零外部契约变更——flag、error kind、
`result.json`、`notify.json` 形状与 ADR-0001…0017 均不动。）

1. **封装式，seam 不动**：新增 `Herdr` 网关 module，interface 全用领域词；命令字面量、
   返回解析、落地轮询复核、错误映射都在其实现内。seam 仍在命令级——`Deps.hc_json` /
   `Deps.app_json` 不变，`FakeHerdr` 不动；`main` 内部用 `Herdr(deps)` 构造，不增
   `Deps` 字段。
2. **一个对象，两簇操作**（会话簇对应 hc，空间簇对应 herdr-app）；因为跨簇操作（领养
   清理赠送 shell 既要 `tab.close` 又要 `hc rm`）要看同一个世界视图。新建兄弟模块
   `loop/scripts/herdr.py`（沿 `learning.py` 先例，部署单元不变），对外只暴露
   `Herdr`；内部是否按簇拆文件由实施决定，对外不可见。
3. **读模型类型化，容错吸收在解析处**：`Session`（地址、live/finished、`status`、
   `turn`、`foreground`、`labels`，及 `is_shell()`、`identity_ok(expect)` 等派生判断）
   与 `Layout`（一次解析整棵 app 树，`placement(address)` 给空间/标签页/pane，四份遍历
   归一）。都带 `raw`，供台账逐字记录。`pane` 为空与「条目隐藏」统一为「无 placement」。
4. **判别规则**：验证**某一条命令自己效果**的轮询进网关（`terminal.keep` 加轮询 `ls`
   等落地；`tab.close` 加轮询 `hc get` 等 finished 再 `rm`）；等**目标 agent 行为**的
   循环留在管线。领养取「机械下沉」：机械步骤进网关，策略留管线（要不要领养、
   `--space` 三态选哪个、零匹配是否建空间、赠送清理何时触发、失败映射 `adopt_failed`）。
5. **操作清单**：
   - 会话簇：`session(target)`、`create_session(name, cwd)`、
     `label_session(target, labels)`、`send(target, text, guard)→Receipt`（把「`--if idle`
     被拒且 `hc get` 为 done 才补发一次」收进去，回执带 `resent` 与首次拒绝）、
     `wait_change(target, timeout)`、`read(target, lines)`、`kill(target)`。
   - 空间簇：`layout()`、`selected_space()`、`create_space(name, folder)→id`、
     `focus(space)`、`keep(address, within, poll)→Placement`、`rename_tab(address, name)`、
     `discard_shell_tabs(space, keep_address)→warnings`（`rm` 是其内部细节）。
   - 预检：`require_hc()`、`require_app()`。
6. **`focus` 是上下文管理器**：`with herdr.focus(space) as f:`——进入时 select 并核对，
   核对不符先还原再抛错；退出时（含异常退出）无条件按护栏还原（用户已切走则不抢），
   结局 `restored / user_switched / not_needed / failed(+warning)` 挂在 `f.restore` 上，
   退出后可读。「恒还原」是这个操作的价值，不交给调用方写 `finally`。
7. **网关对台账与 ctx 失明**：操作不收 ledger / ctx / path 参数；成功返回事实，失败的
   异常带部分事实；台账与 ctx 的写入全由管线在操作边界做。台账内容与写入时序不变。
8. **操作切分服从台账 phase 边界**：宁可多一个操作，不在单个操作内部吞掉一个台账
   phase（例：`create_session` 与 `label_session` 分开，因为两者之间写 `phase`）。
9. **失败模型**：单一基类 `HerdrFailure`，**继承 `DispatchError`**，携带
   `op`、`source`（hc/app）、`native_kind`、`message`、`hints`、`detail`、
   `partial_facts`；进程层失败（`tool_timeout`、`hc_output`、`app_output`）也归它，
   `.kind`、退出码、`hints` 逐字不变，hc 原生 kind 的穿透保持。只对调用方真会分支的
   三种建子类：`NotFound`、`StatusMismatch`、`Blocked`。`session(target)` 找不到时
   **抛 `NotFound`，不返回 `None`**。失败还带领域名的 `stage`，让管线映射到不同的外部
   kind：`keep` 区分「命令失败」「轮询读失败」「窗口耗尽（`NoPane`）」；`rename_tab`
   区分 `no_tab` / `rename_error` / `rename_mismatch`；`discard_shell_tabs` 的 warnings
   带 `tab_close_error` / `finish_timeout` / `finish_poll_error` / `rm_error`。
   **外部 kind 的映射全留管线**，网关不产生 `launch_failed`、`adopt_failed`、
   `keep_error`、`app_closed` 等外部 kind。
10. **可用性预检**：`require_app()` 缺失即抛 `missing_tool`（不经 `HerdrFailure`），
    管线在 `adopt`、显示名落地、学习解析三处之前先调，保住 `missing_tool` 不被吞成
    `app_closed`；`hc` 缺失仍在预检之前报。
11. **六个等待循环不进网关**（`wait_for_result`、`settle`、`reset_settle`、
    `wait_for_reset`、`wait_for_shell_ready`、`wait_for_agent_ready`）：每个都夹着阶段
    专属的外部 kind，编码的是 loop 的等待策略而非 herdr 语义。网关只给 `session` /
    `wait_change` / `read` 三个原语。
12. **测试与护栏**：
    - 网关有**自己的 interface 级测试**（`tests/` 里用 `Herdr` 加 `FakeHerdr`），集中
      覆盖四件事：焦点还原、`keep` 落地轮询、`send` 补发、赠送 shell 清理；`main` 级测试
      只管管线。
    - **棘轮守卫**：`dispatch.py` 不得出现 herdr 命令字面量，白名单列尚未迁移的函数，
      只许缩小，终态为空。
    - **故障键守卫**：`FakeHerdr` 故障/延迟声明里的命令名必须是 herdr 契约里真实存在的
      命令（取自 `hc schema` 与 `herdr-app commands` 的 fixtures），拼错在构造阶段报错，
      不静默不触发。
13. **三项未实测假设的处置**（ADR-0016 交接给本票）：
    (a) `pane:null` 与「条目隐藏」被 `Layout` 吸收，行为不再依赖该假设；解析测试用两种
    形态各钉一次（fixtures 手写），`FakeHerdr` 现有的 `pane:null` 输出保留。
    (b) `space.new` 的真实返回：生产代码已依赖键 `space`，且 #43、#46 的真实运行跑通
    过；假设隔离在 `create_space→id` 一处，键缺失抛 `HerdrFailure`；实施票加一个可跳过
    的 HITL 步骤，由人手动采一份脱敏样例进 fixtures，采不到不阻塞。
    (c) app 未开时的错误 JSON：行为不依赖具体内容——领养里 `status` 或 `ls` 的任何失败都
    映射 `app_closed`；网关只需保证这类失败抛 `HerdrFailure`，解析测试沿用研究票已有
    的信封。
14. **术语**：GLOSSARY 补三条领域词——标签页、选中空间、赠送 shell。代码名（`Herdr`、
    `Session`、`Layout`、`Placement`、`Receipt`、`focus`）不进词表。地图与票面里把
    herdr 的 tab 写作「选项卡」属撞词（GLOSSARY 的**选项卡**是调用方呈现给用户的
    Option Card），一律更正为「标签页」。

## 对 ADR-0016 的修订

ADR-0016「网关之后：稳定与临时」一节的判断被推翻：

- **`Deps.hc_json` / `Deps.app_json`** 两个字段：由「临时」改为**稳定**，不换成一个
  领域级字段。
- **`FakeHerdr` 的 argv 级两个入口**（`.hc_json` / `.app_json`）：由「临时」改为**稳定**。
- **契约一致性检查器与 `hc_json` / `app_json` 解析测试**：由「下沉成网关生产适配器
  自己的测试」改为**留在 `FakeHerdr` 一侧**，仍是稳定资产。
- **故障与延迟声明里的命令名键**：不改成领域操作名；交接项「重命名故障键」**撤销**。
  命令名本来就是 herdr 契约词汇，fake 模拟的是 herdr 本身，网关操作名（`focus`、
  `keep`）只是封装，fake 在它下面拦不到。
- 交接项里的三项未实测假设已在决定 13 处置；`learning.enabled()` 的环境变量洞仍归
  「一轮管线」票。

理由：herdr 目前只有一种真实后端，网关 interface 上的领域级 fake 是人造的第二个
adapter（「一个 adapter 是假想的 seam，两个才是真的」）；而命令级 seam 已有两个
adapter（真进程与 `FakeHerdr`），并经 ADR-0016 验证。把 seam 上移到网关要付两层 fake
（领域级 fake 加一个命令级仿真，用来测生产适配器里的落地轮询与还原复核）与一轮全量
测试入口迁移，换来的「与 herdr 命令方言解耦」收益，只在 herdr 契约频繁变化或出现第二个
后端时才兑现。

**演进路径（B 上移式）**：将来出现第二个后端时，把网关 interface 提为 `Deps` 的领域级
字段，`FakeHerdr` 加领域级入口（状态机不重写），`hc_json` / `app_json` 降为生产适配器
内部 seam，解析测试与一致性检查器随之下沉。A 不关上这扇门——seam 位置没变，封装只是
先把命令字面量收进一个 module。

## 交接：一轮管线（候选①）

- 领养**策略**那一步（见决定 4）。
- 六个等待循环如何归一，及各自夹带的外部 kind（`target_exited`、`not_started`、
  `launch_failed`、`dialog_blocked` 等）。
- 全部「网关失败 → 外部 kind」映射表（按阶段，用决定 9 的 `stage`）。
- `focus` 的 `f.restore` 如何在 `finally` 里写进 ctx 与台账。
- 台账与 ctx 的写入点全部落在网关操作边界（决定 7、8）。
- `learning.enabled()` 的环境变量洞（ADR-0016 遗留，待⑤收口）。
- `--space` 三态解析：地图之外的实施票 #70 抽 `parse_space` 纯函数，与学习解析、
  领养的三态匹配共用。

## Considered Options

- **seam 上移到网关（B）**：见上，人造的第二个 adapter 加两层 fake。否，留作演进路径。
- **领养取薄（网关只包单条命令）**：否——是改名的浅模块，interface 与实现一样复杂。
- **整个 `adopt` 下沉到网关**：否——把七八个领养专属 error kind、ctx / ledger 词汇与
  `--space` 三态解析全拖进网关，网关成了管线的一半，也违背 #70 的纯函数边界。
- **两个对象（`Sessions` 与 `Workspace`）**：否——跨簇操作要同一个世界视图；`FakeHerdr`
  本来就是一个世界。
- **读模型继续返回原始 dict**：否——容错（`status` 为 dict 或 null、`foreground` 键整个
  缺失、`pane` 为空或条目隐藏）会继续在调用点各处理一遍。
- **等待循环进网关**：否——六个循环各夹阶段专属外部 kind，属管线阶段。
- **`session(target)` 找不到时返回 `None`**：否——`NotFound` 五处含义各异，显式 `except`
  比统一 `None` 更不易出错。
- **网关收 `progress` 回调、直接写台账**：否——台账收敛归候选④，网关不预支其形状；
  且按决定 8 切分后没有哪个台账写入需要发生在单个操作内部。
  （后半句已被 #113 G2 在途裁决证伪并订正：`send` 的补发必须拆成两个操作，台账
  写入发生在两次复核之间——见顶部补记。）
- **`select` 与 `restore` 两个显式操作**：否——「恒还原」交给调用方写 `finally` 等于把
  不变量重新泄漏出去。
- **故障键改成领域操作名**：否——见「对 ADR-0016 的修订」。

## Consequences

- 引入网关对既有测试零影响：测试仍在命令级注入。ADR-0016 迁移步骤的顺序不变——
  网关建立在 `Deps` 之上，故实施在「引入 `Deps`」这一步之后。
- 已接受的代价：测试永远得说 herdr 命令方言，hc / herdr-app 的契约变动会波及
  `FakeHerdr` 与 `Herdr` 两处（管线测试不受影响，它们说世界事件与故障键）。
- `HcFailure` / `AppFailure` 目前被 6 个测试文件的旧桩共 22 处引用，其名字退役排在
  迁移最后一步，与旧桩一起删；迁移期保留兼容别名。
- 实施切片与护栏顺序要等一轮管线票定型后才能写具体，本 ADR 不预设。
- 补记（ADR-0021，#82）：实施切片已由 ADR-0021 写定——网关分 G1（纯新增 `herdr.py`）、G2（会话簇）、
  G3（空间簇），特征化快照排在网关迁移之前；兼容别名退役在收尾片 R。
- `space.new` 返回形状仍是假设（决定 13b），在 HITL 采样入库之前，依赖它的行为只能说
  「由生产运行验证过」，不能说「由样例证明」。
- 隐私：样例由实施票手工提取脱敏最小集带进公开仓库（占位符化，不含本机路径、用户名、
  主机名、真实会话标题或 token）；研究分支仍只留本地。
