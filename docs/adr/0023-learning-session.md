---
status: accepted
---

# 学习会话：四个动词、会话持有 `fact`、空对象独立成类

## 背景

评审 #53 候选⑤：学习（ADR-0012）的接入代码散在 `main()` 里。ADR-0020 决定 13 只堵了两处
——`learning.enabled(env)` 不再直读环境变量，抽取改为显式的
`deps.extract(request, turn_path, injection) -> Extraction`——并规定管线在四个点通过
`state.learning` 调用、关闭时它是空对象，把对象本身的形状留给本票。`LearningStore` 内部与
学习案例的存储格式不动，ADR-0012 与 ADR-0001…0022 不重议。

核对现行代码得到几条事实，决定都建立在它们之上：

1. `LearningStore` 现有三个公开事务：`settle_and_record`、`build_injection`、
   `record_prediction`。每个事务自己捕获所有异常，只返回 `{warning}` 一类 dict，不外抛。
2. 「解析 space id」目前是 `main` 里的内联代码（`resolve_space_id_for_learning`），不属于
   `LearningStore`；开启时它多读一次 `ls`，关闭时整段跳过。
3. 四个调用点都由 `main` 往 ctx 写：`next_skill_feedback` 只来自结算，`learning_warning`
   四处写入、后写覆盖先写（ADR-0022 决定 6 已冻结这一行为，并把「谁来调 `fact`」留给本票）。
4. 注入得到的 `sequence`、`examples_used`、`sequence_used` 现在是 `main` 里的三个局部变量，
   到记录预测时才被消费。
5. `learning_dir()` 自己读 `os.environ`（`LOOP_LEARNING_DIR`、`XDG_DATA_HOME`）与
   `Path.home()`；`now_iso()` 用真实时钟，在 `LearningStore` 内被调用四次。
6. `adopt` 在创建、复活、既有三条路径上都会无条件先查 herdr-app，所以到学习准备时
   `missing_tool` 早已在领养处报过。
7. ADR-0020 的占位名 `begin` / `after_extract` / `finish` 与四个调用点对不上：一轮结束时
   学习侧没有任何工作要做。

## 决定

（grill 裁定，2026-11-25；纯内部结构，零外部契约变更——flag、error kind、`result.json`、
`notify.json` 形状、学习存储格式与 `learning_warning` 的四处写入次序均不动。）

1. **四个动词，一个调用点一个动词，不设 `finish`**：

   | 阶段 | 动词 | 入参 |
   |---|---|---|
   | `learning_prepare` | `begin(resolve_space)` | 解析 space id 的回调 |
   | `learning_settle` | `settle(first_line)` | 主任务首个非空行 |
   | `extract_verify`，抽取前 | `inject() -> str` | 无 |
   | `extract_verify`，抽取并校验后 | `record(extraction, complete)` | 抽取结果、折叠判据产出的 `complete` |

   `begin` / `after_extract` / `finish` 只是 ADR-0020 的占位名，到此作废。`LearningSession`
   是 `LearningStore` 之上的一层薄包装，新增的只有三件事：持有 space id、持有注入元数据、
   把事务结果搬进 `fact`。`LearningStore` 的事务逻辑与存储格式不动。删除测试：删掉会话，
   `if learning_on` 分支、三个局部变量与四处 ctx 写入会散回四个调用点。
2. **会话持有 `fact` 写入口，动词不返回 feedback / warning**：
   - 会话构造时拿到 `state.fact`，动词内部写 `fact("next_skill_feedback", ..., ledger=False)`
     与 `fact("learning_warning", ..., ledger=False)`，仅在值非空时写，与现状逐行等价。
   - 后写覆盖先写由 `fact` 本身的语义保证（ADR-0022 决定 6），会话不合并、不累积；
     `Journal` 仍对学习完全无感，接缝只有 `fact` 一个口。
   - 结算之后抽取失败时反馈仍在 error 载荷里：结算那一刻已写进 ctx。
