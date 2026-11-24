---
status: superseded by ADR-0009
---

# 换 agent kind＝按终端形态分流的混合前置步

## 背景

ADR-0006 留了接缝：前置已是有序列表（重置 → 切模型 → 主任务），未来重启机制
可作为新前置类型挂入。用户随后提出「我还是想实现一下更换 kind，但我不确定更换
之后，执行者是否还认得出来新的终端」——核心顾虑是**换 kind 后地址连续性**：loop
的 `--target` 定位模型、放行台账、结果里的 target 都系于终端地址。

事实核查（2026-11-23 一次性终端实证，未碰生产会话 t24）：

1. **keep＋信号恢复对真实 agent 不可行**。`hc kill` 是「hangup then escalate」但
   会**结束终端**（live keep 程序被 kill → `finished`，不恢复）；程序干净退出
   （exit_code 0）也 → `finished` 不恢复。唯一实证能原地恢复的触发是 **SIGINT**
   （`hc interrupt`／`hc keys ctrl+c`）——bash 不接 SIGINT 故死后 keep 原地换 shell、
   同地址。但真实 TUI agent 接信号：pi 的 stop 键是 **esc**（`hc interrupt` 只取消
   活动、pi 仍 live），ctrl+c 也被接住；ctrl+d/退出命令是**干净退出 → finished**。
   故「interrupt 旧 agent → keep 原地恢复 shell → send 新命令」这条同地址路径对
   真实 agent 走不通。
2. **shell 包装终端可同地址换**。终端程序是持久 shell（`zsh -l`）、agent 当子进程
   跑时：退出 agent → 前台回到 shell（终端仍 live、**同地址**、cwd 保留）→ send
   新 agent 启动命令即可。不依赖 keep。且 **hc 按前台进程认 agent/追 turn**（pi 当
   shell 子进程跑时 `status.app=pi`、turn 随发消息递增），loop 的 turn0 豁免/turn>0
   须重置逻辑在 shell 包装下照常。
3. **直接起 agent 的终端（t24 这种，程序=agy）只能新地址**：kill → finished，
   `hc run` 新终端 → 新 t 号。地址必然变。
4. 终端形态可按 `hc get` 的**启动 argv** 判：argv[0] 是持久 shell（`zsh -l` 等、
   非 `-c` 一次性）→ shell 包装；直接起 agent 或 `sh -c` 一次性 → direct。

## 决定

dispatch.py 新增 `--switch-agent <完整启动命令>`，作为与重置/切模型**互斥**的前置步
（新会话 turn 0、模型写进启动命令），按终端形态分流：

1. **字面透传、零映射**：启动命令（`--switch-agent`）与退旧方式都由调用方字面提供，
   脚本不维护 kind→命令映射。退旧用 `--exit-command <文本>`（走 `hc send`，如
   claude 的 `/exit`）或 `--exit-keys <按键>`（走 `hc keys`，如 pi 的 `ctrl+d`），
   二者互斥、须配合 `--switch-agent`。与 `--reset`/`--target-model`/`--pi-model`
   同一条边界。
2. **shell 包装终端 → 同地址**：退旧 agent（若有 agent 在跑）→ 等前台回 shell →
   send 启动命令 → 等新 agent 就绪（前台非 shell 且 idle/done）→ 核身份 → 发主任务。
   地址不变，结果无 `target_new`。前台已是空闲 shell（无 agent）则跳过退旧。
3. **直接起 agent 终端 → 显式新地址**：kill 旧终端 → `hc run --bg --keep --cwd <旧cwd>
   -- sh -c <启动命令>` 起新终端 → 等就绪 → 核身份 → 主任务发到新终端。结果
   `target` 恒为调用方传入的原地址、另报 `target_new`（新 t 号），调用方据此更新
   `--target`。新地址是**显式回报**，不是静默降级。
4. **互斥（Q3）**：`--switch-agent` 与 `--reset`/`--target-model` 同传 → `bad_args`
   （退出码 2，放行 id 不消耗）。理由：启动命令是新 agent 配置的唯一来源、新会话
   turn 0 无需重置，叠在一起只产生顺序歧义。
5. **安全契约（Q4）**：shell 分支任一环节失败即 blocked、**绝不静默换地址**——
   退旧后终端 finished（地址丢失）→ `switch_recovery_failed`；等回 shell/等新 agent
   超时 → `switch_timeout`；有 agent 在跑却没传退出旗标 → `switch_exit_required`；
   新 agent 身份不符 `--expect-proc` → `identity_mismatch`。direct 分支的新地址是
   显式回报，不违反「不静默换地址」。
