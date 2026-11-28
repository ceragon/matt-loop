---
status: accepted
---

# loop 收窄为纯传话工具：抽取与学习整线退役，reply＝剥回声断轮段

> 本 ADR 是两轮裁定（第一轮七问＋第二轮 Q1–Q7）的决策记录。supersede：
> 0002（仅「pi 干抽取」半篇）、0005、0008、0012、0023、0026；0013、0014、
> 0019、0020、0022 各加被修订注记。正文留历史，定义以根目录 `GLOSSARY.md` 为准。

## 背景

loop 从传话工具长成了「抽取＋预测＋学习」的复杂形态：三档规则链 api→pi→caller
（ADR-0005/0026）、逐字校验与 `rewritten`、`next_skill` 预测（ADR-0008）、机械
反馈＋space 隔离 few-shot（ADR-0012/0023）、通知投影恒带 7＋条件 6（ADR-0026）。

用户裁定：判据只按**传话助手三职责**（交回结果、呈报拍板、回传原话，见 GLOSSARY
「传话助手」词条）＋奥卡姆剃刀——凡不是这些职责必需的机制一律删。caller 自抽兜底
（#139/#140）已经实证：机械步＋本轮原料足以完成交付，识别责任本就在调用方；把该
语义转正为唯一路径后，三档、校验、预测、学习全部失去存在理由。dispatch 收窄为
**发送 → 等落定 → 回读 → 断轮 → 剥回声 → 投递**，全程不调模型。

## 决定

1. **职责收窄**：抽取整条线退役——api/pi/caller 三档、`prompts/extract.md`、
   逐字校验（`verify_extraction`/`verify_reply`）、`rewritten`、选段字段
   （`needs_user`/`questions`/`conclusion`/`blocked`）；学习与预测整条线退役——
   `learning.py`、`next_skill` 预测与可见性纪律、机械反馈、近期 skill 序列、
   few-shot 注入。ADR-0002「脚本干机械活」半篇继续有效，且是本次收窄的分工依据。
2. **reply＝回复全文**（第二轮 Q1=A）：剥掉回声后、从本轮提示词之后到断轮段末尾
   为止的**逐字原文**；TUI 状态行等杂物留在正文里，由调用方模型自行跳过，脚本不做
   杂物过滤。回声剥离沿用 ADR-0026 决定 3 的确定性归一前缀匹配（含「提示词归一
   文本被完整消耗才算干净」的收紧判据），从 caller 专属转正为核心路径。
   `<id>.md` 不再写；`run_record` 指 `.turn.md`；`.raw.md`/`.turn.md` 审计件照旧。
   reply 不设上限、不截断（延续 #139 Q10）。词表「干净回复全文」退役，新立
   「回复全文（Reply）」。
3. **异常统一 `complete:false`＋`complete_reason`**（Q2=B、Q3=B）：不设
   `reply_warning`/`turn_text_warning`，异常只有一条可判别通道。值域沿用列表形态、
   可多值并存：
   - `fold_markers`：折叠判据（ADR-0019）照旧；
   - `echo_degraded`：剥离发散（部分消耗／消耗完无剩余行），reply 退回首行起
     整段（含回声行）；
   - `whole_segment`：回声完全没命中，reply 退回整段回读（可能混旧轮）；
   - `truncated`：回读触及 1000 行上限（ADR-0013），历史开头可能已滚出范围——
     保守标记，宁多标待复核、不静默丢开头。
   `complete:false` 不阻断投递、不新增 error（与 ADR-0019 降级哲学同构）。
4. **通知契约**（supersede ADR-0026 决定 4、ADR-0014 决定 2/3）：
   - 成功恒带只两键：`ok:true`＋`reply`；
   - 仅异常条件带 `complete:false`＋`complete_reason`（缺席＝完整且干净）；
   - error 通知只三键：`ok:false`＋`error{kind,message,hints}`；
   - `target` 不再随通知交付，传话助手用自己恒加载配置里写死的会话名字面量
     （ADR-0025）新建放行；
   - 字节一致、序列化一次、落盘先于 POST、唤醒优先、早期 error 路径安全、
     先 notify 后 result 等 ADR-0014 其余契约不动。
5. **result.json 十键**（Q4=B）：`ok`、`release_id`、`target`、`elapsed_s`、
   `complete`、`complete_reason`、`raw_record`、`turn_record`、`run_record`
   （＝`.turn.md`）、`reply`。`anchor_found` 砍——回声是否命中统一看
   `complete_reason`；`extract_backend`、`rewritten`、选段字段与全部学习键退役。
   error 的 result.json 仍全量（诊断、部分状态、`message/hints`），只去掉退役键。
   台账（重复放行闸）原样不动，`echo_found` 等过程键仍在台账。
