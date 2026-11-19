---
name: loop
description: 跑一轮分发：把用户已放行的提示词文件发进 herdr 目标会话，等落定、取回原文落盘、pi 提炼成固定 JSON。手里有放行 id、提示词文件和目标终端地址，要发送并取回新结果时用。
---

# loop：分发脚本 + pi 提炼

调用方给出**放行**（用户对提示词全文说「发」，每次放行有唯一 id）、提示词文件、目标终端地址；`scripts/dispatch.py` 做发送、等待、取回、落盘这些机械活，pi 只读原文做**提炼**。异常由脚本直接返回 error，不进 pi。架构见 `docs/adr/0002`，用词见根目录 `GLOSSARY.md`。

## 调用前

1. 提示词全文已经用户放行，拿到唯一放行 id。对内容方向说「行」不算放行。
2. 提示词已写成 UTF-8 文件，放行后一字未改（脚本会核对 sha256，改动即拒发）。
3. 本机 `hc`、`pi` 可用。Mini 上非交互环境的 PATH 里没有 node，起 pi 前需显式补 PATH。

## 调用

```bash
python3 scripts/dispatch.py \
  --target <终端地址> --release-id <放行id> --prompt-file <提示词文件> \
  --expect-title <标题子串> --expect-proc <前台进程名子串> \
  --wall-clock <秒> --check-in <秒> --settle-seconds <秒> --settle-checks <次> \
  --lines <行数> --on-incomplete <error|extract> --extract-timeout <秒>
```

- `--expect-title` / `--expect-proc`：身份预检，强烈建议都传（如 grok 目标传 `--expect-proc grok`）；不符即拒发。
- 后 7 个参数是**待拍板项**（issue #16），裁定前无默认值、必须显式传；推荐值见该票的实现者方案。裁定后此段更新为默认值。
- `--loop-dir` 默认 `./.loop`；`--pi-model` 可透传 `pi --model`。
- 一轮最长跑「墙钟上限 + 提炼护栏」这么久；期间不要重复调用。

## 返回契约

stdout 恰好一行 JSON。成功（退出码 0）：

```json
{"ok": true, "target": "t7", "turn": 3, "status": "idle",
 "complete": true, "complete_reason": [],
 "elapsed_s": 690.5, "chars": 1834,
 "run_record": ".loop/runs/<放行id>.md", "prompt_sha256": "…",
 "needs_user": false, "questions": [], "summary": "…", "conclusion": "…", "blocked": false}
```

- `complete` / `complete_reason`：脚本的完整性判定。判据两条：`anchor_missing`（提示词最后一行非空文本在回读里搜不到，回复起点被挤出屏幕）、`fold_markers`（回读含 ▲ 或 ▼，目标 TUI 折叠了内容）。都不命中才 `complete: true`。hc read 自己的 `complete` 字段只记台账，不作判据（对 grok 不可靠）。
- `elapsed_s`：发送成功到取回落定的秒数，不含提炼。
- `needs_user` / `questions` / `summary` / `conclusion` / `blocked`：pi 提炼产物，语义见 `prompts/extract.md`。
- **提炼有损**：这五个字段是摘要不是原文。涉及要不要批准、要不要改接口的判断，必须回看 `run_record` 原文，不能只凭 JSON。

失败（退出码 1；参数错误退出码 2）：

```json
{"ok": false, "error": {"kind": "…", "message": "…", "hints": ["…"]}}
```

发送后失败的 error 另带 `elapsed_s`，原文已落盘的另带 `run_record`。常见 `kind`：

| kind | 含义 | 调用方动作 |
| --- | --- | --- |
| `duplicate_release` | 该放行 id 已有台账/落盘 | 查台账；重发须用户重新放行、新 id |
| `not_idle` / `identity_mismatch` | 预检不过，未发送 | 整理成选项卡告知用户 |
| `send_refused` | hc 拒发（如 status_mismatch），未打入 | 同上 |
| `dialog_blocked` | 目标停在弹框（审批/提问/认证） | 告知用户去目标屏幕前处理；不按键、不代答 |
| `timeout` | 墙钟到点，目标仍在工作 | 告知用户；原文未取回 |
| `not_started` / `target_exited` / `target_error` | 发出后未开工 / 程序退出 / 自报 error | 告知用户 |
| `incomplete_result` | 完整性判据命中（`--on-incomplete error` 时） | 原文已落盘，请用户回看 `run_record` |
| `extraction_failed` / `extraction_invalid` / `tool_timeout` | 提炼失败或子进程超护栏 | 原文已落盘，可人工重做提炼 |

拿到任一返回后，把内容整理成**选项卡**交给用户；原文不直接转给用户。

## 本地记录

- `.loop/runs/<放行id>.md`：**原文落盘**，屏幕逐字读回，无任何添加。
- `.loop/runs/<放行id>.json`：台账。先记账后发送；记录 sha256、发送/等待/读取元信息、阶段与错误。台账存在即拒绝同 id 重发——一个放行只跑一轮，至多发送一次。

## 机械保证（脚本兜底，调用方无需自查）

原文照发、至多发一次、只 `--if idle`、永不 `--force`、不碰 `hc keys`、身份预检、sha256 记账并随成功输出回报。调用方要守的只剩一条：**先放行，后调用**。

<!-- 待拍板（issue #16，实现者方案已交会话，等用户确认后补写）：
     制表符免责声明的落点（屏幕读回会把 tab 展开为空格，E1 已归因为终端渲染）。 -->