6. **身份与纪律**：`--expect-proc` 由调用方同步改成新 kind；换 kind 时身份核对推迟到
   新 agent 就绪后（旧前台与新 kind 不符，预检不核身份）。换 kind 起全新 turn 0 会话，
   会话起始 skill 纪律预检跳过（本身即满足「空会话」）。`duplicate_release`/`not_idle`
   优先级不动，前置仍在预检＋写台账之后。
7. **台账与结果**：ledger 记 `switch_agent`/`exit_command`/`exit_keys`/`switch_form`
   与 `switch_start/switch_exit_sending/switch_waiting_shell/switch_launching/
   switch_waiting_agent/switch_killing/switch_running/switch_done` phase；direct 分支
   另记 `target_new`。结果成功与 error 都带 `switch_agent`/`switch_form`，direct 另带
   `target_new`。
8. **退出按键是显式授权的例外**：提示词发送仍永不 `--force`、不碰 `hc keys`（裁定 6）；
   `--exit-keys` 走 `hc keys` 是调用方显式授权的**退旧**操作（不是提示词），单独区分。

## Considered Options

- **同地址靠 keep＋信号恢复（interrupt 旧 agent → keep 原地换 shell → send 新命令）**：
  最初选定，被**实证推翻**——真实 agent 接 SIGINT/esc（pi stop 键是 esc、ctrl+c 被接），
  干净退出 → finished 不恢复，`hc kill` 也结束终端；keep 的「signal death 恢复」只对
  不接信号的程序（bash）有效。对真实 agent 走不通。
- **纯 shell 包装启动约定（所有 agent 都起在持久 shell 里）**：唯一保住地址的路，但需把
  shell 包装定为启动约定、现有直接起 agent 的终端（t24）须迁移才适用，且接上挂起的
  「启动 agent」skill 线。被否作为**唯一**机制：现有直接终端用不了；改为混合，shell
  包装终端同地址、直接终端显式新地址。
- **纯新地址 kill＋run（所有终端都换地址）**：最稳最简单，但地址必然变，正是用户担心的
  「认不出」。被否作为唯一机制：shell 包装终端本可同地址，不该白白丢地址。
- **脚本内置 kind→启动命令/退出命令映射**：调用方只说换哪个 kind，脚本查表拼命令。被否：
  违反「脚本不映射命令、字面透传」边界，每加 agent/改旗标都要改脚本。
- **脚本固定通用退出序列（ctrl+d→/exit→exit 试到回 shell）**：不用调用方传参，但可能误发
  文本污染会话（实测 pi 把 `/exit` 当 prompt 处理走了 token）、对不认的 agent 留残局。被否：
  退出方式由调用方字面提供，可预测。
- **同地址失败自动降级 kill＋run 新地址**：更不易卡死，但悄悄破坏地址连续性。被否：
  违反 Q4「绝不静默换地址」；shell 分支失败即 blocked，direct 分支的新地址是显式回报。

## Consequences

- 调用方换 kind 时：shell 包装终端地址不变；直接终端须读结果 `target_new` 更新 `--target`
  （「认不出」靠显式回报化解，而非靠地址不变）。`--expect-proc` 须同步改新 kind。
- 同地址换 kind 仅适用于**持久 shell 包装终端**；这类终端的启动约定归挂起的「启动 agent」
  skill 线（本 ADR 不创建终端，只对既有终端分流）。直接起 agent 的终端（含 t24）走显式新地址。
- 退旧方式（`--exit-command`/`--exit-keys`）按旧 agent 认哪个挑，归调用方纪律（SKILL.md
  对照：pi=ctrl+d 走 keys，claude=/exit 走 send）；写错的风险与写错重置命令同级。
- 一个放行的发送上限：换 kind 与重置/切模型互斥，shell 分支为退旧＋起新＋主任务，direct
  分支为主任务（新终端）；台账 phase、error 部分状态、SKILL/REFERENCE 已同步。
- 实证留档：keep＋信号恢复对真实 agent 不可行（推翻 11-20「kill＋run 换地址」之外的同地址
  设想），同地址靠 shell 包装；hc 按前台进程认 agent/追 turn，shell 包装下照常。
