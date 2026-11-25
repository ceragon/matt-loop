---
status: accepted
---

# 测试缝先于网关：`Deps` 注入加命令级 `FakeHerdr`

> 补记（ADR-0020，#75）：决定 1 预留的 `run(request, deps)` 已由 ADR-0020 按预留定形，
> `main` 退成薄壳、测试入口不变；决定 3 里 `extract` 字段的签名改为显式的
> `deps.extract(request, turn_path, injection) -> Extraction`，取代往 `args` 上暗渡
> 属性；决定 7 末尾「待⑤收口」的 `learning.enabled()` 环境变量洞已**部分**收口——
> `learning.enabled(env)` 加一个参数、由管线传 `deps.env`，只改这一个函数签名，其余
> 仍归 ⑤。「交接」一节里管线设计票接手的事项按此落地，`harness.run` 仍停在 argv 层。

> 补记（ADR-0018，#74）：本 ADR 的决定 1–10 不变；但「网关之后：稳定与临时」一节里把
> `Deps.hc_json` / `Deps.app_json`、`FakeHerdr` 的 argv 级入口、契约一致性检查器与解析
> 测试判为**临时**的判断被推翻，三者改为**稳定**（网关封装在命令级 seam 之上，seam 不上
> 移；理由与演进路径见 ADR-0018「对 ADR-0016 的修订」）；交接项「重命名故障键」撤销，
> 故障键保持 herdr 命令名；三项未实测假设已由 ADR-0018 决定 13 处置。
>
> 补记（ADR-0021，#82）：决定 10 末句「怎么把这些步骤切成实施票」已由 ADR-0021 回答——步骤即
> 切片 T1–T5 与收尾片 R，棘轮守卫的机制（自检式白名单）亦在 ADR-0021 决定 4 补全。

## 背景

评审 #53 把 `dispatch.py` 的测试面列为首要摩擦：`main(argv)` 内部自行取所有依赖，
测试每轮替换 5–6 个模块全局，全仓 patch 9 个内部名共 100+ 处；fake 脚手架无主、
重复（`FakeHc` 4 份、`FakeApp`、`NamedFakeHc`）且互相 import（18 条跨测试
import、10 份 run helper）；`time.sleep` 被改成共享 `time` 模块属性且不恢复，十余
个测试文件在 import 期直接写 `os.environ`。重构任何一段都会打碎二十来个测试文件。

地图 #71 的主干顺序是「测试缝 → herdr 领域网关 → 一轮管线」，即测试缝**先于**网关
定型。网关定型后进程层 argv/JSON 形状会被上移，所以本 ADR 必须同时回答两件事：
现在立什么、网关之后哪些会变、哪些不变。

输入事实（研究票 #72 的脱敏结论，采集于 hc 0.1.22 / herdr-app 0.1.18）：

1. 现有缝是模块级 `hc_json` / `app_json`，二者内部再调 `run_cmd`；测试整体替换前
   两者，所以 fake 绕过了真实的错误信封解析，必须自己重实现状态机。
2. fake 与真实输出有 9 类形状偏差（如 `read.how`、`rm` 拒绝的 error kind、
   `ls` 里的 `pane:null`），且无任何真实输出金样例。
3. hc 有 OpenRPC schema，herdr-app 只有入参文档、无返回值与错误 schema。
4. 外出 I/O 实际只有两个原语：进程 `run_cmd`（hc、herdr-app、pi 共用）与 HTTP
   `webhook_open`（webhook 投递与 API 抽取共用）。

## 决定

（grill Q1–Q13 裁定，2026-11-25；纯内部结构与测试层，零外部契约变更——flag、error
kind、`result.json`、`notify.json` 形状与 ADR-0001…0015 均不动。）

1. **测试入口**：停在 `main(argv=None, *, deps=None)`，`deps=None` 用生产默认。
   flag 是对调用方冻结的外部契约，测试从 argv 进、验 stdout JSON 与落盘文件。一轮管线
   落地后在其下加 `run(request, deps)`，`main` 退成薄壳，测试入口不变。
