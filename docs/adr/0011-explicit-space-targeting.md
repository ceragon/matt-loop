---
status: accepted
---

# 空间定位：--space 必传三态解析，指定领养落点

> 追加说明（ADR-0015，#62）：机制不变；调用方约定「一 bot 一常驻空间」——把固定 --space 值写死在自己配置里、每轮恒传同一值。「pane 已在 adopt 提前返回、无跨空间挪 pane API」成为该约定的承重机械事实。见 ADR-0015 决定 5。

## 背景

issue #43 实证：命名会话领养（ADR-0009）后，终端 pane 落进 herdr-app **当前
选中的空间**，而非与 cwd 相关的工程空间——一个 cwd 为项目目录的命名会话被
领养进 folder 为 home 的活动空间（台账无 `space_created`，全程没走
`space.new`，是 `terminal.keep` 把终端落进了当时选中的空间）。恒可见不变量
满足，但「可见于对的工程空间」不满足；多工作流共用一台机器时终端散落在无关
空间，调用方无法预期会话出现在哪个工作区。

事实核查（2026-11-24，本机只读 `herdr-app commands`/`status`/`ls`，未碰生产
会话）：

1. `terminal.keep` 参数只有 `terminal`＋`place`；`place` 语义是
   `tab`(默认)/`right`/`down`（相对焦点 pane 的位置），**非空间定位**。
   无 pane 终端一律落进当前选中空间。ADR-0009「keep 无 space 参数、落点随
   herdr-app 规则不可控、已接受」表述不准（有 `place`，但 `place` 不管空间）。
2. `space.select {"id"}`（或 `index`）存在：切换选中空间，**无 `focus:false`
   （必抢视图）**。`status` 返回 `space`＝当前选中（shown）空间 id；`ls` 的
   spaces 带 id/name/folder。
3. `tab.new{space,focus:false}` 能静默建终端，但建的终端**无会话名**
   （`hc get <名>` 断，get-or-create 命脉）；**无跨空间移动现有 pane 的 API**
   （`tab.move` 仅本空间内）。故命名会话＋指定空间只能 `space.select`。
4. **关键约束**：多个空间可共享同一 folder（本机实证四个空间同 folder），
   且某些工作流的会话**故意**放在 folder 为 home 的空间里。**不传参静默按
   cwd 猜**会撞多个同 folder 空间（选哪个歧义），还会把本该在 home-folder
   空间的会话硬拽进项目目录空间、拆散既有组织——仍禁止。显式传路径 opt-in
   自动建是另一回事（调用方明示意图）。

本 ADR 经 grill 定稿（2026-11-24，issue #43「实现规格」节），supersede 早期
「C 打底＋E 可选（--space 可选、不传维持现状）」推荐。

## 决定

**修法 E：调用方显式 `--space` 指定目标空间，必传。**

1. **参数**：`--space` **必传**（所有调用，argparse `required=True`）。
   三态解析（只在需要 keep／pane 缺时生效；pane 已在 adopt 提前返回不抢）：
   - `/` 或 `~` 开头 → **路径**：按 `space.folder==路径` 匹配；唯一→复用；
     零个→`space.new{name:目录名, folder:路径, focus:false}`；多个→
     `adopt_failed(space_ambiguous)`。路径须是已存在目录（否则 `bad_args`，
     退出码 2、零发送、id 不消耗）。
   - `sp` 前缀 → **id** 直配（不存在→`space_not_found`）。
   - 其余 → **名**：ls 按 name 唯一匹配；重名→`bad_args`（退出码 2、id 不
     消耗）；不存在→`space_not_found`。名不自动建空间。
   - `--space` 与 `--cwd` 独立（空间 folder 未必等于终端 cwd）。
2. **机制**（静默不可行，API 实证见背景 3）：keep 真发时（pane 缺失），
   目标≠当前 shown → `space.select(target)`→`status` 复核（命令成功不算数；
   不符先走还原护栏再 `adopt_failed(select_error)`，keep 不发——select 可能
   已生效只是复核抖动，焦点不滞留目标空间）→keep→带超时轮询 `ls` 复核 pane
   落地（#40 护栏不变）＋**复核 pane 所在空间**→`finally` 还原原 shown。
   目标==shown 不 select（不抢视图）。复用轮 pane 已在→adopt 提前返回。
   基本仅创建时抢一下（1–2s）。
3. **还原纪律**：keep 失败也还原；还原前当前 shown≠target（用户已切走）→
   不还原（`space_restore:"user_switched"`），绝不抢回用户的视图；还原失败→
   `space_restore_warning`（restore_error/restore_mismatch）降级不丢放行。
   还原是 check-then-act 的 best-effort：status 核对与还原 select 之间仍有
   极小窗口，用户恰在此刻切换会被拉回一次——已接受（无原子条件切换 API）。
