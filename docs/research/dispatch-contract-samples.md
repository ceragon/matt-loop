# dispatch / hc / herdr-app 契约样例（实测、文档与代码推断）

采集日期：2026-11-25
工具版本：`hc 0.1.22`（schema `openrpc 1.3.2`，`info.version 0.1.22`）、`herdr-app 0.1.18`
方法：严格只读。`hc --version/--help/schema`、`hc ls [--all]`、`hc get <id>`；`herdr-app --version/--help/--skill/commands/status/ls`，以及一次无副作用的 `herdr-app do --help`（被当作未知命令拒绝）。未执行 `hc send/run/read/wait/label/kill/rm`，未执行任何 `do` 写命令，未启动/关闭 herdr-app，未向任何会话写入。
用途：给上游设计票提供真实 JSON 形状，供「测试缝」与「herdr 领域网关」两个设计决定参考。本文只陈述事实，不替设计做决定。

## 脱敏占位符

| 占位符 | 含义 |
| --- | --- |
| `<TITLE>` | 会话/标签/空间的显示名、`where` 标签内容 |
| `<NAME>` | 会话名（`hc run --name`） |
| `<PATH>` / `<HOME>` | 任意绝对路径 / 用户主目录 |
| `<USER>` / `<HOST>` | 用户名 / 机器名（含远端机器别名） |
| `<ADDR>` | 终端地址（真实形如 `t<base36>` 或 `<HOST>/t<base36>`） |
| `<SPACE_ID>` / `<TAB_ID>` / `<PANE_ID>` | 不透明 id（真实前缀分别为 `sp` / `tb` / `p`，后接十六进制） |
| `<MSG>` | 程序通过 OSC 7501 自报的消息正文 |
| `<KEY>` | 任何 token/密钥（本文无真实值） |

---

## 1. `hc`（herdr-core CLI）

### 1.1 CLI 与 schema 概览

- `hc --help` 显示子命令：`run/ls/get/attach/read/kill/rm/label/keep/send/keys/interrupt/wait/put/exec/file/templates/events/server/update/machine/connect/schema`。
- 全局选项：`--json`（管道时默认；`run` 与 `read` 默认打印文本，故脚本必须显式传 `--json`）。
- `hc schema` 输出完整 OpenRPC 文档：`components.schemas`（`Terminal`、`Status`、`WaitResult`、`SendResult`、`ReadResult` 等）＋ `methods`（每个方法的 params/result/errors）。这是唯一机器可读的 `hc` 契约来源。
- 重要：`hc schema` 描述的是 RPC 层；CLI 的 `ls` JSON 是**另一套投影**（见 1.3），与 schema 的 `Terminal`/`ListResult` 不一致。

### 1.2 `hc get <id>`（实测，RPC `Terminal`）

live、自报 working 的终端（原样字段，值已脱敏）：

```json
{
  "address": "<ADDR>",
  "argv": ["zsh", "-l"],
  "cols": 73,
  "created_ms": 1795599201904,
  "cwd": "<PATH>",
  "die_with_parent": false,
  "exit_code": null,
  "finished_ms": null,
  "foreground": {
    "argv": ["claude", "--dangerously-skip-permissions", "--dangerously-skip-permissions"],
    "cwd": "<PATH>",
    "name": "2.1.331",
    "pid": 22780
  },
  "identity": "ghostty",
  "keep": true,
  "labels": {
    "herdr.seen": "2",
    "herdr.space": "<SPACE_ID>",
    "herdr.tab": "<TAB_ID>",
    "loop": "1",
    "loop-agent": "claude",
    "where": "<TITLE>"
  },
  "name": "<NAME>",
  "notify": true,
  "parent": null,
  "pid": 22409,
  "reason": null,
  "records": {
    "<HEX>": { "app": "claude-code", "state": "working", "title": "<TITLE>" }
  },
  "reported": true,
  "rm": false,
  "rows": 62,
  "screen": "exact",
  "signal": null,
  "state": "live",
  "status": { "app": "claude-code", "state": "working" },
  "status_ms": 1795600036419,
  "template": null,
  "title": "<TITLE>",
  "turn": 3
}
```

