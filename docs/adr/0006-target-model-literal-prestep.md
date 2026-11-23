---
status: accepted
---

# 目标模型切换＝字面 /model 前置步

## 背景

loop 的调用方有时需要在发主任务前切换执行者会话的模型：全新会话启动时带错了
模型、旧会话想换更强的模型接着干同一张票、或重置会话的同时顺便换模型。用户
同时提出「未来不排除增加更换 agent kind 的支持」——那是进程生命周期操作，
与切模型不同类。

事实核查（2026-11-23 实施时核准）：`hc` 本身没有切模型命令，`hc get` 的 JSON
也不暴露当前模型；各 agent 启动旗标都有 `--model`（claude/pi/codex/agy 均核实），
会话内 `agy`、`claude`、`pi`、`grok` 都认行内 `/model <名字>`，`codex` 的裸
`/model` 开选择器（行内名字未实证）。

## 决定

dispatch.py 新增 `--target-model <名字>`：重置（如有）之后、主任务之前，在同一
target 发字面命令 `/model <名字>`（`--if idle`，共用 `do_send` 发送门），等落定
（idle/done＋屏幕静置，复用 `wait_for_reset`）再发主任务。

1. **字面透传、零映射**：脚本不认识模型、不按 agent 变换命令、不校验名字合法性
   （argparse 非空之外不加新校验）；传什么名字由调用方按目标 agent 认哪个挑，
   与 `--reset` 同一条边界。
2. **与 `--reset` 独立**：可单独传（任意一轮都能随下一条消息带模型切换），也可
   同传（顺序固定：重置 → 切模型 → 主任务）；不新增耦合拒发规则。
3. **只等落定，不核实、不预读跳过**：`target_model` 语义是「本轮请求切换到的
   名字」，非「已验证生效」。`hc get` 无模型字段，刮屏核实须按 agent 写识别
   规则，打破零映射边界且脆弱；agy 对「已是该模型」（报 `Model already set`）
   与「名字不认」（打印有效列表）均不弹框，落定判定照常走。
4. **纪律交互不变**：会话起始 skill＋`turn > 0`＋`--reset=none` 仍拒发
   `reset_required`（切模型不能替代重置）；`turn == 0` 豁免时切模型前置照跑；
   `duplicate_release`/`not_idle`/`identity_mismatch` 优先级不动，前置仍在全部
   预检＋写台账之后。
5. **台账与结果**：ledger 记 `target_model` 与 `model_sending/model_waiting/
   model_done` phase（镜像 reset 三 phase）；`target_model` 仅传了才出现在结果
   （成功与 error 都带）；error 路径带 `error.model_sent` 部分状态（镜像
   `reset_sent`）。一个放行至多发送成功三次（重置＋切模型＋主任务，各一次）。
6. **agent 支持对照**（实施时核准，写入 SKILL.md）：`agy` 认 `/model <名字>`
   （name/slug/label，存为默认；注意 `/model <名字> <提示词>` 是其一次性语义，
   多词值按字面发出可能触发，归调用方纪律）；`claude` 认别名或完整模型 ID；
   `pi` 认 `/model [provider/model]`；`grok` 认 `/model <名字> [window] [effort]`；
   **`codex` 别传 `--target-model`**（裸选择器 → blocked → `dialog_blocked`
   停轮），换 codex 模型用启动旗标 `-m` 重开会话。

## Considered Options

- **重启 agent 带 `--model` 旗标（kill＋同 pane 重新拉起）**：一条机制同时覆盖
  切模型与未来换 kind，全 agent 通用。被否：脚本从信使变进程管理器——启动
  命令、yolo 旗标、cwd、身份重验、turn 归零、信任弹框全要管，复杂度陡增且
  违背最小形态；与挂起的「启动 agent」skill 线重叠；每次切模型都丢会话进程
  状态。
- **双轨现在就做（/model 管模型＋重启管 kind）**：kind 切换明说是「未来不排除」，
  现在没有需求方。被否：提前实现没有需求方的重机制。
- **`--model` 耦合 `--reset`（model_requires_reset 拒发）或耦合＋turn==0 豁免**：
  被否：独立前置步完整覆盖「重置顺便切模型」主用例，还白送空会话纠模型与
  中途不清上下文换模型两个真实场景；零新拒发规则，不再造 #31 刚修完的
  「首条消息被拒」摩擦。
- **落定后刮屏核实（model_not_confirmed）或预读状态栏跳过**：被否：`hc get`
  无模型字段，唯一核实通道是刮屏，各 agent TUI 显示模型格式各异（别名/slug/
  label），识别规则脆弱且打破零映射边界；与启动 skill「不读屏、不核实模型
  是否被接受」的既有决定一致。
- **裸 `--model` 命名**：被否：与抽取侧 `--pi-model`/`LOOP_API_MODEL` 并存易被
  误读成换抽取模型；`--target-model` 与 `--target` 同族，零新词汇。

## Consequences

- 调用方任意一轮放行都可带模型切换；放行 id 一并授权全部前置＋主任务，放行
  纪律不变。
- `target_model` 是请求值非验证值：名字写错的风险与今天写错重置命令同级，归
  调用方纪律（SKILL.md 对照表）；要确认随时 `hc read` 只读监听。
- codex 暂不在切模型覆盖内（选择器型 `/model`）；其模型切换须重开会话或等
  未来的重启机制。
- **kind 切换接缝**：前置已是有序列表（重置 → 切模型 → 主任务），未来重启
  机制可作为新前置类型挂入，不破坏 `--target-model` 契约；重启的进程管理
  细节等 kind 切换有真实需求方时另设计。
- 一个放行的发送上限从两次变三次；台账 phase、error 部分状态、SKILL/REFERENCE
  已同步。
