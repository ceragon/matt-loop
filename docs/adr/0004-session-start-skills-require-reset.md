---
status: accepted
---

# 会话起始 skill 必须重置目标会话

> 补记（ADR-0017，#61）：本 ADR 的机械闸（行首会话起始 skill＋turn>0＋none 拒发）不变；其前新增静态预检 `split_required`——命中触发器且提示词超体积或多行时先拒发（命令行必须单独单行一轮），先于 `reset_required`，口径与证据见 ADR-0017。

> 补记（ADR-0015，#62）：本 ADR 的机械闸（行首会话起始 skill＋turn>0＋none 拒发）不变；「新任务首轮推荐重置（不限起始 skill）」属建议层延伸纪律，不新增机械拒发，见 ADR-0015 决定 7。

## 背景

loop 的调用方（Grok Bot 等）把放行提示词发进 herdr 执行者会话。执行者跑的是
/ask-matt 工作流的 skill：wayfinder 的连续性在地图 issue 里（地图每会话加载一次），
grill-with-docs 的产出在 GLOSSARY/ADR 里，implement 按票自包含（ask-matt 明文
「/clearing context between each one」），implement-spec 每票全新 implementer
subagent，prototype 经 handoff 文件在全新会话里开场。这些 skill 的连续性都在工件里，
不在聊天里；带着旧上下文接着调，只会把上一票的余温混进这一票，还加速撑爆 smart zone。

此前 SKILL.md 只有一条护栏「先放行，后调用」，--reset 完全可选，调用方可以在
半截会话里继续调 wayfinder/implement，没有提醒也没有拦截。

## 决定

五个 skill 定为**会话起始 skill**：`wayfinder`、`grill-with-docs`、`implement`、
`implement-spec`、`prototype`。它们只能作为空会话的开场任务，两层强制：

1. **文字纪律**（SKILL.md「会话起始 skill」节＋护栏第二条＋「每轮循环」Step 1）：目标已有轮次（`turn > 0`）时，调用必须带 `--reset new/clear/compact` 先清目标执行者会话（Step 1 讨放行时一并确认，不先空跑撞拒发再回来二轮请示）；`turn == 0` 的全新空会话免 `--reset`；调用独占一行（行首斜杠命令）；点名 /ask-matt 为工作流出处，默认调用方已装 Matt 全套 skill。SKILL.md 同时写明已核实的 agent 命令对照（`agy`/`pi` 认 `/new`＋`/compact`、`claude` 认 `/clear`＋`/compact`、`codex` 三者都认、`grok` `/new`≡`/clear`）。
2. **机械预检**（dispatch.py）：放在 `hc get`＋身份预检之后、写台账与任何发送之前。提示词文件**行首**命中五个斜杠命令之一（允许行前空白；命令后须非词字符且非连字符；implement-spec 优先报长名）且 `--reset=none` 时：
   - 若 `hc get` 的 `turn` 为精确整数 `0`（全新空会话，无历史上下文可污染），**直接豁免放行**，省去无效重置与来回请示；
   - 若 `turn > 0` 或缺失，拒发：`error(kind=reset_required)`，退出码 2，message 带 skill 名、行号与当前 `turn` 值。放行 id 不消耗，补 `--reset` 同 id 可重试；台账已存在时 `duplicate_release` 优先，`not_idle`/`identity_mismatch` 先于 `reset_required`。

边界：`new`/`clear`/`compact` 都算重置，唯一拒发值是 `none`。中途 skill
（to-spec/to-tickets/tdd/code-review/retro 等）、回答提问的普通回复、行文中提及
都不触发——清完以 /grill-with-docs 开场后，grill→to-spec→to-tickets 保持一个
不间断窗口（ask-matt 的 Context hygiene）。调用方自己的会话不清；不强制开新标签页，
同一 target 重置即可。

## Considered Options

- **只写文字纪律，不加代码检查**：符合「程序只给工具定义、流程由提示词驱动」与
  最小形态，但调用方 LLM 可能滑掉纪律，无兜底。被否：用户要 a+b 结合，文字提醒＋
  机械拒发两层咬合。
- **compact 不算清空**（只认 new/clear）：compact 留摘要种子，不是彻底的空会话。
  被否：用户裁定纪律管「重置过」、不管彻底不彻底；smart zone 场景下 compact 是
  现实选项，留摘要可接受。
- **全文匹配斜杠命令**：任何位置出现 /skill 都触发，宁误杀不漏放。被否：讨论
  工作流的普通回复（「答完再跑 /implement」）、引用 ask-matt 原文的提示词都会被
  误拒；行首匹配＋「调用独占一行」的文字约定覆盖真实用法，缺口由纪律补。
- **强制开新标签页/新 target**：更彻底，但 target 地址管理、关旧标签、身份预检
  全变复杂，收益与 --reset 相同（都是空上下文）。被否：复用现有 --reset 机制。
- **清单更宽**（加 to-spec/to-tickets/prototype/research 等所有阶段 skill）：
  与 ask-matt 的 Context hygiene 冲突——grill→spec→tickets 须一个不间断窗口，
  中途清会话反而丢思考。被否：只收五个会话起始 skill（prototype 按用户要求纳入，
  ask-matt 本来就要求它新会话开场）。

## Consequences

- 调用方调这五个 skill 时永远从工件（票/地图/文档/handoff）重新定向，上下文干净；
  放行提示词里的调用要写行首，行中提及不触发。
- dispatch 多一个预检拒发点（reset_required，退出码 2，有 JSON 与 result.json）；
  拒发在台账之前，放行 id 不消耗，重试无需重新放行。
- 机械检查只兜行首这一层：调用方把调用写在行中且不带 --reset 仍会漏过——那已先
  违反文字纪律，属调用方责任，脚本不再加复杂豁免逻辑（代码块/引用检测）。
- 清单是常量（SESSION_START_SKILLS），Matt 工作流演化时改清单＋测试即可；
  退出码 2 从「只有 argparse」扩为「参数类错误（含 reset_required）」，
  REFERENCE 的 error 契约已同步。

## 修订补记（2026-11-23）

实跑暴露一条摩擦：新开 `agy` 会话（`turn == 0`，刚就绪无任何历史对话）首条发送 `/wayfinder` 且 `--reset=none` 时被 `reset_required` 拦下，调用方停下来二轮请示用户选 `/new`/`/clear`/`/compact`，选择 `/new` 后又白等 ~26 秒重置前置；且 SKILL.md 仅写了 `claude/grok` 示例、未写 `agy` 认 `/new`。本次校准：
1. `reset_required` 预检移至 `hc get`＋身份检查之后（仍在写台账前），`turn == 0`（精确整数 `0`）自动豁免放行，`turn > 0` 或缺失才拒发且 message 带 `turn` 值（否决了「`turn > 0` 也按 agent 自动清」的 `auto` 方案——旧上下文清空仍须用户授权）；
2. SKILL.md 补充「`turn > 0` 复用旧会话在 Step 1 讨放行时一并带出推荐 `--reset`」并核实补全各 agent 命令对照（顺带修正先前对 `claude` 认 `/new` 的误写）。

## 修订补记（2026-11-27）

`SESSION_START_SKILLS` 常量已删（#133 R4）：它只被测试引用，生产判定的唯一出处是
`turn.py` 的 `SESSION_START_RE` 行首触发器（正则内逐字列出五项）。测试改为自带一份
外部真值清单，改漏一处即红。决定不变——五个会话起始 skill 的口径与 `reset_required`
预检行为都没有变化。