已结束（`state:"finished"`）的终端（实测）——注意 `foreground` 键**整个缺失**（不是 `null`），`status` 为 `null`，并出现 `exit_code`/`reason`/`finished_ms`：

```json
{
  "address": "<ADDR>", "state": "finished", "exit_code": 0, "reason": "exited",
  "finished_ms": 1795585835348, "status": null, "status_ms": 1795578670451,
  "foreground": "<ABSENT>", "signal": null, "title": "", "turn": 12
}
```

已结束但**从未自报状态**的空闲 shell（实测）——`status` 为 `null`，`foreground.name`/`argv[0]` 是 shell：

```json
{ "state": "live", "status": null, "foreground": { "name": "zsh", "argv": ["/bin/zsh", "-l"] }, "turn": 46 }
```

远端机器终端（实测）——地址带机器前缀，`labels`/`foreground` 形状不变；`status` 里可带 `msg`：

```json
{
  "address": "<HOST>/<ADDR>",
  "foreground": { "argv": ["agy", "--dangerously-skip-permissions"], "name": "agy", "pid": 7933 },
  "labels": { "loop": "1", "loop-agent": "agy", "where": "<TITLE>" },
  "state": "live",
  "status": { "app": "antigravity", "msg": "<MSG>", "state": "done" },
  "turn": 12
}
```

字段要点（schema，`Terminal`）：

- `state`：`"live"` 或 `"finished"`。
- `status`：`Status` 对象或 `null`；**程序从未自报或已退出时为 `null`**。
- `turn`：`uint64`，默认 0；schema 原文「Goes up each time the status leaves `working`」——即**终端级累计**，不是会话轮次。
- `screen`：`"exact"` 或 `"inexact"`（core 崩溃后旧历史可能缺失）。
- `reason`（结束时）：`exited`、`killed`、`server_stop`、`host_shutdown`、`signaled`、`holder_lost`、`stopped_by_restart`、`spawn_pending`。
- `foreground`：活着的进程对象；schema 说结束后可为 `null`，**实测是键缺失**。
- `records`：其它 OSC 7501 记录（按 id），每项形如 `Status`。
- `labels`：自由键值；loop 使用 `loop`、`loop-agent`、`herdr.space`、`herdr.tab`、`where`、`herdr.seen`。
- 必填键（schema）：`address`、`labels`、`argv`、`cwd`、`state`、`created_ms`、`screen`、`cols`、`rows`。

`Status`（schema）：

```json
{
  "app": "string",
  "kind": "permission | question | auth | null",
  "msg": "string",
  "progress": "uint8 0..255（描述为 0..100；working 或 blocked 时可能带）",
  "state": "idle | working | done | blocked | error",
  "title": "string"
}
```

- `state` 取值域：**`idle` / `working` / `done` / `blocked` / `error`**（schema 原文）。
- `kind` 取值域：**`permission` / `question` / `auth`**，schema 注明「blocked only, when the program said」。
- 实测只见到 `idle` / `working` / `done` / `null`；`blocked` / `error` 未在样本中出现，取值域来自 schema。

### 1.3 `hc ls`（实测，CLI 投影，非 RPC `Terminal`）

`hc ls` 与 `hc ls --json` 输出完全相同。`hc ls --all` 的终端对象键共 10 个：

```
address, agent, cwd, state, turn,
name?, status?, exit_code?, reason?, where?
```

- 与 `hc get`/schema `Terminal` 的差异：
  - **多出 `agent` 与 `where`**（派生展示字段，schema 无）。
  - **缺少** `labels`、`argv`、`foreground`、`records`、`pid`、`cols`、`rows`、`screen`、`created_ms`、`finished_ms`、`keep`、`notify`、`parent`、`reported`、`rm`、`signal`、`status_ms`、`template`、`title`、`identity`、`die_with_parent`、`exit_code`（活终端时）。
  - **可选字段用「键缺失」而非 `null`**：无名终端无 `name`，无自报状态无 `status`，活终端无 `exit_code`/`reason`。这与 `hc get` 明确输出 `null` 不同。
  - `status` 是**字符串**（`idle`/`working`/`done`/…），不是 `Status` 对象。
