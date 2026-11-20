---
name: loop
description: 走管道：每轮要回复执行者的信息都经 loop 推回——把用户放行的提示词发进 herdr 目标会话并取回结果（pi 裁剪＋抽取成 JSON）；监听只读随时可，仅报错时可直推（仍须放行）。可选 --reset 先发 /new /clear /compact。手里有放行 id、提示词文件、目标地址，要发送并取回新结果时用。
---

# loop：走管道

**走管道**：每一轮要发给执行者的回复，都经本 skill 的 `scripts/dispatch.py` 推回。和用户讨论定稿后，写放行文件、调 dispatch 发送＋取回、判读结果、再讨论——下一轮回复仍走管道。监听（`hc get`/`hc read` 只读）随时可；仅当 dispatch 报错才可直推，直推内容仍须用户放行。

dispatch 做发送、等待、取回、落盘这些机械活，pi 读整屏原料做裁剪＋抽取；异常由脚本直接返回 error，不进 pi。架构见 `docs/adr/0002`，用词见根目录 `GLOSSARY.md`；error kind 表、机械保证、发布前检查见 `REFERENCE.md`。

## 每轮循环（走管道）

1. **讨论定稿**：和用户定出要回复执行者的全文。完成判据：用户对全文说「发」，拿到唯一放行 id（对内容方向说「行」不算放行）。
2. **写放行文件**：全文写成 UTF-8 文件，放 `$TMPDIR/loop/releases/<放行id>.txt`（目录不存在就建），放行后一字未改。完成判据：文件就绪（dispatch 核对 sha256，改动即拒发）。
3. **走管道推回**：调 dispatch 发送＋取回（见「调用」「交付」）。完成判据：dispatch 返回、`.result.json` 生成。用 `--reset` 时该放行一并授权重置前置＋主任务（前置是枚举里可预知的固定命令）。
4. **判读结果**：读 `.result.json`＋`run_record`，整理成选项卡给用户。完成判据：用户看到结果、给出下一轮方向。
5. 回到 1，下一轮回复仍走管道。

## 调用前

本机装有 `hc`、`pi`、`uv` 与 Python ≥ 3.10 的解释器（脚本要求 `>=3.10`）。工具先按 PATH 找，PATH 没有时脚本自动探 `~/.local/bin`、homebrew、nvm node bin——后台 job 缺 PATH 也能起；探不到才报 `missing_tool`。PATH 由脚本兜底，**不需手写 export PATH 包装脚本**；后台直接用调用方自己的 bg 机制（见「交付」）。

## 调用

```bash
uv run --no-python-downloads scripts/dispatch.py \
  --target <终端地址> --release-id <放行id> --prompt-file <提示词文件> \
  --expect-title <标题子串> --expect-proc <前台进程名子串>
```

- 一律 `uv run`：脚本头部声明 `requires-python = ">=3.10"`、无第三方依赖；`--no-python-downloads` 只用机器上已有解释器。解释器低于 3.10 直接返回 `error(kind=python_too_old)`。
- `--expect-title` / `--expect-proc`：身份预检，强烈建议都传（grok 传 `grok`、claude 传 `claude`）；`--expect-proc` 匹配 foreground.name/argv[0]/status.app 任一（claude 的 name 可能是版本号，认 argv[0]/app），不符即拒发。
- `--reset {none,new,clear,compact}`（默认 `none`）：主任务前的重置前置。选 `new`/`clear`/`compact` 就在同一 target 先 `--if idle` 发字面命令 `/new`/`/clear`/`/compact`，等落定（idle/done＋屏幕静置）再发主任务；`none` 即单 prompt，向后兼容。**发哪条由调用方按目标 agent 认哪个挑**（claude/grok 的 `/new`≡`/clear` 都认，只认 `/new` 的挑 `new`），脚本不按 agent 映射。前置不抽取、不落 run_record；前置或主任务任一步发不出即整轮停，error 带 `reset_sent` 标明部分状态。`/clear` 是本地命令、可能不进 working，前置落定用屏幕静置而非 turn 判据。
- 等待与抽取默认值一般不用动：墙钟 3600 秒（主任务发送成功起计）、check-in 180 秒、落定静置 10 秒 × 2 次、pi 抽取护栏 1200 秒（`--wall-clock` / `--check-in` / `--settle-seconds` / `--settle-checks` / `--extract-timeout` 可覆盖）。读取固定 `hc read --lines 0`。
- `--loop-dir` 默认 `$TMPDIR/loop`（随 OS 临时清理，记录不持久）；`--prompt-file` 传 `$TMPDIR/loop/releases/<放行id>.txt`；`--pi-model` 透传 `pi --model`。

## 交付：后台跑＋完成通知