3. **构造：`admit` 里一次**，仍遵守 ADR-0020 决定 13：

   ```
   state.learning = learning.open(env=deps.env, now=deps.now, fact=state.fact)
   ```

   - `LOOP_LEARNING=off` 返回空对象（决定 4），否则返回 `LearningSession`。
   - `learning_dir(env)` 与 `enabled(env)` 一样改成吃 `env`，包括用 `env["HOME"]` 取代
     `Path.home()`，harness 不写 `os.environ` 即可指定存储目录。
   - 学习事件的时间戳改走 `deps.now`，与 ADR-0022 决定 7 里 `Journal` 取墙钟的口径一致；
     `Deps` 若尚无墙钟字段，由落地片补，与 `Journal` 共用同一个。
   - 锁等待的 `monotonic` 与 `sleep` 仍用真实时间：它等的是真实文件锁，不虚拟化。
   - 对 `LearningStore` 唯一的触碰是机械的：构造参数加 `now`（默认现行 `now_iso`），四处
     `now_iso()` 调用改为 `self._now()`；事务逻辑与存储格式不动。
4. **空对象是独立的 `Disabled` 类**：与 `LearningSession` 同在 `learning.py`，四个动词全是
   空操作——`begin` 不调回调、`settle` / `record` 无返回、`inject()` 返回 `""`。两个类不继承
   ABC、不设 Protocol（ADR-0020 决定 10 的鸭子类型口径）。防漂移靠一条 parity 测试：用
   `inspect.signature` 逐个比对两个类四个动词的签名。调用点不写 `if on`。
5. **space id 解析：回调归管线，警告归 `begin`**：
   - `begin` 收的是回调、不是预先解析的 id。预先解析要么在阶段里写 `if on`，要么关闭时也
     白读一次 `ls`，后者会改变 `FakeHerdr` 命令序列、破坏特征化快照。
   - 回调由 `learning_prepare` 阶段构造：先 `herdr.require_app()`（缺失照旧是主流程的
     `missing_tool`，不吞；见背景 6，实际不可达）；再**新读一次** `herdr.layout()`，
     `HerdrFailure` 返回 `None`（现状：`ls` 失败即 `None`）；再对 `request.space` 跑
     `plan_space`（ADR-0020 决定 7：与领养共用的只读匹配半边），仅 `reuse(id)` 取 id，
     创建、歧义、未找到一律 `None`，绝不建空间。
   - 必须新读、不复用 `ensure_visible` 读过的 `layout`：领养刚 `create_space` 之后，旧
     `layout` 里没有新 id，复用会让新建空间那一轮被误判为「解析不出」。
   - 真实 `begin` 拿到 `None` 时写 `space_id_missing` 警告（detail 文案原样搬走），并把
     `None` 记在会话里；后续动词原样把它传给 `LearningStore`，它内部已有 space id 缺失
     分支，行为不动。`Disabled.begin` 不调回调，所以关闭时零 `ls`、零 `require_app`。
   - `learning.py` 不 import `pipeline`，依赖方向 `dispatch → pipeline → {…, learning, …}`
     不变，所以才用回调。
6. **一轮内状态由会话持有，`record` 签名瘦身**：
   - `inject()` 把 `sequence`、`examples_used`、`sequence_used` 存在会话实例上，`record()`
     直接取，调用点不再传。`inject` 未调用或注入失败时取现状初值（`[]`、`0`、`False`）。
     会话一轮一个、不复用。
   - `Extraction` 已带 `reply`，现状传给记录预测的正是抽取返回的同一个 `reply`（中间只被
     `closest_fragment` 写进 `rewritten`，`reply` 本身不变），所以 `record` 不再单独收
     `reply`；预测取自 `extraction.fields` 的 `next_skill`，无效标记取自
     `extraction.next_skill_invalid`。
7. **锁与事务边界：不入 interface，用探测测试守不变量**：
   - 每个动词对应至多一次短事务，锁的临界区留在 `LearningStore` 内部，会话对管线不暴露
     任何锁或事务对象。唯一横跨抽取的动词对是 `inject` 与 `record`，它们之间不持有任何
     东西，只有决定 6 的内存元数据。
   - ADR-0012「锁绝不跨抽取调用」用一条 harness 级测试守住：测试里的 `deps.extract` 假实现
     被调用时，对存储目录里的 `.lock` 尝试一次非阻塞 `flock`，必须立刻拿到。真实文件锁，
     不虚拟化。
8. **失败降级：会话内吸收，不加第二层 `try`**：
   - 四个动词永不外抛。吸收仍在 `LearningStore` 现有的每方法 catch-all 里，会话只做
     dict → `fact` 的搬运，不新增 `try/except`、不做管线侧的 kind 映射；`learning_warning`
     的后写覆盖语义已由 ADR-0022 冻结。
   - 唯一例外是回调里的 `require_app()` 缺失：它不被吞，照旧是主流程的 `missing_tool`。
   - 会话单测做故障注入：权限错误、锁超时、记录损坏，各验证「动词不抛、`learning_warning`
     落 ctx、主轮仍 `ok`」。
