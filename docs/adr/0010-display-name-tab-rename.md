---
status: accepted
---

# 显示名层：调用方传 --display-name＋tab.rename 落地

## 背景

用户要求：loop 创建会话时给 agent 命名，参考某工作台 space 里三个 claude
agent 的命名方式让它更直观。参考事实（调用方侧核过）：hc 终端名
pop-en6o/pop-pmif/pop-x6u9（随机短码，地址层）；GUI 标签页显示名与 hc label
summary 逐字一致（`<bot 族>_<任务描述>` 式、可中文）；另有 bot/where 标签
与终端 title 第三层。loop 现状：--target 会话名即地址（语义 slug），无显示层。

探针实证（2026-11-24，display-name-probe，herdr-app 0.1.17 / hc 0.1.22，
文档＋自建一次性终端变异实测，已清理无残留）：

1. **summary 完全不参与显示**。GUI tab 名只由 tab.rename / tab.new{name}
   的显式名字驱动；未命名 tab 由前端 mod 用 agent live title / 程序名兜底
   （tabs mod：显式名优先，未命名或恰等于程序名时跟随 live title）。
   app/core 二进制与全部 shipped mods 均无 summary 显示处理。实测：打
   summary 领养后 tab 名是位置号；tab.rename 后再改 summary，tab 名不跟随。
2. 「显示名 == summary」的真正成因是**参考工作流双写**（summary 标签＋
   tab 名各写一次）；探针期间新样本 pop-y2dy 的存储 tab 名与 summary
   逐字相同，佐证双写而非派生。
3. tab.rename＝显式覆盖、从此不再自动跟随（直到改空回位置号，或恰改回
   程序名重新跟随 live title）；herdr-app ls 不暴露 named 布尔。
4. summary 只能显式 hc label 写，agent/OSC 不自动写、不覆盖；值无字符集/
   长度限制（中文/空格/300 字/换行均实测接受）；GUI 约 170px 截断。
5. where 标签自动跟随 tab 显示名（`<space> / <tab 名>`），hc ls/get 可读。
6. terminal.keep 无 name 参数（{terminal, place?}）；命名只能在 keep＋ls
   复核 pane 落地后补 tab.rename。pane.rename 只改窗格头、不改 tab 名。
7. 撞名坑：显示名恰等于程序名时 tabs mod 把它当自动名、继续跟随 live title。

## 决定

（Q1–Q9，用户 2026-11-24 拍板）

1. **分层**：地址层完全不动——会话名仍是语义 slug（1–32 位 a-z0-9-_），
   唯一寻址与复用依据；只新增显示层，纯增量。不学参考的随机短码地址：
   参考终端是一次性任务会话，loop 命名会话是跨轮复用的稳定身份
   （ADR-0009 get-or-create 靠同名寻址）。loop=1/loop-agent 标签与收编
   凭据不变。
2. **来源**：调用方显式传 --display-name，脚本不派生、不拼接、不理解，
   只机械校验＋落地（ADR-0002 机械/理解分离、零映射纪律）。
3. **式样**：SKILL 推荐 `<bot 族>_<任务描述>`（如 `LoopEng_命名层设计`）、
   可中文；bot 族前缀＝谁派的活（增量信息），kind 前缀冗余（GUI 本就能
   看到哪个 agent 在跑）。脚本不校验式样。
4. **语义与必传范围**：显示名代表当前任务；创建/复活/重建三条新鲜路径
   必传，复用轮可选——传了就更新、不传保持旧值。调用方通常只在创建/重建
   时传。（此句 superseded by ADR-0015：约定改为每轮传显示名——同任务各轮
   同名（幂等覆盖）、新任务才换名；脚本「新鲜路径必传、复用轮可选」机制
   不变。）
5. **覆盖语义**：传了就覆盖，不做记账比对保护——放行已授权名字本身。
   SKILL 纪律：复用轮传显示名前，调用方若知用户手工改过名，先向用户确认。
   （ADR-0015 追加说明：显示名改为每轮传后，本条手工改名确认仍保留、每轮
   触发，best-effort；覆盖语义与不记账比对不变。）
6. **落地机制**：tab.rename 唯一路。时序：领养复核 pane 落地后、前置/主任务
   发送前；find_tab_id 拿 tab id → tab.rename → ls 复核 tab.name == 显示名
   （命令成功不算数）。台账 naming phase；result 回显 display_name。
7. **失败策略**：降级告警不丢放行——pane 落地（可见性）仍是硬闸
   （adopt_failed 不变）；rename 失败/复核不符/无 tab 记
   display_name_warning（reason：rename_error/rename_mismatch/no_tab），
   主流程继续，选项卡如实呈报。显示名是外观层，不抬成硬闸。
8. **不双写 summary**：显示名只落 tab 名一处，机器可读靠自动跟随的 where
   标签。双写制造第二份真相（手工改名后 where 更新、summary 停旧值）；
   且 summary 在本仓库是已退役抽取字段，避免词汇混淆。
9. **参数纪律**：缺参 display_name_required（退出码 2、零发送、id 不消耗、
   补参同 id 重试，与 launch_required/reset_required 同构）；校验非空、
   1–64 字符、禁换行控制字符，非法 bad_args；撞名坑写 SKILL 警示、不进
   脚本校验。space.new 的 name 维持会话名（空间是容器不是任务）。
   （**此句 superseded by ADR-0011**：#43 定稿建空间 name=目录名，
   统一走 `--space` 路径解析。）

## Considered Options

- 随机短码地址＋显示名承载全部语义（照抄参考）：否——破坏跨轮同名寻址与
  get-or-create。
- 脚本从提示词派生显示名：否——违反机械/理解分离与零映射。
- summary 标签落地（照抄参考工作流双写）：否——探针证实 summary 不参与
  显示；双写制造漂移。
- 记账比对尊重手工名（loop-display 标签＋--force）：否——放行已授权名字，
  低频后门不值护栏复杂度；静默跳过反而多一轮排查。
- rename 失败硬停轮（display_name_failed）：否——装饰损失不值消耗一次放行。

## Consequences

- 调用方 API 增 --display-name（新鲜路径必传），放行贴出参数增显示名；
  SKILL/REFERENCE/GLOSSARY 同步。
- ADR-0009 不 supersede：显示层是其纯增量；恒可见不变量不变（pane 硬闸），
  名字是软装饰。
- herdr-app 版本依赖：tab.rename 语义与 tabs mod 兜底逻辑是 0.1.17 事实，
  版本升级须复核（与 ADR-0009 的 keep/space.new GUI 侧约定同一风险面）。
- 公开仓库隐私：背景与实证里的真实 space 名与任务描述已泛化为占位式，
  机制证据与结论不变；pop-* 终端短码保留。
