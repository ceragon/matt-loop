---
status: accepted
---

# 命名会话 get-or-create：报错驱动创建＋shell 包装＋恒可见

## 背景

loop 原要求目标终端预先存在（`--target` 传 t 号）；ADR-0007 把「启动 agent」
约定挂起为独立 skill 线（11-19 旧决定）。用户要求：loop 自动创建 herdr 会话
（初始化设置），且一个 agent 恒对应一个会话。该线并入 loop，独立启动 skill 作废。

事实核查（2026-11-24 两个探针：一次性终端实证＋只读文档/schema 排查，
hc 0.1.22、herdr-app 0.1.17，未碰生产会话）：

1. 名字唯一性只约束 live：同名重复 `hc run` 报 `name_taken`（exit 1），
   原终端无损；并发创建竞态由 herdr 仲裁。
2. kill→finished 后名字立即可复用，按名字解析到最新 live；旧 finished
   条目按 t 号仍可审计；rm 后 `not_found`（exit 3）。
3. 名字可作 `send/read/get/kill/label` 地址；`hc get` JSON 含
   name/labels/cwd/foreground{argv,cwd,name,pid}/argv/state/status/turn。
4. `--keep` 只对信号死亡原地换 shell；自然退出与显式 kill 是终态。
   shell 包装下终端程序是 shell 本身，agent 退出不触发终端死亡。
5. **turn 是终端级累计计数**（schema：status 每次离开 working +1，无清零
   语义；实测 shell 包装终端 agent 退出后 turn 保留）。**修正 ADR-0007
   「hc 按前台进程追 turn」**：认 agent 对（OSC 7501 自报），追 turn 不按
   前台进程。
6. agent 判定只认程序 OSC 7501 自报；`foreground.name` 不可靠（claude
   实测为版本号），`hc ls` 的 `agent` 派生字段不作身份判据；shell 回前台
   status 清空。
7. `hc run` 起的终端默认无 pane、GUI 不显示；领养走
   `herdr-app do terminal.keep`（参数仅 terminal＋place?，**无 space 参数**，
   目标空间文档未明确；UI Keep 规则＝最近祖先 space 否则本机第一个 space）；
   keep/park 后台完成、失败弹 toast → 必须只读 `herdr-app ls` 复核 pane。
8. app 存活探测：`herdr-app status` 退出码或活 socket；**裸 `herdr-app`
   会打开窗口，不可作探针**；app 关闭时 ls/status 报错文案文档未明确。
9. 本机无 space 时 keep 无处落；`space.new(name,folder,focus)` 建空间赠送
   shell 标签，`tab.close confirmed:true`＋`hc rm` 可清；`tab.close` 会结束终端。
10. herdr 模板目录 `~/.config/herdr-core/templates` 本机不存在，
    `hc templates` 为空；模板是 `hc run` 层构造，不是 shell 命令。

## 决定

1. **身份＝会话名**（Q1=C）：调用方命名（bot 名或会话 id 类，slug 成
   1–32 位 a-z0-9-_），脚本零映射；推荐命名法（`<kind>-<repo>` 或 bot slug）
   写 SKILL.md。`--target` 推荐传名字，result `target` 恒为传入地址、另报
   `target_address`（解析 t 号）。
2. **报错驱动 get-or-create**（Q2=A）：`hc get` 预检 `not_found`/`finished`
   即创建信号，同一次调用内创建→等就绪→核身份→发主任务。需创建/复活而无
   `--launch` → `launch_required`（退出码 2、零发送、放行 id 不消耗、补参同 id
   重试，与 `reset_required` 同构）。
3. **单形态 shell 包装**（Q3=A、Q9=B）：`hc run --bg --keep --name <名字>
   --cwd <目录> -- zsh -l` → send 启动命令。`--launch` 永远只收 shell 命令
   字符串；herdr 模板退居持久配置仓库，调用方抄命令字面传，loop 不感知模板，
   无 `--launch-template`、无双形态。复活：live＋空闲 shell（前台是 shell＋
   status null）＋loop 标签相符 → 重发启动命令。
4. **预检分流与标签绑定**（Q7）：not_found→创建；finished→同名创建旧留
   审计；live＋身份符→既有路径；live＋空闲 shell＋标签符→复活；live＋别的
   agent/程序或无 loop 标签→`session_occupied` 停轮呈报占位者（地址、前台、
   turn、状态），不建不 kill。创建时打 `loop=1`＋`loop-agent=<expect-proc>`
   标签；无标签同名终端拒收编。身份认 `status.app`/argv[0]，分流一律 `hc get`。
5. **强制重建**（Q8）：`--rebuild` 仅用户明示后传，放行必贴「将 kill <名字>
   （占位者摘要）并重建」；只杀 idle/done，working/blocked → `rebuild_busy`；
   台账 rebuild phase，result `rebuilt:true`＋旧 t 号；kill 后创建失败整轮
   error 带部分状态。