- 顶层：`{"hidden_finished": <int>, "machines": [...], "terminals": [...]}`。
- `machines` 项也是简化投影：实测 `{"name":"<HOST>","state":"connected"}`；schema 的 `MachineView` 还要求 `target`、`id`、`host`、`version`——CLI `ls` 未给。
- `agent` 实测取值：`zsh`、`bash`、`claude-code`、`grok`、`antigravity`；`status` 实测取值：缺失/`null` 表现（键缺失）、`idle`、`working`、`done`。
- `dispatch.py` **不使用 `hc ls`**；代码注释明确「不用 hc ls 的 agent 派生字段」，分流一律用 `hc get`。

### 1.4 其它 `hc` 命令的契约（schema 文档；未执行）

- `hc run` → `RunResult`（`= Terminal` 加上 `first_message`、`output`、`wait` 等）。必填 `address`、`labels`、`argv`、`cwd`、`state`、`created_ms`、`screen`、`cols`、`rows`；`turn` 默认 0。错误 kind：`invalid_params`、`name_taken`、`spawn_failed`、`unavailable`、`invalid_template`、`not_found`、`machine_unknown`、`config`。
- `hc label` / `hc kill` / `hc keep` → `Terminal`。`kill` 错误 kind：`not_found`、`machine_unknown`、`unavailable`。
- `hc rm` → `RemoveResult {"removed": string}`；错误 kind：`not_found`、`machine_unknown`、**`live`**、`unavailable`。
- `hc send` → `SendResult {turn（必填）, started?: bool|null, status?: string|null, wait?: WaitResult|null}`。`--if` 的取值域只有 **`idle` / `working`**（schema `IdleOrWorking`），没有 `done`。错误 kind：`not_found`、`machine_unknown`、`finished`、`unavailable`、`blocked`、`status_mismatch`、`invalid_params`、`config`、`cancelled`。
- `hc wait` → `WaitResult`，必填 `terminal`、`ended`、`reason`、`turn`、`children`。`reason` 取值：`status`、`exit`、`output`、`timeout`、`not_started`、`children`、`message`、`running`（children 用）。`ended:false` 表示超时 check-in 或消息中断。错误 kind：`not_found`、`machine_unknown`、`invalid_params`、`needs_target`、`config`、`unavailable`。
- `hc read` → `ReadResult {text, complete, how, why?}`。`how`：**`screen` / `history` / `stitched`**；`why`：`working` / `blocked` / `error` / `null`（仅返回屏幕时的原因）。错误 kind：`not_found`、`machine_unknown`。

### 1.5 `hc` 错误形状（实测）

```json
{"error": {"hint": "see `hc ls --all`", "kind": "not_found", "message": "no terminal \"<ADDR>\""}}
```

- 退出码：本例为 **3**（`not_found`）。其它 kind 的退出码未逐一实测。
- 外层固定 `{"error": {kind, message, hint}}`；`hint` 是可选提示。
- `dispatch.hc_json` 同时兼容 `detail["kind"]` 与 `detail["error"]["kind"]`（实测形状命中后者）。

---

## 2. `herdr-app`

### 2.1 CLI 概览

- 无参数会打开/抬起窗口（禁止用作探针）。
- 只读子命令：`ls`、`status`、`commands`、`shot`、`--skill`。
- 写命令统一 `do <command> [--arg value ... | 单个 JSON 对象]`；**没有 `--json` 开关**（原生输出 JSON）。
- `commands` 输出数组：`[{name, args:[{name, required}], about}]`，只描述**入参**。
- `--skill` 说明：`do` 成功时「prints the created IDs (for example `{ "pane": "...", "terminal": "..." }`) or `{ "error": "..." }` and exits 1」。

### 2.2 `herdr-app status`（实测）

```json
{
  "zoom": 1.0,
  "font_size": 14.0,
  "ui_font_size": 13.0,
  "space": "<SPACE_ID>",
  "tab": "<TAB_ID>",
  "focused_pane": "<PANE_ID>",
  "layout": "main",
  "agent": "<ADDR>",
  "floats": {},
  "overlays": [],
  "errors": []
}
```

