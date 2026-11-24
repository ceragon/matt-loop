---
status: accepted
---

# 显式空间定位：--space 指定领养落点，不传维持现状

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
2. `space.select {"id"}`（或 `index`）存在：切换选中空间。`status` 返回
   `space`＝当前选中空间 id；`ls` 的 spaces 带 id/name/folder。
3. **关键约束**：多个空间可共享同一 folder（本机实证四个空间同 folder），
   且某些工作流的会话**故意**放在 folder 为 home 的空间里。「按 folder==cwd
   自动匹配/新建」会撞多个同 folder 空间（选哪个歧义），还会把本该在
   home-folder 空间的会话硬拽进项目目录空间、拆散既有组织。**纯 cwd 自动
   路由不是安全修法。**

## 决定

**C 打底＋E 可选**（issue #43 推荐修法）：

1. **C（治标，零风险）**：接受不传 `--space` 时 keep 落点随选中空间；
   REFERENCE.md 把 keep 参数表述改准（`place` 语义＋落点随选中空间），
   SKILL.md 写明「想落对空间，调用方发 loop 前先手动 `space.select` 到目标
   空间，或直接传 `--space`」。
2. **E（治本，显式意图）**：dispatch 加可选 `--space <名/id>`，落点意图交给
   调用方，绕开 cwd 瞎猜：
   - 只在**需要 keep**（pane 缺）时生效；pane 已在无操作（不挪已有 pane）。
   - 解析对着领养时已取的 `ls`：先按 id 精确匹配、再按名；找不到→
     `adopt_failed(space_not_found)`，重名→`adopt_failed(space_ambiguous)`。
     **不自动建空间、不按 cwd 猜**；本机无 space 时传 `--space` 同样
     `space_not_found`（不走 `space.new` 分支）。
   - 序列：`space.select {"id"}`→`status` 复核选中（命令成功不算数；不符→
     先走还原护栏再 `adopt_failed(select_error)`，keep 不发——select 可能已
     生效只是复核抖动，焦点不滞留目标空间）→keep→带超时轮询 `ls` 复核
     pane 落地（#40 护栏不变）＋**复核 pane 所在空间**→还原选中。
   - **落地复核所在空间**：keep 异步落地，select 复核后、pane 落地前用户
     切走选中时，pane 会落进用户的空间。pane 存在还不够，须在目标空间：
     `space_landed` 如实报实际落点；非目标空间→`space_target_warning
     (landed_elsewhere)` 告警不丢放行（pane 已落地恒可见满足，且多半是
     用户自己切走所致），不静默重演 #43。
   - **还原竞态护栏**：还原前先只读 `status` 核当前选中——仍是目标空间
     （用户没接管）才 `space.select` 还原回原选中；用户已切走即跳过
     （`space_restore:"user_switched"`），绝不抢回用户的视图。原本就选中
     目标/无原选中→`not_needed`。keep 失败路径（keep_error/no_pane）也经
     finally 还原，不劫持视图。
   - **还原失败降级告警不丢放行**：还原命令失败/复核不符→
     `space_restore_warning`（restore_error/restore_mismatch），主流程继续；
     pane 落地仍是硬闸（与 ADR-0010 显示名降级同哲学）。
   - result/台账：`space`（请求值回显，传了就带，成功与 error 都带）、
     `space_selected`（解析出的 id）、`space_landed`（实际落点空间 id）、
     `space_target_warning`（落错告警）、`space_restore`（restored/
     user_switched/not_needed/failed）、`space_restore_warning`。空/纯空白
     `--space` → `bad_args`（退出码 2、零发送、id 不消耗，请求值仍回显）。
3. `space.new` 命名维持会话名（ADR-0010 不动）；`--space` 与 `--reset`/
   `--target-model`/显示名层零耦合。

本 ADR **supersede ADR-0009 决定 8 的「不指定空间（keep 无 space 参数），
落点随 herdr-app 规则、不可控，已接受」条款**；恒可见不变量、adopt_failed
语义、创建/既有路径的 id 消耗规则全部不变。

## Considered Options

- **A（纯 `space.select` 自动切）**：无显式意图时抢焦点＋还原竞态代价大，
  且切去哪个空间仍是瞎猜。否。
- **B（`tab.new{space}`）**：建新终端，收编不了已跑起来的现有终端，单独
  不可行。否。
- **按 folder==cwd 自动匹配/新建空间**：撞多个同 folder 空间歧义；把故意
  放 home-folder 空间的会话硬拽进项目空间、拆散既有组织（关键约束）。否。
- **只做 C（文档改准＋手动 space.select 纪律）**：零风险但落点意图仍靠
  调用方手动两步操作，窗口内依旧漂。作为打底保留，E 补治本。
- **--space 找不到时自动建空间**：把「显式指定」悄悄变成「自动创建」，
  与不静默创建精神冲突；报错让调用方核对名字更诚实。否。
- **还原失败整轮报错**：pane 已落地、恒可见已满足，为视图还原丢掉放行
  得不偿失。降级告警。否。

## Consequences

- 调用方：想落对空间就传 `--space`（名或 id，重名用 id），或发 loop 前手动
  `space.select`；不传行为与旧版完全一致。
- 显式传 `--space` 时仍短暂抢焦点（select→keep→还原窗口），只在调用方
  显式指定时发生；用户窗口内接管选中即让位。还原是 check-then-act 的
  best-effort：status 核对与还原 select 之间仍有极小窗口，用户恰在此刻
  切换会被拉回一次——已接受（无原子「条件切换」API）。
- herdr-app 升级可能改 keep/space.select 行为；select 复核与还原复核都是
  只读 status，行为漂移会如实报错/告警而非静默落错。
- 与 #40（领养复核竞态）独立：轮询护栏原样保留，targeting 包在其外层。