9. **GLOSSARY 不加词条**：`LearningSession`、`Disabled`、动词名都是代码名，沿用 ADR-0016
   决定 13 的口径；现有「学习案例」「近期 skill 序列」「next_skill 反馈」已覆盖领域语义。

## 与既有 ADR 的关系

- **ADR-0012**：行为一字不变；锁的短临界区、降级为 `learning_warning`、`LOOP_LEARNING=off`
  语义全部保留，本 ADR 只给它们换了表达位置与测试手段。
- **ADR-0016**：决定 7 末尾「待⑤收口」的 `learning.enabled()` 环境变量洞，至此连同存储目录
  与时间戳一并收口，全部走 `deps.env` 与 `deps.now`。
- **ADR-0020**：决定 13 的最后一句「`begin` / `after_extract` / `finish` 的形状、空对象本身
  的形状仍归 ⑤」由本 ADR 定写；四个调用点的位置、`learning.enabled(env)` 与 `Extraction`
  不变。`resolve_space_id_for_learning` 随决定 5 退役，成为 `plan_space` 的只读投影。
- **ADR-0021**：⑤ 的落地挂接点见下节；其「清单不建实施票」的口径不变。
- **ADR-0022**：决定 6 里「谁来调 `fact`」由决定 2 回答：会话持有写入口。

## Considered Options

- **动词返回结果，管线拿到后调 `state.fact`**：否——每个调用点都要写「拿到非空才写」，条件
  分支只是从 `if learning_on` 挪成 `if fb is not None`，空对象还得返回空值。
- **保留 `begin / after_extract / finish` 三个动词**：否——对不上四个调用点，`finish` 没有
  任何工作可做；删掉它不会让复杂度在别处重现。
- **预先解析 space id 再传入 `begin`**：否——见决定 5，关闭时多一次 `ls` 会改变快照。
- **真实对象带 `enabled` 开关代替空对象**：否——每个动词开头都要判断，把条件分支搬进对象
  内部且更难测。
- **管线持有注入结果、显式传给 `record`**：否——管线会多出三个局部变量，正是本票要消掉的
  东西。
- **暴露事务上下文管理器，让管线表达「锁不跨抽取」**：否——把锁泄漏进管线的 interface；
  探测测试更直接地证明不变量。
- **会话内再包一层 `try/except`，或由管线把学习故障映射成 kind**：否——存储层已吸收且从不
  外抛，第二层只会掩盖真正的编程错误；也不新增 error kind（ADR-0012）。
- **虚拟化锁等待的时间**：否——被等的是真实文件锁，虚拟时钟只会让测试与真实行为分叉。
- **为学习会话新增 GLOSSARY 词条**：否——代码名不进词表。

## 交接：落地片

ADR-0021 决定 9 已写明 ⑤ 的落地挂在 `P3` 之后、不建实施票。本 ADR 补充两点，供切片清单
追加：

- **纯新增部分可先行**：`learning_dir(env)`、`LearningStore` 的 `now` 参数、
  `LearningSession`、`Disabled`、`open`，以及会话单测、parity 测试、故障注入测试。这些
  不改 `dispatch.py`，不与串行重构片冲突；`Deps` 若缺墙钟字段，在此补。
- **接线随 `P3`**：`state.learning` 的四个调用点、`learning_prepare` 里的回调、
  `args.learning_injection` 与 `args.learning_next_skill_invalid` 的退役、
  `resolve_space_id_for_learning` 的删除、抽取期探测 `.lock` 的 harness 测试。特征化快照
  须覆盖 `LOOP_LEARNING` 开与关两种状态，存储目录经 `deps.env` 指向临时目录。

## Consequences

- `main` 里的 `if learning_on` 分支、三个局部变量、四处 ctx 写入收口到 `LearningSession`
  一处；换学习策略时调用点不动。
- 代价：多一个 `Disabled` 类与一条 parity 测试；`LearningStore` 有四处机械的时间戳调用
  改动。学习的环境变量洞与真实时钟因此全部关闭，harness 可以在不写 `os.environ` 的前提下
  覆盖学习开启的路径。
- `space_id_missing` 的四处警告文案与覆盖次序被如实保留，不借机统一。
- 隐私：本 ADR 与相关快照样例同样遵守公开仓库红线。