一轮最长约 80 分钟（pi 抽取实测每轮 5–11 分钟）。用调用方自己的后台任务跑 dispatch、靠完成通知收结果，再读 `$TMPDIR/loop/runs/<放行id>.result.json`：

- **DS Bot（默认）**：`run_in_background` 跑 dispatch → job 完成通知唤醒 → 读结果文件。
- **Grok Bot**：配 `LOOP_WEBHOOK_URL`＋`LOOP_WEBHOOK_SECRET`（示例见 `webhook.env.example`），dispatch 完成时 POST 结果 JSON 唤醒它 → 读结果文件。
- **其他调用方**：仿 DS Bot（后台任务＋完成通知）。

结果文件永远是事实源；webhook 只是通知，发失败不连累整轮（result 记 `webhook: off/sent/failed`）。短超时包装器（`hc exec` 默认 60 秒/上限 600 秒、超时不够的 bash）会到点把 dispatch 连同子进程杀掉、留下 `phase=waiting` 台账——给不了足够超时就用上面的后台路径。

## 返回契约（每轮判读）

stdout 恰好一行 JSON，同一字符串写入 `$TMPDIR/loop/runs/<放行id>.result.json`（成功与 error 都写，逐字一致，是**最终交付**）。成功（退出码 0）：

```json
{"ok": true, "target": "t7", "reset": "none", "turn": 3, "status": "idle",
 "complete": true, "complete_reason": [], "anchor_found": true, "elapsed_s": 690.5, "chars": 1834,
 "run_record": "$TMPDIR/loop/runs/<放行id>.md", "raw_record": "$TMPDIR/loop/runs/<放行id>.raw.md",
 "prompt_sha256": "…", "rewritten": {}, "webhook": "off",
 "needs_user": false, "questions": [], "summary": "…", "conclusion": "…", "blocked": ""}
```

- `run_record`（`.md`）是 pi 从整屏**裁剪出的干净回复全文**（剔 logo/思考计数/工具痕迹/转圈行/空行/输入框/状态栏，逐字保留正文），是回看对象；`raw_record`（`.raw.md`）是整屏原料（逐字读回，制表符按终端制表位展开为空格，审计用）。error 时尚无干净全文，`run_record` 退回指整屏原料。
- `needs_user`（布尔）：回复里是否有需用户决定的事。四个选段字段是 pi 从原文**逐字摘出**的句子（语义见 `prompts/extract.md`）：`questions` 每条问题/审批一个元素（问题＋选项＋推荐整块）、`summary` 关键段、`conclusion` 结论段（无则 `""`）、`blocked` 弹框原句（无则 `""`）。干净全文 `reply` 不进本 JSON，落在 `run_record`。
- `rewritten`：脚本对选段字段逐一做原文子串校验，搜不到的在此标出、值为最接近原文片段；`{}` 即全部逐字命中。**非空时对应字段是 pi 的改写，不可当原话引用；`reply` 被标时回看 `raw_record`。**
- `reset`：本轮前置类型（`none`/`new`/`clear`/`compact`）；`complete`/`complete_reason`：完整性判定，只看折叠标记（成功恒 `complete:true`）；`anchor_found`：提示词回声是否还在当前屏——仅信息，长任务回声滚出屏为 `false`，不影响 `complete`；`elapsed_s`：主任务发送成功到取回落定的秒数，不含前置与抽取；`webhook`：完成通知状态。
- **四个选段只摘原文片段、不保证覆盖全文**；要不要批准、要不要改接口的判断，必须回看 `run_record`，不能只凭 JSON 选段。

拿到结果整理成**选项卡**交给用户，原文不直接转给用户。失败（退出码 1；参数错误 2）返回 `{"ok": false, "error": {"kind", "message", "hints"}}`；kind 含义与调用方动作见 `REFERENCE.md`。监听只读随时可；仅报错时可直推（仍须放行）。

## 本地记录（默认 `$TMPDIR/loop`，随 OS 临时清理）

- `releases/<放行id>.txt`：放行提示词（调用方写，dispatch 原文照发）。
- `runs/<放行id>.raw.md`：整屏原料（审计）。
- `runs/<放行id>.md`：干净回复全文（回看对象 `run_record`）。
- `runs/<放行id>.json`：台账，先记账后发送；一个放行只跑一轮，至多发送成功两次（前置＋主任务各一次），台账存在即拒同 id 重发。
- `runs/<放行id>.result.json`：最终交付，后台调用靠读它收结果；重发拒绝仍以台账为准。

记录与台账都随 OS 临时清理、不持久：事后审计与拒重发保护止于重启（放行 id 每次唯一，跨重启复用风险低）。要长期留存某轮，自行另存。

## 护栏

调用方要守的只剩一条：**先放行，后调用**。其余机械保证由脚本兜底，细节见 `REFERENCE.md`。