- `space` 是当前选中空间 id；`tab`/`focused_pane`/`agent` 是当前焦点；`overlays` 是打开的对话框/弹层；`errors` 是 app 的文件/mod 错误列表（`--skill` 称 status 展示「file/mod errors」）。
- `dispatch.py` 只读 `status["space"]`，**不读 `errors`/`overlays`**。

### 2.3 `herdr-app ls`（实测）

空间 → 选项卡 → 终端的三层树，外加顶层 `recent`：

```json
{
  "spaces": [
    {
      "id": "<SPACE_ID>",
      "name": "<TITLE>",
      "folder": "<PATH>",
      "machine": null,
      "active": true,
      "tabs": [
        {
          "id": "<TAB_ID>",
          "name": "<TITLE>",
          "active": true,
          "settled": false,
          "terminals": [
            {
              "terminal": "<ADDR>",
              "pane": "<PANE_ID>",
              "status": "working",
              "app": "claude-code",
              "name": "<NAME>",
              "sub_agents": []
            }
          ]
        }
      ]
    }
  ],
  "recent": [
    { "name": "<TITLE>", "folder": "<PATH>" }
  ]
}
```

实测取值/形状：

- 顶层键：`spaces`、`recent`。
- `space`：`id`、`name`、`folder`、`machine`（本机为 `null`，远端为 `<HOST>`）、`active`、`tabs`。
- `tab`：`id`、`name`、`active`、`settled`、`terminals`。
- `terminal`：`terminal`（地址）、`pane`、`status`（**字符串或 `null`**，如 `idle`/`working`/`done`）、`app`（字符串或 `null`，如 `claude-code`/`grok`/`antigravity`）、`name`（会话名或 `null`）、`sub_agents`（数组）。
- 远端空间的 `id`/`tab.id`/`pane`/`terminal` 都带机器前缀，如 `<HOST>/sp<HEX>`、`<HOST>/tb<HEX>`、`<HOST>/p<HEX>`、`<HOST>/t<ADDR>`。
- 实测中所有终端条目都有非空 `pane`；`pane` 为 `null` 的条目未出现。

### 2.4 `do` 命令入参（`herdr-app commands` 文档）

`dispatch.py` 实际用到的 6 个：

| 命令 | 入参（`required`） | dispatch 发送的 JSON | dispatch 读取的返回 |
| --- | --- | --- | --- |
| `space.new` | `name? folder? tab? argv? task? machine? focus?` | `{"name": <目录名>, "folder": <路径>, "focus": false}` | `r["space"]`（缺则 `keep_error`） |
| `space.select` | `index? id?` | `{"id": <space id>}` | 忽略返回；随后 `status.space` 复核 |
| `terminal.keep` | `terminal`（必填）, `place?` | `{"terminal": <地址>}` | 忽略返回；随后 `ls` 轮询复核 pane |
| `tab.close` | `id? confirmed?` | `{"id": <tab id>, "confirmed": true}` | 忽略 |
| `tab.rename` | `id? name?` | `{"id": <tab id>, "name": <显示名>}` | 忽略；随后 `ls` 复核 `tab.name` |
| `terminal.kill` | `terminal`（必填） | 未使用（rebuild 走 `hc kill`） | — |

`space.select` 也接受 `index`；`tab.close` 的 `confirmed` 缺省会先问人——脚本必须显式带 `true`。

### 2.5 `do` 的返回与错误（文档 + 一次实测）

- 成功返回：`--skill` 原文「prints the created IDs (for example `{ "pane": "...", "terminal": "..." }`)」。`space.new` 的返回形状**没有文档**；dispatch 依赖其返回 `space` 键，测试 fake 手写为 `{"space","tab","terminal"}`。
- 失败返回：`{"error": "<字符串>"}`，退出码 1（`--skill` 文档）。
- 实测一次（无副作用）：`herdr-app do --help` 被当作未知命令：

```json
{"error": "unknown command \"--help\": see `herdr-app commands`; a mod's are <mod>.<command> and need the mod loaded"}
```

  退出码 1。可见 `error` 是**字符串**，不是对象。
