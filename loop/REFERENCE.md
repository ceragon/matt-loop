# loop REFERENCE（按需查阅）

SKILL.md 的披露参考：只在对应分支才读。error kind 表在拿到 error 时查；机械保证是脚本兜底细节；发布前检查在把 `loop/` 公开前跑。用词见根目录 `GLOSSARY.md`。

## error 返回契约

失败退出码 1（参数错误退出码 2，只有 argparse 报错、无 JSON）。stdout 一行、同一字符串写 `.result.json`：

```json
{"ok": false, "error": {"kind": "…", "message": "…", "hints": ["…"]}}
```

发送后失败的 error 另带 `elapsed_s`；原文已落盘的另带 `run_record`。用了 `--reset` 的 error 另带 `reset`（类型）与 `error.reset_sent`（前置是否已发出，标明「前置已发／主任务未发」这类部分状态）。放行 id 本身非法（`bad_release_id`）或参数错误的 error 只有 stdout——没有可安全落盘的文件名。

## error kind 表

| kind | 含义 | 调用方动作 |
| --- | --- | --- |
| `python_too_old` | 解释器低于 3.10，未进入流程 | 改用 `uv run --no-python-downloads` 调用 |
| `duplicate_release` | 该放行 id 已有台账/落盘 | 查台账；重发须用户重新放行、新 id |
| `not_idle` / `identity_mismatch` | 预检不过，未发送 | 整理成选项卡告知用户 |
| `send_refused` | 前置或主任务 hc 拒发且 done 补发不适用或仍失败；看 `reset_sent` 判断前置是否已发 | 同上 |
| `dialog_blocked` | 目标停在弹框（审批/提问/认证） | 告知用户去目标屏幕前处理；不按键、不代答 |
| `timeout` | 墙钟到点（前置或主任务），目标仍未落定 | 告知用户；前置超时则主任务未发、原文未取回 |
| `not_started` / `target_exited` / `target_error` | 发出后未开工 / 程序退出 / 自报 error | 告知用户 |
| `incomplete_result` | 折叠标记命中（▲▼ 藏了内容，当前屏看不到完整回复） | 原文已落盘，请用户回看 `run_record` |
| `extraction_rewritten` | 抽取字段全部未通过逐字校验 | 原文已落盘，请用户回看；可人工重做抽取 |
| `extraction_failed` / `extraction_invalid` / `tool_timeout` | 抽取失败或子进程超护栏 | 原文已落盘，可人工重做抽取 |

完整性硬判据只剩 `fold_markers`（回读含 ▲ 或 ▼，目标 TUI 折叠了内容，当前屏看不到完整回复）：命中即落盘后返回 `error(kind=incomplete_result)`，不进 pi。锚点（提示词第一个非空行前 24 字符）降级为信息字段 `anchor_found`：完整性检查在 turn>基线＋落定之后，本轮确已被处理，长任务提示词回声滚出当前屏（`--lines 0`）是常态、回复仍在屏底，故锚点搜不到不再阻断（PAW 实测多次误判后降级）。hc read 自己的 `complete` 字段只记台账、不作判据。

监听（`hc get`/`hc read` 只读）随时可；直推（绕过 dispatch 主动发）仅在报错时，且内容仍须用户放行。正常回复一律走管道。

## 机械保证（脚本兜底，调用方无需自查）

原文照发、一个放行至多发送成功两次（重置前置＋主任务，各一次）、永不 `--force`、不碰 `hc keys`、身份预检、sha256 记账并随成功输出回报、不完整不进 pi、改写字段标 `rewritten`。重置前置按枚举校验、与主任务共用同一发送门，任一步发不出即整轮停。

发送门：首发只 `--if idle`；被拒（`status_mismatch`，零字节进入，不算重发）后 `hc get` 复核确认 `done`，补发前再核一次非 working/blocked，才不带 `--if` 补发一次；仍失败即 error。

抽取逐字校验：选段字段逐一做原文子串校验（含去空白折叠），搜不到标 `rewritten` 附最接近片段，全部搜不到则 `error(extraction_rewritten)`；`reply` 干净全文先整体、再逐行校验，未过逐字校验的行标 `rewritten["reply"]`。

可选 webhook：env `LOOP_WEBHOOK_URL`＋`LOOP_WEBHOOK_SECRET` 都配才发，成功/报错都 POST 结果 JSON，密钥走 header（`X-Loop-Secret`）不进日志、不进 stdout，~10 秒超时；发失败不连累整轮，result 记 `webhook: off/sent/failed`，结果文件仍是事实源。

工具定位：`hc`/`pi`/`uv` 先 `shutil.which`（尊重显式 PATH），找不到再探 `~/.local/bin`、`/opt/homebrew/bin`、`/usr/local/bin`、nvm node bin（`~/.nvm/versions/node/*/bin`）；后台 job 缺 PATH 也能起，探不到才报 `missing_tool`。

## 发布前检查

把 `loop/` 公开到 GitHub 前，在仓库根目录跑一遍。第一行的 `-e` 里补上你环境里的机器名、主机名、内网域名、私有组织名、用户名；第二行是通用模式，直接可跑：

```bash
grep -rniE -e '<机器名>' -e '<主机名>' -e '<内网域名>' -e '<组织名>' -e '<用户名>' loop/
grep -rnE '/Users/|/home/|[[:space:]](10|127)\.[0-9]+\.[0-9]+\.[0-9]+|192\.168\.|172\.(1[6-9]|2[0-9]|3[01])\.' loop/
```

命中即改：机器名、主机名换通用表述，绝对路径换占位符（`<工作目录>`、`<放行id>`），会话地址、账号一律不写。`herdr`、`hc`、`pi` 这类公开产品名可以保留。扫描命令自身的模式串会命中自己，属预期误报。