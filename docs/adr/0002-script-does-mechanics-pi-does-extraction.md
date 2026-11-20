---
status: accepted
---

# 脚本干机械活，pi 干提炼（C）

> 术语正名（2026-11-19）：本文的「提炼」已正名为「抽取」（Extraction）——逐字摘出原文连续片段并做子串校验，「提炼有损」的说法随之作废；定义以根目录 `GLOSSARY.md` 为准，正文保留作历史。

> 字段补记（2026-11-20）：`summary` 已从抽取契约移除——原文才是重点，干净回复全文（run_record）即回看与展示对象。固定 JSON 现为 `needs_user`、`questions`、`conclusion`、`blocked`（`reply` 落盘不内联）；pi 若仍多给 `summary`，脚本直接忽略，不校验、不计入 rewritten。下文字段列表中的「summary」按此移除理解，正文保留作历史。

> 机制补记（2026-11-20）：pi 的职责由「只抽取」扩为「裁剪＋抽取」。整屏原料逐字落 `.loop/runs/<放行id>.raw.md`（审计）；pi 读它，先裁剪出干净回复全文 `reply`（剔掉 logo/思考计数/工具痕迹/转圈行/空行/输入框/状态栏，逐字保留回复正文）落 `.loop/runs/<放行id>.md`（即 run_record、调用方回看对象），再摘选段字段；输出 JSON 增加 `reply`（落盘、不内联进 result），`questions` 改「问题＋选项＋推荐整块」摘取，result 增加 `raw_record`。下文「完整原文落 .md」「pi 输出五字段」「回看 .md 原文」按此更新理解；裁剪、整屏原料、干净回复全文的定义以 `GLOSSARY.md` 为准，正文保留作历史。

用户 2026-11-19 16:40 撤销了 ADR-0001 的「一个开 bash 的 pi 全包」（原 Q2 选 B），改回 C：确定性的脚本（`dispatch.py`）做发送、等待、取回这些机械活，pi 只读落盘的原文做提炼。理由是机械活由代码做就能机械保证「原文照发、只发一次、不 `--force`、不替对方答」，不必再靠一个很慢的本地模型带 bash 去守这些安全约束，也不必为看管它而搭垫片、绊线、审计那一整套。

## 架构

一个 skill 目录，含 `SKILL.md`、`scripts/dispatch.py`、`prompts/extract.md`。

`dispatch.py`（纯代码，不经过模型）：

1. 读提示词文件并记 sha256。
2. `hc get` 核对目标 idle。
3. `hc send --if idle` 发送，拿 `turn` 基线。
4. `hc wait` 循环：状态变化即醒、到点 check-in、墙钟超时兜底。
5. `hc read` 取回。
6. 完整原文逐字落盘到 `.loop/runs/<放行id>.md`。
7. 调 pi 提炼。

pi 只做提炼：读原文文件，输出固定 JSON（`needs_user`、`questions`、`summary`、`conclusion`、`blocked`）。

异常不进 pi：非 idle、超时、blocked、`complete=false`、退出码非零，脚本直接返回 error。

调用方拿到的永远是同一个形状，底下是 claude 还是 grok 都一样。

## Considered Options

见 ADR-0001 的 A / B / C。0001 采用 B；本 ADR 改回 C 并取代 0001。

## Consequences

- 「原文照发、只发一次、不 `--force`、不替对方答」由 `dispatch.py` 机械保证，不再是提示层约束。pi 不碰 `hc`、不开 bash，只读文件，所以 ADR-0001 为看管模型而设的垫片、绊线、全量审计、外层弹框轮询都不必要（见「外层脚本的职责边界」票的收口评论）。
- 提炼有损：pi 的 JSON 是摘要，不是原文。所以「原文落盘 + `complete` 标记」是兜底；涉及要不要批准、要不要改接口的判断，必须回看 `.loop/runs/<放行id>.md` 的原文，不能只凭 JSON。
- 异常一律由脚本返回 error，不进 pi，调用方据 error 形状处置。
- 等待循环的具体参数（check-in 间隔、落定复核、墙钟超时怎么计）仍由「等待策略」票裁定，本 ADR 只定「脚本做等待、有墙钟超时兜底」这一层。
