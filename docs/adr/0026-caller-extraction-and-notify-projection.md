---
status: accepted
---

# 抽取三档规则链（api→pi→caller）＋调用方自抽兜底＋通知投影瘦身

> 本 ADR 是 #139 三轮拷问（Q1–Q16）共识稿 v1 的决策记录。翻案两处：
> ADR-0005「否决自动探测」的 Considered Option 被决定 1 的规则链 supersede；
> 拷问中轮「回声未命中不塞全量」的旧口径被决定 3 的「绝不交白卷」推翻。
> ADR-0005/0014 各加补记，正文留历史。

## 背景

抽取后端现状是「api 默认、pi 显式备选」的二元配置（ADR-0005）：什么都没配
（无 API env、无 pi）的机器在受理闸就被 `api_not_configured` 拒发，一轮也跑不
起来。但发送、等待、回读、断轮这些机械步完全不依赖抽取器，本轮原料已经落盘，
调用方自己就是一个能识别回复的模型——零配置机器没有任何兜底路径是不必要的死门。

同时通知投影（ADR-0014）恒带 13 字段：空选段、空 `rewritten` 也占唤醒载荷，
`run_record` 是云端读不到的本机路径。caller 档若只交「无结论的纯成功」，整轮
内容会被传话助手一句话交回、静默丢失——兜底档必须把本轮正文交出去。

ADR-0005 曾以「隐式魔法、走了哪条路变猜谜」否决自动探测；该理由的前提是后端
不可观察。`extract_backend` 已逐轮写进 result/台账（ADR-0005 决定 7），猜谜
理由失效，翻案成立。

## 决定

1. **三档规则链，受理时判一次**：`LOOP_EXTRACT_BACKEND` 值域扩为 `api|pi|caller`
   （非法值仍启动报 `bad_extract_backend`，退出码 2）。规则直链：
   ① env 显式设了 → 照办（部署级意图恒赢）；
   ② 未设但**任一 `LOOP_API_*` 已设**＝api 意图 → api，三个不齐报
   `api_not_configured`（零发送、同 id 可重试）；
   ③ 三个全未设 → `find_tool(pi)` 探到 → pi；
   ④ 探不到 → caller。
   `api_not_configured` 收窄为「api 意图明确（显式选 api 或任一变量已设）但配置
   不齐」；纯未配置不再报错，落链下探。**轮中不级联**：api/pi 失败照旧
   `extraction_failed`/`tool_timeout`，不静默换道——失败原因诚实，原文已落盘可
   人工重做。判定写进 result/台账既有 `extract_backend`（值域扩三）。
2. **caller 档行为**：发送/等待/回读/断轮/落盘/webhook 照旧；**跳过抽取与逐字
   校验**，干净回复全文文件（`<id>.md`）不写。result `ok:true`、
   `extract_backend:"caller"`；选段空值（`questions:[]`、`conclusion:""`、
   `blocked:""`、`next_skill:""`、`needs_user:false`、`rewritten:{}`），语义＝
   **尚未识别**，判别器只有档位字段。`rewritten:{}` 同值异义（api/pi＝全部逐字
   命中；caller＝未校验）靠档位消歧。result 的 `run_record` 指本轮原料
   （`.turn.md`）——尚无干净全文，不假装有；`raw_record`/`turn_record` 照旧。
   学习：不产案例、不注入 few-shot；机械反馈结算照常，`next_skill` 空按「无预测」
   结算。caller 自抽产物不在脚本逐字校验防线内，文档如实声明。
3. **turn_text 与回声剥离**：caller 档通知内联 `turn_text`＝**执行者回复首行起
   的正文，不含回声行**；本轮原料（`.turn.md`）仍含回声、是落盘原件，两者不再
   逐字相同，差别写进 SKILL/REFERENCE。result caller 档带同一份 turn_text
   （投影不变量：通知字段必须先是 result 字段）。
   - **断轮复用现有三级回退链**（❯＋锚点 → 含锚点最后一行 → 整段），不为 caller
     另写切分，api/pi 共用的断轮行为不动。
   - **回声剥离是确定性机械步**（ADR-0002 分工不变，不用模型）：脚本手里有发送
     提示词原文——从断轮切点（回声行）起，把段首逐行去空白归一后与提示词归一
     文本做前缀匹配，消耗掉回声及其软换行续行，首个不匹配行即回复首行。
   - **绝不交白卷**：回声未命中 → turn_text 内联整段（回读全文），带
     `turn_text_warning:"whole_segment"`（可能含旧轮）；TUI 折叠回声（如「首行
     ＋…」形态）或匹配发散 → **不猜**，退回首行起整段内联（含回声行）＋
     `turn_text_warning:"echo_degraded"`。
   - `turn_text_warning` 是**条件字段、仅 caller 档、仅异常时携带**，值域
     `whole_segment|echo_degraded`；缺席＝干净剥离。一个字段覆盖全部异常形态，
     不新增恒带字段；`anchor_found` 维持只在 result，云端判别由 warning 承载。