4. **落地复核所在空间**：keep 异步落地，select 复核后、pane 落地前用户切走
   选中时 pane 会落进用户的空间。pane 存在还不够，须在目标空间：
   `space_landed` 如实报实际落点；非目标空间→`space_target_warning
   (landed_elsewhere)` 告警不丢放行（pane 已落地恒可见满足），不静默重演 #43。
5. **零空间**：废「零空间自动 `space.new{name:会话名}`」特殊分支，统一走
   `--space` 解析；建空间 name=**目录名**；赠送 shell 清理改绑 `space_created`
   （不再绑「本机无 space」）。建后复核 shown：零空间时新空间即被显示，
   目标==shown 不 select。
   **赠送 shell 清理（#46 修订）**：`tab.close confirmed:true` 后**带超时
   轮询 `hc get` 等赠送终端 finished**（复用 `--adopt-timeout`/`--adopt-poll`）
   再 `hc rm`——tab.close 异步结束终端，单次 rm 撞 live 被拒（退出码 1，
   与 #40 同类竞态：单次动作应改轮询）。清理失败（tab.close 失败／窗口耗尽
   仍 live／rm 失败）降级 `gift_cleanup_warning`，**不丢已成功的领养**——
   真终端 `pane_adopted` 则整轮仍 adopted、照发主任务（原「清理任一步失败
   adopt_failed」把已可见的整轮误判 failed）。
6. **result/台账**：`space`（请求值回显，传了就带，成功与 error 都带）、
   `space_selected`（解析出的目标 id）、`space_landed`（实际落点 id）、
   `space_target_warning`（落错告警）、`space_restore`（restored/
   user_switched/not_needed/failed）、`space_restore_warning`、
   `space_created`/`pane_adopted`（建空间与 pane 落地部分状态；#46 起
   `pane_adopted` 在 keep 落地复核通过即报，不限于建空间分支）、
   `gift_cleanup_warning`（赠送清理降级告警，#46：reason `tab_close_error`/
   `finish_timeout`/`finish_poll_error`/`rm_error`）。

本 ADR **supersede**：ADR-0009 决定 8「本机无 space：space.new{name:会话名,
folder:cwd}」与「不指定空间（keep 无 space 参数），落点随 herdr-app 规则、
不可控，已接受」两条款；ADR-0010 决定 9「space.new 的 name 维持会话名」。
恒可见不变量、adopt_failed 语义、创建/既有路径的 id 消耗规则全部不变。

## Considered Options

- **C 打底＋E 可选（--space 可选、不传维持现状）**：早期推荐，grill 否——
  不传时落点仍随用户当前视图漂，调用方无法预期；必传把落点意图变成每轮
  显式契约。
- **不传参静默按 cwd 自动路由**：撞多个同 folder 空间歧义；把故意放
  home-folder 空间的会话硬拽进项目空间、拆散既有组织（关键约束）。否。
- **A（纯 `space.select` 自动切）**：无显式意图时抢焦点＋还原竞态代价大，
  且切去哪个空间仍是瞎猜。否。
- **B（`tab.new{space,focus:false}` 静默建终端）**：建的终端无会话名，
  `hc get <名>` 断、get-or-create 命脉断；且收编不了已跑起来的现有终端。否。
- **跨空间移动现有 pane**：无 API（`tab.move` 仅本空间内）。已落错存量终端
  不移动，手动挪或 kill 重建（范围外）。
- **名重名也报 space_ambiguous**：名是调用方可控的静态输入，重名属参数错误
  （bad_args、id 不消耗）；space_ambiguous 留给路径多匹配（数据态）。
- **--space 找不到时一律自动建空间**：把「显式指定」悄悄变成「自动创建」，
  与不静默创建精神冲突；仅路径形态零匹配 opt-in 建，名/id 找不到报错。否。
- **还原失败整轮报错**：pane 已落地、恒可见已满足，为视图还原丢掉放行
  得不偿失。降级告警。否。

## Consequences

- 调用方：**每轮必传 `--space`**（名/id/路径），讨放行时随参数一并贴出；
  路径形态零匹配会自动建空间（name=目录名），属显式 opt-in。
- 显式传 `--space` 且目标≠shown 时短暂抢焦点（select→keep→还原窗口，
  基本仅创建时 1–2s）；用户窗口内接管选中即让位。
- herdr-app 升级可能改 keep/space.select/space.new 行为；select 复核、还原
  复核、落地复核都是只读 status/ls，行为漂移会如实报错/告警而非静默落错。
- 与 #40（领养复核竞态）独立：轮询护栏原样保留，空间定位包在其外层。
- 已落错存量终端不移动（无 API），手动挪或 kill 重建。