6. **退役清单**：env `LOOP_API_BASE/KEY/MODEL/MAX_TOKENS`、
   `LOOP_EXTRACT_BACKEND`、`LOOP_LEARNING`、`LOOP_LEARNING_DIR`；flag
   `--extract-timeout`、`--pi-model`；受理里的「抽取后端配置」静态闸；error kind
   `api_not_configured`、`extraction_failed`、`extraction_rewritten`、
   `extraction_invalid` 及 pi/uv 的 `missing_tool` 分支；`deps.extract`、
   `Extraction`、`learning_prepare`/`learning_settle` 阶段与 `extract_verify`
   的抽取语义。
7. **存量学习数据留置**（第一轮 Q5=A）：
   `${XDG_DATA_HOME:-~/.local/share}/matt-loop/learning/` 代码不读写、不删除；
   文档给一行清理路径（用户可自行整目录删除）。
8. **ADR 手术**（Q5=A）：supersede 0002（仅「pi 干抽取」半篇）、0005、0008、
   0012、0023、0026；注记 0013（校验语料段退役）、0014（白名单与 error 通知段
   被 supersede）、0019（白名单扩展与学习结算段退役）、0020（Extraction/学习缝
   退役）、0022（`next_skill_feedback`/`learning_warning` 两键退役）。原则：
   留一篇「有效」ADR 描述已不存在的模块，比没有 ADR 更误导未来读者。
9. **测试六删四改，不新增**（Q6=A）：
   - 整删：`test_dispatch_learning.py`、`test_learning_session.py`、
     `test_dispatch_next_skill.py`、`test_dispatch_api_extract.py`、
     `test_extract_fields_consistency.py`、`test_dispatch_no_summary.py`。
   - 改写：`test_dispatch_notify.py`（两键＋异常条件契约）、
     `test_dispatch_turn_cut.py`（留断轮/折叠/剥回声，删逐字校验段；剥回声转正
     为核心路径）、快照测试（删 `LOOP_LEARNING` 两态，按 ADR-0021 做改前后双向
     diff）、`tests/helpers.py`（EXT/EXT5/notify_fields 正典重述为新契约，
     不 import dispatch 常量）。
   - 不动：受理/发送/等待/回读/ledger/space/领养/webhook 各测试。
   - 纪律：每条新增/翻转断言做变异验证。
10. **实施三切片，②③不并**（Q7=A）：
    - ① 删学习/预测整线：`learning.py`、调用点、开关、`next_skill` 字段与
      extract.md 选段规则、对应测试整删；存储不碰。独立可合，外部行为只少键。
    - ② 删抽取整条线＋reply 语义＋result/error 瘦身（含决定 6 的 env、flag、
      error kind 退役）。②后通知暂走旧白名单（少字段版）。
    - ③ 通知收成两键＋异常条件契约落地，SKILL/REFERENCE/relay-routine/README
      全量同步。
    - 中间态可运行、可快照（ADR-0021 迁移护栏）；每片 PR 显式列出快照 diff 并
      写明「有意的契约变化」。并片被否：diff 变大、中间态失去快照护栏。

## Considered Options

- **只砍预测＋学习，保留抽取**：抽取仍要养三档后端、逐字校验与一份 prompt，而
  识别责任终归调用方。否（第一轮 Q1=B，比原方案更狠）。
- **正则剔 TUI 杂物**：把判断性工作伪装成机械步，违背 ADR-0002 分工；杂物形态
  跨 agent kind 不可穷举。否（Q1=A 接受杂物留存）。
- **`reply_warning` 条件键**（第二轮推荐案）：与 `complete_reason` 同构，两条
  异常通道冗余；统一走 `complete:false` 一条通道。否（Q2=B）。
- **接受云端静默截断**：ADR-0014 决定 9——云端传话助手只能拿 webhook 正文、
  读不到 result.json；1000 行截断在正文无痕＝旧结论混入而不自知。否（Q3=B）。
- **保留 `anchor_found`**：回声命中与否已由 `complete_reason` 可判别，冗余。
  否（Q4=B）。
- **②③并片**：diff 变大，中间态失去快照护栏。否（Q7=A）。

## Consequences

- 零配置地板成为唯一路径：装齐 Herdr＋Python 即可跑，不再有抽取 endpoint 配置面。
- 识别责任全在调用方：传话助手读 reply 自行判断是否需拍板、结论为何；选项卡／
  一句话交回纪律不变。
- 「新任务」三件套变两件套（换显示名＋推荐重置）：「已编辑：新任务，上轮预测
  不适用」标签随预测退役，ADR-0015 决定 8 的标签句按此理解。
- `ok:true` 不再蕴含 reply 干净：`whole_segment` 可能混旧轮、`echo_degraded`
  含回声行、`truncated` 丢开头——云端凭条件键判别，复核责任在调用方。
- 超大正文 POST 失败走既有 `webhook:failed` 诊断，整轮不连累（ADR-0026 决定 5
  的代价声明继续有效）。
- 存量学习数据留置不删；清理路径随切片③写进 README。
- 隐私：本 ADR 与相关快照样例遵守公开仓库红线。