2. **依赖形态**：一个 `Deps` 数据对象，字段是朴素可调用物或小对象，取代现在逐层透传的
   `hc_bin` / `app_bin`。不用 Protocol（没有第二种实现就不值得多一层类型声明），
   不引入模块级「当前依赖」全局或 contextvar（那是换名的全局副作用）。
3. **`Deps` 字段**：herdr 命令级入口 `hc_json` / `app_json`；时钟（`monotonic` +
   `sleep`，测试用只推进虚拟时间不真睡的虚拟时钟）；环境变量（只读 Mapping，默认
   `os.environ`）；工具定位 `locate`（取代 `find_tool`）；`http_open`；`run_cmd`；
   `extract`（高层，默认由 `http_open` 与 `run_cmd` 搭出，是同一条依赖链的两个层级，
   不是两个竞争的 adapter）。
4. **herdr 这一缝本阶段停在命令级**，不下沉到 `run_cmd`。fake 说 dict、不说
   argv/stdout；错误映射由单独的解析测试（`run_cmd` 桩喂脱敏真实信封）守住。
5. **一个有主 fake 模块**：`FakeHerdr` 世界对象持有终端、空间、选项卡、窗格状态，
   对外暴露 `.hc_json` 与 `.app_json` 两个命令入口。约 20 个竞态旋钮归并为三类：
   **故障**（`(命令, 模式, 参数)` 声明，模式含 error / noop）、**延迟**（同形，
   模式含 land_after / vanish）、**外部干扰**（命名世界事件方法：app 未开、用户
   切走空间、pane 落进别的空间、ls 不暴露 tab id）。构造器里不再有一排布尔。
6. **契约一致性检查**：fake 返回须含该命令的必填键、取值落在枚举域内、值类型正确；
   不做键集精确相等（真实 `hc get` 约 30 个字段，fake 只需表达 dispatch 读的那部分）。
   脱敏最小样例放 `tests/fixtures/`，同批样例也喂解析测试。**未实测项不造样例**，
   在 fake 里标「未实测假设」：`pane:null` 与「无 pane 条目隐藏」的语义、
   `space.new` 的真实返回、app 未开时 `ls` / `status` 的真实错误 JSON。
7. **全局副作用归零**：时钟、环境变量、工具定位全部走 `Deps`，不在夹具里
   monkeypatch；`check_structure_and_tools` 不再需要被打成空操作。例外：
   `learning.enabled()` 自己读 `os.environ`，属学习案例集成（评审 #53 候选⑤），本决定
   不动 `learning.py`，测试作用域内设置并恢复 `LOOP_LEARNING=off`，标「待⑤收口」。
8. **三个测试盲区各归位，不新增注入点**：`run_cmd` 真实 subprocess 分支直接单元测试
   （真实无害进程与不存在的可执行名）；`missing_tool` 用返回 None 的 `locate`
   经 `main` 测，`missing_structure` 让 `check_structure_and_tools` 接受 `root`
   参数、测试直接调用；结果文件与通知记录写失败用真实临时目录、把目标路径占成已存在
   目录触发 `OSError`（不依赖文件权限）。
9. **布局**：测试与假桩搬到仓库根 `tests/`，含 `fake_herdr.py`、`harness.py`、
   `fixtures/`；`loop/` 保持只含运行时文件。`harness.py` 是唯一知道 `loop/scripts`
   位置的地方：加载时把它放进 `sys.path` 并导出 `dispatch`（取代各文件的
   `sys.path.insert` 与三个 `importlib` 相对路径加载），并提供唯一的 `run(...)`
   取代 10 份 run helper。CI 工作目录、卫生扫描白名单里的测试路径、文件头跑法注释
   同步改。