4. **通知投影新契约**（supersede ADR-0014 决定 2 的白名单形状）：
   - **恒带 7 字段**：`ok`、`release_id`、`target`、`needs_user`、`elapsed_s`、
     `complete`、`extract_backend`（新增）。
   - **有内容才带 6 字段**：`questions`（空 `[]` 缺席）、`blocked`、`conclusion`、
     `next_skill`、`rewritten`（`{}` 缺席）、`complete_reason`（空缺席）。
     **缺席即空**写进契约。
   - caller 档另带 `turn_text`（恒有内容）＋条件 `turn_text_warning`。
   - **`run_record` 整个移出通知**；要路径的消费方读 result.json。
   - 瘦身只作用于成功通知投影；result.json 恒全量；error 通知照旧全量不套白名单。
     字节一致、序列化一次、落盘先于 POST、唤醒优先等 ADR-0014 其余契约不动。
5. **交付与代价**：内联**不设上限、不截断**（逐字哲学——截断＝脚本改写原料）；
   超大正文 POST 失败走既有 `webhook:failed` 诊断，整轮不连累，本机 result/notify
   仍全量可读。文档写明兜底档代价：① 云端可能取不到超大正文（该轮内容需到发送端
   看）；② caller 档单次唤醒成本高于 api/pi，成本验收口径不适用兜底档；
   ③ run_record 移出后云端选项卡不再附记录位置；④ `whole_segment` 标记的正文
   可能混旧轮，识别责任在调用方。
6. **hc/herdr-app 维持硬依赖**：缺失 → skill 拒绝执行、零发送，README 指引引导
   安装；**不存在无 Herdr 的降级轮**——兜底只是「跳过抽取＋内联正文」，不是绕过
   放行/台账/恒可见护栏的人肉通道。

## Considered Options

- **维持二元配置（api 默认、pi 显式备选）**：零配置机器死在受理闸，尽管机械步
  与本轮原料俱在、调用方自己能识别。否——caller 是零配置地板。
- **自动探测（ADR-0005 原否决）**：原理由是「隐式魔法、走了哪条路变猜谜」。
  翻案采纳：`extract_backend` 逐轮记录使猜谜理由失效；显式 env 恒赢保住部署级
  意图，半配置笔误仍大声失败（任一 `LOOP_API_*` 已设即 api 意图）。
- **轮中级联降档（api 失败换 pi、pi 失败换 caller）**：失败原因被静默换道掩盖，
  违背「失败诚实、原文可人工重做」哲学。否。
- **回声未命中不塞全量（拷问中轮旧口径）**：会把找不到回声的轮静默当空回复交回。
  推翻——绝不交白卷，整段内联＋`whole_segment` 标记，成色责任交调用方。
- **为 caller 另写断轮切分**：两套切分漂移风险。否——复用三级回退链；「整首行
  模糊匹配」增强属断轮行为变更，另裁。
- **回声剥离用模型判断**：违背 ADR-0002 机械/模型分工。否——归一前缀匹配是
  确定性机械步；剥离不精确时不猜，降级标记。
- **内联截断/字节上限**：截断＝脚本改写原料，打破逐字哲学。否——POST 失败走
  既有 `webhook:failed`，故障域清晰。
- **`anchor_found` 进通知**：新增恒带字段膨胀载荷。否——云端判别由条件
  `turn_text_warning` 承载。
- **run_record 留在通知**：云端读不到本机路径，只制造「已核验本机原文」假象
  （ADR-0014 已否决读取，此处连位置也移出）。否。
- **无 Herdr 的人肉通道**：绕过放行/台账/恒可见护栏，违背走管道纪律。否
  （Out of Scope）。

## Consequences

- 装齐 Herdr＋Python 就能零配置跑通一轮；抽取 endpoint 从必需降为推荐
  （README 同步）。
- **迁移**：旧部署纯未配置的，跟新代码后不再启动即 `api_not_configured`，而是
  落 pi/caller——行为变化由规则链与文档声明承载，不主动迁移存量终端。
- caller 档唤醒载荷含全文，单次唤醒成本上升；成本验收口径（ADR-0014）不适用
  兜底档。
- 传话例程加 caller 分支：先看 `extract_backend`，caller → 读 `turn_text` 自行
  识别（见 warning 知正文成色），再走正常选项卡/交回；全部字段按「缺席即空」
  判读，不再引用 run_record。
- 词表新增「调用方自抽（Caller Extraction）」；「抽取」「唤醒」「通知投影」
  词条同步三档与新白名单口径。
- 测试零新增缝：定档矩阵、caller 路径、通知形状、回声剥离全部钉在既有 main
  入口级缝（ADR-0016/0018）；变异验证照仓库纪律。