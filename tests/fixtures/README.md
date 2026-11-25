# fixtures — 脱敏契约样例（T3，ADR-0016 决定 6）

来源：研究票 #72（`docs/research/dispatch-contract-samples.md`，采集于 hc 0.1.22 /
herdr-app 0.1.18）。占位符（`<ADDR>`、`<SPACE_ID>`、`<TAB_ID>`、`<PANE_ID>`、`<PATH>`、
`<TITLE>`、`<NAME>`、`<HEX>`）不含任何机器相关信息。

**未实测的形状显式标成假设**；假设不是事实，依赖它们的行为不得被当成经验证的契约
（ADR-0018 决定 13 处置）。

| 文件 | 命令 | 来源分级 |
| --- | --- | --- |
| `hc_get_live.json` | `hc get`（live、自报 working） | 实测（原样字段，值脱敏） |
| `hc_get_finished.json` | `hc get`（finished） | 实测形状特征（`foreground` 键缺失、`status:null`）＋schema 必填键补全 |
| `hc_get_shell.json` | `hc get`（空闲 shell） | 实测形状特征（`status:null`）＋schema 必填键补全 |
| `hc_send.json` | `hc send` | schema（`SendResult` 必填仅 `turn`），未实测 |
| `hc_wait.json` | `hc wait` | schema（`WaitResult` 必填 `terminal/ended/reason/turn/children`），未实测 |
| `hc_read.json` | `hc read` | schema（`how` ∈ screen/history/stitched），未实测 |
| `hc_run.json` | `hc run` | schema（`RunResult` ⊇ `Terminal` 必填键），未实测 |
| `hc_rm.json` | `hc rm` | schema（`RemoveResult {"removed"}`），未实测 |
| `hc_error_not_found.json` | `hc` 错误信封 | 实测（退出码 3；其它 kind 的退出码未逐一实测） |
| `app_status.json` | `herdr-app status` | 实测 |
| `app_ls.json` | `herdr-app ls` | 实测 |
| `app_do_error.json` | `herdr-app do` 错误 | 实测（unknown command；`error` 是字符串） |
| `app_not_running.json` | app 未开时 `ls`/`status` 错误 | **假设**（未实测；行为不依赖具体内容，ADR-0018 决定 13c） |
| `app_space_new.json` | `do space.new` 成功返回 | **假设**（无文档；dispatch 只依赖 `space` 键，HITL 采样票另追） |
| `app_do_empty.json` | `do space.select`/`tab.close`/`tab.rename` 成功返回 | **假设**（无文档；dispatch 忽略返回，靠只读复核） |
| `hc_commands.json` | hc 子命令词汇表 | 实测（`hc --help`；故障键守卫用） |
| `app_commands.json` | herdr-app 子命令＋`do` 命令词汇表 | 只读子命令实测自 `--help`；`do` 命令为 dispatch 用到的＋`terminal.kill` 的最小集（故障键守卫用；守卫另拒绝真实但假世界未实现的命令） |

`pane:null` 语义（无 pane 的终端在 `ls` 里是 `pane:null` 还是条目隐藏）未实测；
`FakeHerdr` 沿用 `pane:null` 输出（ADR-0018 决定 13a：两种形态由网关 `Layout` 吸收）。

`hc label`/`hc kill` 返回完整 `Terminal`（schema），样例复用 `hc_get_live.json`。