10. **迁移策略**：按文件渐进，并存边界定在文件层（CI 一个文件一个进程，旧式全局
    patch 污染不到新式文件）。生产默认适配器在**调用时**才解析模块级旧全局（不在
    构造时绑定），因此引入 `Deps` 这一步所有旧测试零改动继续绿。顺序：纯搬家 →
    引入 `Deps` 并以其取代 `hc_bin` / `app_bin` 透传 → 只新增 fake / harness /
    fixtures / 检查 → 先迁三个供应方测试文件（暴露 `FakeHerdr` 装不下的旋钮，旧桩类
    原地保留作过渡）→ 其余消费方逐个迁 → 删旧桩与跨文件 import。步骤 3 完成时加
    **棘轮守卫**：禁止 `tests/` 内出现 `dispatch.<名> =` 赋值与 `os.environ[...] =`
    写入，白名单列出尚未迁移的文件，只许缩小，终态为空。怎么把这些步骤切成实施票，
    等三张设计票都定型后再定。

## 网关之后：稳定与临时

**稳定**（网关与管线定型后不动）：决定 1 的测试入口；`Deps` 作为一个对象及其
时钟 / 环境 / 工具定位 / `http_open` / `run_cmd` / `extract` 字段；全局副作用归零与
棘轮守卫；`FakeHerdr` 的世界状态与世界事件方法；`tests/` 布局与 `harness.run`；
迁移的并存边界；fixtures 本身。

**临时**（网关定型后重写）：`Deps.hc_json` / `Deps.app_json` 两个字段，换成一个
领域级字段；`FakeHerdr` 的 argv 级两个入口，换成领域级入口（状态机不重写）；故障与
延迟声明里的命令名键，改键成领域操作名；契约一致性检查器与 `hc_json` / `app_json`
解析测试，下沉成网关生产适配器自己的测试（仍用 `run_cmd` 桩加同一批 fixtures），
不再属于 `FakeHerdr` 一侧。

**交接**：网关设计票须对三项未实测假设给出处置，并重命名故障键；管线设计票接手
`learning.enabled()` 的环境变量洞，`harness.run` 停在 argv 层、管线落地只改 `main`
内部转发。

## Considered Options

- **seam 下沉到 `run_cmd`**：fake 说 argv→rc/stdout/stderr，错误映射天然被覆盖，
  但 fake 要自己解析 argv，而这层形状在网关定型后整体作废，丢弃成本最高。否——
  错误映射改由解析测试守住。
- **等网关定型再立测试缝**：否——那样网关与管线重构都在无护栏状态下进行，这正是
  评审首推测试缝的原因；代价（fake 的 argv 入口要重写一次）已显式标为临时。
- **多份按文件各自的桩（现状）**：否——手抄同步、偏差无主，且互相 import。
- **每个依赖一个 Protocol 类**：否——没有第二种实现就不值得多一层声明。
- **模块级当前依赖 / contextvar**：否——等于换名的全局副作用，违背「接受依赖、
  不要创建依赖」。
- **fake 键集与真实精确相等**：否——被无关字段拖累，改为必填键＋枚举域＋类型。
- **测试放 `loop/tests/`**：否——`loop/` 是部署单元，开发产物不跟部署单元走。
- **为盲区新增注入点（文件写、subprocess 注入）**：否——它们是本地可替代依赖，
  真实临时目录与真实无害进程即可测。

## Consequences

- 引入 `Deps` 的那一步对既有测试零影响；之后每迁一个文件都是独立可验证的一步，
  棘轮守卫保证 monkeypatch 只减不增。
- 已接受的代价：`FakeHerdr` 的 argv 级入口、故障键、一致性检查器是过渡形状，网关
  定型后要重写一次；世界状态、`Deps`、harness、fixtures、布局是不丢的资产。
- 三项未实测假设在 fake 里是假设，不是事实；在网关票处置之前，依赖这三处的行为不得
  被当成经验证的契约。
- 本 ADR 补充而不取代 ADR-0001…0015；对 ADR-0012 的关系仅限于 `learning.enabled()`
  的环境变量读取待候选⑤收口。
- 隐私：样例由实施票手工提取脱敏最小集带进公开仓库（占位符化，不含本机路径、
  用户名、主机名、真实会话标题或 token）；研究分支仍只留本地。