6. **放行纪律**（Q6=A、Q10）：创建参数（名字、启动命令、cwd）讨放行时一并
   贴出，「发」即授权创建＋主任务；不许静默创建；台账 launch phase，创建失败
   整轮 error 带部分状态。`--cwd` 与 `--launch` 同时必填、无默认。
7. **发送上限与新鲜豁免**（Q11，上限按现行规则纠正）：启动发送占前置槽，
   一轮至多发送成功**三次**不变（重置或启动前置＋切模型前置＋主任务，各一次；
   ADR-0006 的 `--target-model` 仍有效、不在本次移除范围）。刚创建/复活的
   会话自动跳过重置（result `reset_skipped:"fresh_launch"`），会话起始 skill
   豁免视同 turn 0、**不看终端 turn**；跳过重置时切模型前置照跑（平移
   ADR-0006「turn 0 豁免时切模型照跑」纪律）。启动命令不抽取、不落 run_record。
8. **恒可见不变量**（Q12=强制、Q14/15/16=A）：
   - 创建路径：终端就绪后 `herdr-app do terminal.keep` → 只读 ls 复核 pane；
     app 没开/keep 失败/无 pane → `adopt_failed`（原因 app_closed/keep_error/
     no_pane），**保留已建终端**，result 带 `launched:true`/`target_address`/
     `workspace:"failed"`，台账停 `adopt_failed`；launch 已发→放行 id 消耗，
     开 app 后新 id 重试走既有路径修复。
   - 既有路径：每轮预检（写台账前）ls 核 pane，缺则补领养（keep＋复核）；
     补不成→预检 `adopt_failed`，零发送、id 不消耗、同 id 重试。「loop 命名
     会话恒有 pane」全局不变量；**app 没开时所有轮次停摆**（已接受）。
     关 tab＝杀终端，走 finished→重建。
   - 本机无 space：`space.new {"name":<会话名>,"folder":<cwd>,"focus":false}`
     → keep → 复核 → `tab.close confirmed:true`＋`hc rm` 赠送 shell 终端；
     任一步失败 `adopt_failed` 带部分状态（空间可能已建，如实报）。
   - app 探测用 `herdr-app status` 退出码/活 socket，绝不用裸 `herdr-app`。
     不指定空间（keep 无 space 参数），落点随 herdr-app 规则、不可控，已接受。
9. **switch-agent 移除**（Q5=B）：`--switch-agent`/`--exit-command`/
   `--exit-keys` 代码＋测试＋文档整条删除；换 kind＝换 `--target` 到另一命名
   会话（get-or-create 兜底），旧会话保留可切回；既有未命名终端下次换 kind
   时 kill 另建命名会话。ADR-0007 标 superseded by 0009，文件保留。
10. **词汇**（Q13）：GLOSSARY 目标会话去 grok 硬编码、地址改名字（推荐）或
    t 号；新词条会话名/命名会话/shell 包装/创建前置/复活/强制重建/领养；
    分发脚本/放行/一轮定义同步（一轮上限按决定 7 纠正为三次）。

## Considered Options

- 独立 init 步骤／独立「启动 agent」skill（Q2 B/C）：多一条调用方纪律、
  机械保证跨 skill 重复；11-19 旧决定作废。
- kind 级／kind＋cwd 级身份（Q1 A/B）：太粗／写死映射；命名约定可覆盖 B。
- 直接起 agent（Q3 B）：无复活、退出即 finished；复活是用户核心收益。
- 模板双形态 `--launch-template`（Q9 A，助手推荐，**用户否**）：模板分支
  直接起、无复活，二等公民；单形态脚本最瘦；模板目录尚不存在，原生消费是
  假设收益。
- loop 自建配置文件（Q4 C）：新复杂度税；herdr 模板已是部署层配置仓库。
- switch-agent 保留并存（Q5 A，助手推荐，**用户否**）：用户要立刻迁移、
  单一约定。
- 尽力领养降级 headless（Q12 A，助手推荐，**用户否**）：用户裁定「必须进、
  无条件进、进不了报错」，可见性是硬需求。
- 仅创建时领养／尽力补领养（Q15 B/C）：漂移不自愈／静默降级与无条件精神
  不一致。
- 无 space 报错让人建（Q16 B）：自动化断头。
- 领养失败同 id 重试（Q14 B）：launch 已发，「一轮一放行」不开口。
- 重建准杀 working：绝不杀在跑工作。

## Consequences

- 调用方：`--target` 用名字；首轮带 `--launch`＋`--cwd` 并贴创建参数；
  换 kind 换 target；**herdr-app 必须常开**（所有轮次硬依赖）。
- turn 语义修正：复活会话 turn>0 但会话空；result turn 注释为终端级累计。
- herdr-app 升级可能改 keep/space.new 行为；adopt_failed 部分状态复杂
  （空间可能已建、终端已建）。
- 既有未命名终端（t24 等）无换 kind 路径，按需迁移。
- 实现清单：dispatch.py 分流/标签/创建/复活/领养/重建；移除 switch-agent
  三旗标；SKILL/REFERENCE/GLOSSARY 同步；测试覆盖各分流与失败部分状态。
