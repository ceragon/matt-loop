---
name: loop
description: 跑一轮分发：把用户已放行的提示词文件发进 herdr 目标会话，等落定、取回原文落盘、pi 提炼成固定 JSON。手里有放行 id、提示词文件和目标终端地址，要发送并取回新结果时用。
---

# loop：分发脚本 + pi 提炼

调用方给出**放行**（用户对提示词全文说「发」，每次放行有唯一 id）、提示词文件、目标终端地址；`scripts/dispatch.py` 做发送、等待、取回、落盘这些机械活，pi 只读原文做**提炼**。异常由脚本直接返回 error，不进 pi。架构见 `docs/adr/0002`，用词见根目录 `GLOSSARY.md`。

## 调用前

1. 提示词全文已经用户放行，拿到唯一放行 id。对内容方向说「行」不算放行。
2. 提示词已写成 UTF-8 文件，放行后一字未改（脚本会核对 sha256，改动即拒发）。
3. 本机 `hc`、`pi`、`uv` 可用，且机器上已装有 Python ≥ 3.10 的解释器（脚本要求 `>=3.10`）。Mini 上非交互环境的 PATH 里没有 node，起 pi 前需显式补 PATH。

## 调用

```bash
uv run --no-python-downloads scripts/dispatch.py \
  --target <终端地址> --release-id <放行id> --prompt-file <提示词文件> \
  --expect-title <标题子串> --expect-proc <前台进程名子串>
```

- 一律用 `uv run` 跑，脚本头部声明了 `requires-python = ">=3.10"`、无第三方依赖。`--no-python-downloads` 保证只用机器上已有的解释器、不下载新 Python；`uv run` 会在 `~/.cache/uv` 建一个很小的 venv，那是它正常的运行环境。解释器低于 3.10 时脚本直接返回 `error(kind=python_too_old)`。
- `--expect-title` / `--expect-proc`：身份预检，强烈建议都传（如 grok 目标传 `--expect-proc grok`）；不符即拒发。
- 等待与提炼参数已裁定默认值，一般不用动：墙钟 3600 秒（发送成功起计、不重置）、check-in 180 秒、落定静置 10 秒 × 2 次复核、pi 提炼护栏 1200 秒（`--wall-clock` / `--check-in` / `--settle-seconds` / `--settle-checks` / `--extract-timeout` 可覆盖）。读取固定 `hc read --lines 0`（只取当前屏）。
- `--loop-dir` 默认 `./.loop`；`--pi-model` 可透传 `pi --model`。
- 一轮最长跑「墙钟上限 + 提炼护栏」这么久；期间不要重复调用。

## 返回契约

stdout 恰好一行 JSON。成功（退出码 0）：

```json
{"ok": true, "target": "t7", "turn": 3, "status": "idle",
 "complete": true, "complete_reason": [],
 "elapsed_s": 690.5, "chars": 1834,
 "run_record": ".loop/runs/<放行id>.md", "prompt_sha256": "…", "rewritten": {},
 "needs_user": false, "questions": [], "summary": "…", "conclusion": "…", "blocked": ""}
```

- **原文为屏幕逐字读回，制表符按终端制表位展开为空格**（终端渲染所致，非 hc 或脚本改写；读回后无法检测原文是否含 tab）。
- `complete` / `complete_reason`：脚本的完整性判定。判据两条：`anchor_missing`（提示词最后一行非空文本在回读里搜不到，回复起点被挤出屏幕）、`fold_markers`（回读含 ▲ 或 ▼，目标 TUI 折叠了内容）。任一命中即落盘后返回 `error(kind=incomplete_result)`，不进 pi；所以成功输出恒 `complete: true`。hc read 自己的 `complete` 字段只记台账，不作判据（对 grok 不可靠）。
- `elapsed_s`：发送成功到取回落定的秒数，不含提炼。
- `needs_user`（布尔）：pi 判断回复里是否有需要用户决定的事。其余四个文本字段是 pi 从原文**逐字摘出**的句子（语义见 `prompts/extract.md`）：`questions` 每条问题/审批请求一个元素，`summary` 关键段，`conclusion` 结论段（无则 `""`），`blocked` 弹框原句（无则 `""`）。
- `rewritten`：脚本对四个文本字段逐一做原文子串校验（含去空白折叠），搜不到的字段在此标出，值为最接近的原文片段，如 `{"summary": "…"}`；`{}` 表示全部逐字命中。全部搜不到则返回 `error(kind=extraction_rewritten)`。**`rewritten` 非空时，对应字段是 pi 的改写，不可当作原话引用。**
- **提炼有损**：这些字段只是原文的选段。涉及要不要批准、要不要改接口的判断，必须回看 `run_record` 原文，不能只凭 JSON。

失败（退出码 1；参数错误退出码 2）：

```json
{"ok": false, "error": {"kind": "…", "message": "…", "hints": ["…"]}}
```

发送后失败的 error 另带 `elapsed_s`，原文已落盘的另带 `run_record`。常见 `kind`：

| kind | 含义 | 调用方动作 |
| --- | --- | --- |
| `python_too_old` | 解释器低于 3.10，未进入流程 | 改用 `uv run --no-python-downloads` 调用 |
| `duplicate_release` | 该放行 id 已有台账/落盘 | 查台账；重发须用户重新放行、新 id |
| `not_idle` / `identity_mismatch` | 预检不过，未发送 | 整理成选项卡告知用户 |
| `send_refused` | hc 拒发且 done 补发路径不适用或仍失败，未打入 | 同上 |
| `dialog_blocked` | 目标停在弹框（审批/提问/认证） | 告知用户去目标屏幕前处理；不按键、不代答 |
| `timeout` | 墙钟到点，目标仍在工作 | 告知用户；原文未取回 |
| `not_started` / `target_exited` / `target_error` | 发出后未开工 / 程序退出 / 自报 error | 告知用户 |
| `incomplete_result` | 完整性判据命中 | 原文已落盘，请用户回看 `run_record` |
| `extraction_rewritten` | 提炼字段全部未通过逐字校验 | 原文已落盘，请用户回看；可人工重做提炼 |
| `extraction_failed` / `extraction_invalid` / `tool_timeout` | 提炼失败或子进程超护栏 | 原文已落盘，可人工重做提炼 |

拿到任一返回后，把内容整理成**选项卡**交给用户；原文不直接转给用户。

## 本地记录

- `.loop/runs/<放行id>.md`：**原文落盘**，屏幕逐字读回，无任何添加。
- `.loop/runs/<放行id>.json`：台账。先记账后发送；记录 sha256、发送/等待/读取元信息、逐字校验结果、阶段与错误。台账存在即拒绝同 id 重发——一个放行只跑一轮，至多发送成功一次。

## 机械保证（脚本兜底，调用方无需自查）

原文照发、至多发送成功一次、永不 `--force`、不碰 `hc keys`、身份预检、sha256 记账并随成功输出回报、不完整不进 pi、改写字段标 `rewritten`。发送门：首发只 `--if idle`；被拒（`status_mismatch`，零字节进入，不算重发）后 `hc get` 复核确认 `done`，补发前再核一次非 working/blocked，才不带 `--if` 补发一次；仍失败即 error。调用方要守的只剩一条：**先放行，后调用**。