- herdr-app 窗口未开时 `ls`/`status` 的失败 JSON 形状**未实测**（未关闭 app），测试 fake 手写为 `{"error": "not running"}`。
- `dispatch.app_json` 兼容 `error` 为字符串或对象（对象再取 `kind`/`message`），但无论哪种都统一抛 `AppFailure("app_failed", ...)`。

### 2.6 pane 缺失时的形状（文档推断，未实测）

- 未观察到 `pane: null` 的真实条目。`herdr-app --skill` 原文：「Workers without a label or a pane stay hidden.」——**无 pane 的终端可能根本不出现在 `ls` 中**，而不是以 `pane:null` 出现。
- 测试 fake 的选择是：终端**始终出现**，无 pane 时 `pane: None`。这两种语义对 `find_pane`（要求 `pane` 真值）结果相同，但契约形状不同。

---

## 3. `dispatch.py` 解析点逐项对照

### 3.1 解析点 → 依赖字段

| 函数 | 来源 | 读取的键 | 备注 |
| --- | --- | --- | --- |
| `hc_json` | hc CLI | 解析 stdout 为 dict；失败时读 `kind` / `error.kind` | 在子命令后立即插 `--json` |
| `app_json` | herdr-app | 解析 stdout 为 dict；失败时读 `error`（字符串或对象） | 无 `--json`；错误 kind 统一 `app_failed` |
| `status_state` | `hc get` | `terminal["status"]["state"]` | `status` 不是 dict 时返回 `None` |
| `identity_ok` | `hc get` | `foreground.argv[0]`、`status.app` | 明确不用 `foreground.name`（实测该值是版本号） |
| `dialog_error` | `hc get` | `status.kind`、`status.msg` | kind 只回显、不枚举 |
| `wait_for_result` | `hc wait` + `hc get` | `reason`、`status`、`turn`、`exit_code`、`signal`、`state` | 处理 `exit`/`not_started`/`status`，其余用 `get` 兜底 |
| `settle` | `hc get` | `state`、`status.state`、`turn` | 要求 `turn` 与 wait 返回一致 |
| `do_send` | `hc send` | 返回 `turn`；失败 `kind` | 依赖 `status_mismatch`/`blocked` 两种 kind |
| `reset_settle` | `hc get` + `hc read` | `state`、`status.state`、`read["text"]` | 用屏幕稳定而非 turn |
| `fg_is_shell` | `hc get` | `foreground.name`、`foreground.argv[0]` | 与身份判据不同，这里用 `name` |
| `wait_for_shell_ready` / `wait_for_agent_ready` | `hc get` | `state`、`foreground`、`status.state` | `not_found` 时分别继续等/报死 |
| `do_create` | `hc run` | `address` | 另调 `label` |
| `find_pane` / `find_pane_space` / `find_tab` | `herdr-app ls` | `spaces[].tabs[].terminals[].terminal`、`.pane`、`tab.id`、`tab.name`、`space.id`、`space.folder`、`space.name` | 无 pane 返回 None |
| `select_and_verify` | `herdr-app do space.select` + `status` | `status.space` | 命令成功不算数 |
| `apply_display_name` | `herdr-app ls` + `do tab.rename` + `ls` | `tab.name` | 复核不符降级 `display_name_warning` |
| `adopt` | `status`/`ls`/`do` | `space`、`spaces`、`pane`、`space.new` 的 `space` | 另用 `hc get`/`hc rm` 清理赠送 shell |
| `resolve_space_id_for_learning` | `herdr-app ls` | `space.id/name/folder` | 只读三态解析 |

### 3.2 fake 有、真实没有 / 形状不符

