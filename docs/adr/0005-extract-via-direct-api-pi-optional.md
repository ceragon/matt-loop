---
status: superseded by ADR-0027
---

# 抽取直连 API，pi 降为显式备选

> **Superseded by [ADR-0027](0027-loop-narrowed-to-pure-relay.md)**：loop 收窄为
> 纯传话工具，抽取整条线退役——API 直连与 pi 备选一并作废（env
> `LOOP_API_*`/`LOOP_EXTRACT_BACKEND`、`api_not_configured` 等 error kind、
> `--pi-model`/`--extract-timeout`）。以下原文保留作历史，不再有效。

> 补记（ADR-0026，#139）：决定 1 值域扩为 `api|pi|caller`，「默认 api」改为
> 三档规则链（显式 env 恒赢；未设但 `LOOP_API_BASE/KEY/MODEL` 任一已设＝api 意图；三个全未设
> 探 pi、探不到落 caller），受理时判一次、轮中不级联。决定 3 的
> `api_not_configured` 收窄为「api 意图明确但配置不齐」，纯未配置不再报错。
> Considered Options 里「自动探测……被否」一条被 supersede——`extract_backend`
> 逐轮记录使「猜谜」否决理由失效。api/pi 的运输层与错误映射契约全部不变。
> 详见 ADR-0026。

> 补记（ADR-0013，#50）：决定 2/7 的抽取输入语料由「整屏原文」改为「本轮原料」
> （回读 1000 行历史＋断轮切出的本轮段，`.turn.md`）；运输层（OpenAI 兼容
> chat completions、@文件 附件、env、错误映射、零回显）全部不变。详见 ADR-0013。

## 背景

ADR-0002 定「脚本干机械活，pi 干抽取」。实现里 extract() 调 pi 时带着
`--no-tools --no-session --no-extensions --no-skills --no-mcp --no-context-files
--no-prompt-templates`——pi 已被剥成零自主性的单发调用：system=extract.md、
user=@整屏原文＋指令。这与一次 chat completions API 调用没有任何区别，pi 只是
多了 node 启动、CLI 开销和 PATH 依赖（有执行机的 pi 全靠 find_tool 兜底目录才找得到）。
用户裁定：直接调 API，参数（含模型）走环境变量；pi 不删，留作显式备选。

## 决定

1. **双后端，API 默认**：`LOOP_EXTRACT_BACKEND=api|pi`，默认 `api`；非法值
   启动报 `bad_extract_backend`（退出码 2）。后端是部署级事实，走 env 不走 flag。
2. **协议只 OpenAI 兼容 chat completions**：`POST {LOOP_API_BASE}/chat/completions`，
   urllib 标准库直发，零新依赖。system=extract.md，user=指令＋**内联**整屏原文
   （pi 备选保持 @文件 附件）；extract.md 措辞改为协议无关。
3. **env 全必填、无默认**：`LOOP_API_BASE`（到 `/v1`，容忍尾斜杠）、`LOOP_API_KEY`
   （Bearer，零回显）、`LOOP_API_MODEL`（原样透传）。`backend=api` 缺任何一个，
   **启动预检**即报 `api_not_configured`（只列变量名），不发主任务、放行 id 不消耗。
   与 pi 网关复用落在**值**上（`LOOP_API_KEY="$PAW_GATEWAY_KEY"`），名字归 LOOP_
   命名空间，dispatch 不依赖 pi 的私有命名。
4. **请求参数**：temperature 0 钉死（逐字抽取零创造性）；max_tokens 默认 32768、
   `LOOP_API_MAX_TOKENS` 可覆盖（非正整数启动报 `bad_api_max_tokens`）；超时复用
   `--extract-timeout`（1200s）；不重试、不流式、不发 response_format（网关支持
   参差，「指令要 JSON＋宽容解析」是已验证走法，parse_extraction 照旧兜底）。
5. **代理与 webhook 同源**（ADR-0003）：复用 webhook_proxy_resolution 与
   webhook_open，env 显式优先、否则系统代理、例外表直连。
6. **错误映射沿用既有 kind**：HTTPError → `extraction_failed`（message 带
   `HTTP <码>`，不读响应体）；超时（含 URLError 包 TimeoutError）→ `tool_timeout`；
   其它网络异常 → `extraction_failed`（类名）；响应缺 content/非 JSON →
   `extraction_invalid`。密钥/URL 零回显（webhook 的卫生标准）。
7. **契约**：parse_extraction、逐字校验、rewritten、run_record 落盘全部不变——
   只换运输层。result 与台账 params 增加 `extract_backend`（api|pi，error 也带）。
   `--pi-model` 保留，仅 pi 备选生效；`find_tool(pi)` 只在 backend=pi 时跑。

## Considered Options

- **彻底移除 pi，只走 API**：pi 与 API 走同一网关，备选冗余≈0，双后端违背最小
  形态。被否：用户要留显式备选（网关之外的端点、本地模型等场景 pi 自有配置仍可走）。
- **CLI --extract-backend 选后端**：每轮可见性好。被否：后端是机器级部署事实，
  不是轮级选择；flag 把部署细节漏进每轮命令。
- **自动探测（配了 API env 走 API，没配回 pi）**：迁移顺滑。被否：隐式魔法，
  「走了哪条路」变猜谜；显式 env＋启动预检报错更便宜。
- **双协议（anthropic-messages＋openai，env 切换）**：对齐 pi 现有 provider。
  被否：网关本就提供 openai-completions 口；双协议把配置面/代码面/测试翻倍。
- **复用 pi 的变量名（PAW_GATEWAY_KEY 等）**：一份导出两边用。被否（用户改判）：
  pi 只有密钥一个 env 名，base/model 在其配置文件里没有 env 对应；且另一台执行机的 pi
  用字面量占位钥匙、根本不走 env——「pi 的名字」跨机器不成立。最终三个都走 LOOP_
  家族，复用落在值上。
- **max_tokens 默认 16384**：被否（用户指出偏小）——整屏原料实测 ~15KB、中文
  token 密度高，极端轮逼近 1 万 token，余量薄；上限非开销，提到 32768。
- **温度走 env＋失败重试 1 次**：被否——抽取零创造性，温度没有调的理由；失败路径
  本就保原文可人工重做，重试只是多撞几次同一堵墙。

## Consequences

- 执行机不再必须装 node/pi（api 路径）；部署配三个 env 即可跑。
- **迁移风险**：旧部署没配 `LOOP_API_*` 的，跟新代码后默认 api 会启动即
  `api_not_configured`——须配齐变量或设 `LOOP_EXTRACT_BACKEND=pi`。PR 与
  SKILL/REFERENCE 均写明，调用方（Grok Bot 等）需自行完成配置。
- 双后端两条错误路径都要测试覆盖；pi 路径行为冻结照旧，新测试 mock run_cmd 回归。
- 抽取质量基线不变（同提示词、同契约、temp 0）；换模型只动 `LOOP_API_MODEL`。
- ADR-0002 的分工（脚本机械、模型抽取）不变，仅运输层换；0002 加日期补记，
  正文留历史。