| fake 位置 | fake 形状 | 真实情况 |
| --- | --- | --- |
| 全部 `FakeHc.read` | `{"how": "current"}` | schema `how` 只有 `screen`/`history`/`stitched`；实测未跑 `read` |
| 全部 `FakeHc.read` | `{"why": ""}` | schema `why` 是 `working`/`blocked`/`error`/`null`；空字符串不在域内 |
| 全部 `FakeHc.wait` | 只返回 `reason`/`status`/`turn` | 真实 `WaitResult` 必填 `terminal`、`ended`、`children` |
| 全部 `FakeHc.send` | 只返回 `turn` | 真实 `SendResult` 必填仅 `turn`，其余可选——可接受，但 fake 无 `started`/`status`/`wait` |
| `NamedFakeHc.run` | `{"address","state","turn"}` | 真实 `RunResult` 必填 `address`、`labels`、`argv`、`cwd`、`state`、`created_ms`、`screen`、`cols`、`rows` |
| `NamedFakeHc.label/kill/rm` | 简化 `{"address"...}` | 真实 `label/kill` 返回完整 `Terminal`，`rm` 返回 `{"removed": ...}` |
| `NamedFakeHc.rm` 的 live 拒绝 | `kind="not_finished"` | schema 的 `hc rm` live 错误 kind 是 **`live`**，无 `not_finished` |
| `NamedFakeHc` dialog 旋钮 | `status.kind="login"` | schema `kind` 只有 `permission`/`question`/`auth`；`login` 不在域内 |
| `FakeApp._ls` | 终端始终列出，无 pane 时 `"pane": None` | 实测无 `pane:null` 条目；`--skill` 称无 pane 的 worker「stay hidden」 |
| `FakeApp._ls` | space 只有 `id`/`name`/`folder`/`tabs` | 真实还有 `machine`、`active`；tab 还有 `active`、`settled`；terminal 还有 `status`、`app`、`name`、`sub_agents` |
| `FakeApp` `space.select`/`tab.close`/`tab.rename` 返回 | `{}` | 真实返回未文档化；`--skill` 只举了创建类命令的 `{pane, terminal}` |
| `FakeApp` `space.new` 返回 | `{"space","tab","terminal"}` | 真实返回未文档化；dispatch 只依赖 `space` |
| `FakeApp.closed` | `AppFailure(..., detail={"error": "not running"})` | 窗口关闭时的真实错误 JSON 未实测 |
| `FakeApp` 直接抛 `AppFailure` | 绕过 `app_json` 的真实错误解析 | 真实子进程 rc≠0 → 解析 `{"error": ...}` 的路径未被 fake 覆盖 |

### 3.3 真实有、fake 缺的状态/字段

- `hc get`：
  - `records`（多 7501 记录）、`reported`、`status_ms`、`screen`、`identity`、`keep`、`notify`、`rm`、`parent`、`pid`、`cols`、`rows`、`created_ms`、`finished_ms`、`template`、`die_with_parent`、`title`、`signal`、`exit_code`、`reason`——fake 大多不返回。
  - `status.app`/`status.msg`/`status.progress`/`status.kind`——fake 的 `Status` 只有 `state`（＋测试里的 `kind`/`msg`）。
  - `state:"finished"` 的完整形状（`foreground` **键缺失**、`status:null`、`exit_code`/`reason`）——`NamedFakeHc` 置 finished 时仍保留 `foreground`/`status`。
  - 远端地址（`<HOST>/t…`）——fake 全部是本机地址。
- `hc wait` 的 `reason`：fake 永远返回 `"status"`；真实还有 `exit`、`not_started`、`output`、`timeout`、`message`、`children`、`running`。
- `Status.state` 的 `error`：没有任何 fake 返回 `"error"`；`blocked` 只在 dialog 旋钮出现一次。
- `herdr-app status`：真实有 `errors`/`overlays`/`floats`/`agent`/`layout`/`zoom`/字体；fake 只返回 `{"space": ...}`。
- `herdr-app ls`：真实有 `machine`、`active`、`settled`、`sub_agents`、`recent` 内容；fake 的 `recent` 恒空，其余省略或 `None`。

### 3.4 错误分支

已由 fake 覆盖的：

- `hc get` `not_found`（创建分流、等 shell/等 agent 时继续或报死）。
- `hc run` `name_taken`（映射 `launch_failed`）。
- `hc label` 失败、`hc send` `status_mismatch`（done 补发一次）、`hc send` `blocked`（`send_refused`）、`hc send` 其它失败。
- `hc kill` 失败、`hc rm` live/失败/`not_found`。
- `hc wait` 的 `status` 分支；`blocked`/`error` 状态经 dialog/target_error 分支。
- herdr-app 未开（`closed`）、`keep` 失败、`keep` 成功但 pane 不落地、`space.new` 失败、`tab.close` 失败、`tab.rename` 失败/不生效、`ls` 无 tab id、`space.select` 失败/不落地、还原失败。

未覆盖或覆盖不足的：

- `hc wait` 的 `exit`、`not_started`、`output`、`timeout`、`message`、`children` 分支（fake 永不产生）。
- `Status.state="error"`（`target_error` 分支无 fake 触发）。
- `hc send` 的 `finished`/`not_found`/`unavailable`/`config`/`cancelled`。
- `hc read` 的 `not_found`/`machine_unknown`（主流程 read 失败）。
- `hc run` 的 `spawn_failed`/`unavailable`/`invalid_template`/`config`。
- `space.new` 成功但返回缺 `space` 键。
- `herdr-app status` 返回 `errors` 非空（dispatch 不读，仍继续领养）。
- `app_json`/`hc_json` 对真实 stdout/stderr 错误 envelope 的解析（fake 直接抛异常，只有 `test_dispatch_turn_cut` 用 mock `run_cmd` 测了「rc 0 非 JSON」和「rc 1 摘录」两类）。

### 3.5 测试盲区（小结）

1. 没有任何一份「真实输出」金样例；所有 fake 都是手抄，且分散为 5 份 `FakeHc` ＋ 1 份 `FakeApp`。
2. `hc schema` 与 `herdr-app commands` 均未进入测试：没有 schema 校验，也没有「fake 是否符合 schema」的断言。
3. `hc wait` 只测到 `reason="status"`；dispatch 里为 `exit`/`not_started` 写的分支只靠代码审阅。
4. `Status.state="error"` 无测试。
5. 真实 `hc get` 的「finished 时 `foreground` 键缺失」「可选字段显式 `null`」与 `hc ls` 的「可选字段键缺失」两种风格都未被 fake 表达。
6. herdr-app 的 `pane:null` vs「无 pane 条目隐藏」两种语义未定；fake 选了前者。
7. 远端（`<HOST>/…`）标识贯穿 `hc` 与 `herdr-app`，但所有 fake 只用本机地址。
8. `space.new`/`terminal.keep`/`tab.close`/`tab.rename`/`space.select` 的**真实返回**无文档、无样本；dispatch 只依赖 `space.new.space`，其余靠随后的只读 `ls`/`status` 复核——这正是「命令成功不算数」的设计动机，但返回契约本身仍是空白。
9. `herdr-app status.errors`、`overlays` 未被消费；app 带错误但 socket 仍通时，领养会照常进行。

---

## 4. 对两个设计决定的输入要点（只列事实）

### 4.1 「测试缝：run 的显式依赖、单一 fake adapter」

- 现有缝：所有 hc 交互经模块级 `hc_json(hc_bin, args, timeout_s)`，所有 herdr-app 交互经模块级 `app_json(app_bin, args, timeout_s)`；两者内部调模块级 `run_cmd(argv, timeout_s)`。测试直接整体替换 `dispatch.hc_json` / `dispatch.app_json`（部分测试还替换 `dispatch.run_cmd`）。
- 副作用：fake 替换的是**整条命令**，不是「解析后的领域对象」；因此 fake 必须自己重实现命令语义（状态机、异步 pane 落地、空间归属、赠送 shell 清理）。
- 入口依赖：`hc_bin`/`app_bin` 来自 `find_tool`（`shutil.which` ＋常见目录探测），测试另 mock `shutil.which` 与 `check_structure_and_tools`。
- 无版本门：dispatch 不校验 `hc`/`herdr-app` 版本；两者独立发版（实测 0.1.22 / 0.1.18）。
- 契约来源不对称：`hc schema` 可机器读取；`herdr-app` 只有 `commands`（入参），没有返回值/错误 schema。
- 命令面：dispatch 实际调用 9 个 hc 子命令（`get`/`run`/`label`/`send`/`wait`/`read`/`kill`/`rm`）和 3 个 herdr-app 子命令（`status`/`ls`/`do`），`do` 下 5 个命令（`space.new`/`space.select`/`terminal.keep`/`tab.close`/`tab.rename`）。
- 形参顺序耦合：`hc_json` 在 argv[1] 位置插 `--json`（`send` 用 `--` 保护提示词）；`app_json` 原样透传。任何把命令拼装挪出这两个函数的改动都会绕过现有测试缝。

### 4.2 「herdr 领域网关」

- 两个外部系统、两种信封：
  - `hc`：JSON-RPC 风格，`--json` 需显式传（`run`/`read` 默认文本）；错误 `{"error":{kind,message,hint}}` ＋非零退出码；有完整 OpenRPC schema。
  - `herdr-app`：原生 JSON，无 `--json`；错误 `{"error":"<string>"}` ＋退出码 1；无返回值 schema。
- 两套状态词汇部分重叠但形状不同：`hc get.status` 是对象（`state`/`app`/`kind`/`msg`/`progress`/`title`），`herdr-app ls.terminal.status` 是字符串或 `null`。
- 标识符命名空间有 4 层：终端地址（`t…` / `<HOST>/t…`）、空间 id（`sp…`）、标签 id（`tb…`）、pane id（`p…`）；`hc` 与 `herdr-app` 对终端地址的表示一致（远端起机器前缀）。
- 观察到的异步语义：`terminal.keep` 的 pane 落地、`tab.close` 的终端结束都不是命令返回即完成，dispatch 因此用带超时轮询的 `ls`/`hc get` 复核（#40/#46）。这是「命令成功不算数」的事实基础。
- 读模型：`hc get` 是单终端权威事实（含 `labels`/`foreground`/`status`）；`hc ls` 是 CLI 投影（`agent`/`where`，丢字段、可选键缺失），dispatch 明确不用；`herdr-app ls` 是**可见性**权威（space/tab/terminal/pane 树），`status` 是**当前焦点**权威（`space`/`tab`/`focused_pane`/`errors`）。
- 错误 kind 不共享：hc 用 `not_found`/`status_mismatch`/`blocked`/`name_taken`/`live`/…；herdr-app 不暴露 kind，只有字符串消息。dispatch 把 herdr-app 失败统一映射为 `app_failed`，再由 adopt 重新归因为 `app_closed`/`keep_error`/`no_pane`/`select_error` 等领域原因。
- `herdr-app ls` 的「无 pane 隐藏」与 `pane:null` 两种可能语义尚未定论；这会影响「pane 缺失」是「条目不存在」还是「pane 字段为空」的领域判断。

---

## 5. 结论来源分级

- **实测**：`hc --version/--help`、`hc schema`、`hc ls`/`hc ls --all`、`hc get`（live/working、live/idle-shell、finished、远端、not_found）；`herdr-app --version/--help/--skill/commands/status/ls`；`herdr-app do --help` 的错误形状。
- **文档**：`hc schema` 的字段/枚举/错误 kind；`herdr-app commands` 的 `do` 入参；`herdr-app --skill` 的「prints the created IDs / `{ "error": "..." }` exits 1」「无 pane 的 worker stay hidden」；`loop/REFERENCE.md` 的 error kind 表（本仓库文档，不是外部契约）。
- **代码推断 / 未实测**：`hc send/wait/run/read/label/kill/rm` 的真实 stdout（只据 schema）；`herdr-app do` 各写命令的真实返回（只据 `--skill` 与 dispatch 读取点）；app 未开时的真实错误形状；`pane:null` 的真实出现性；`Status.state="blocked"/"error"` 的真实样本。

---

## 6. 只读复现命令

```sh
hc --version
hc --help
hc schema
hc ls
hc ls --all
hc get <terminal-address>

herdr-app --version
herdr-app --help
herdr-app --skill
herdr-app commands
herdr-app status
herdr-app ls
```

注意：`herdr-app` 无参数会开窗；`hc send/run/read/wait/label/kill/rm` 与 `herdr-app do <写命令>` 均不在只读范围。
