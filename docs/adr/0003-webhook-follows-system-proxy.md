---
status: accepted
---

# webhook 出网默认跟随本机系统代理

## 背景

2026-11-21 两轮真实运行（放行 `hex727-pr-20261121-0044`、`hex727-a2-20261120-2331`）webhook 均 `failed`、只报 `"URLError"`。排查本机发现：环境里只有 `NO_PROXY` 与非标准的 `JEV_UPSTREAM_PROXY`，Python 的 `getproxies()` 因此返回 `{'no': …, 'jev_upstream': …}`——非空但没有任何 `http`/`https` 键，urllib 不建对应代理处理器，webhook 实际走了直连；而本机外网只能经 Clash（`127.0.0.1:7890`）出去。macOS 系统代理虽已开启，urllib 在环境变量代理非空时不会读它。

## 决定

webhook POST 默认跟随本机系统代理：

1. 标准 `http_proxy`/`https_proxy` 显式配置时优先（source=env）；
2. 否则读系统代理（macOS SystemConfiguration，source=system）；
3. 系统例外表或 `no_proxy` 命中则直连（source=bypass）；
4. 探测失败标 unknown，不猜。

只作用于 webhook 的 POST（独立 opener），不改进程全局代理、不关证书校验、不改系统代理设置。诊断字段 `proxy` 增加 `source`（`env`/`system`/`bypass`/`none`/`unknown`）。

## Considered Options

- **只让调用方补 `https_proxy` 环境变量**：不改代码，但每个调用方都要配，且现有 `no_proxy` 不含内网段，内网端点会被塞进代理。
- **维持现状（直连）**：与已开启的系统代理矛盾；外网 webhook 在只通代理的机器上必然失败。
- **硬编码 `127.0.0.1:7890`**：把端口写死，换机器或换端口就错。
- **跟随本机系统代理**（采用）：与用户日常上网行为一致，内网例外沿用系统设置，标准环境变量仍可覆盖。

## Consequences

- macOS 上外部 webhook 默认经系统代理出网；内网与 localhost 按系统例外表直连。
- 非 macOS 平台没有系统代理回退，仍按标准环境变量；诊断 `source` 如实反映。
- urllib 不按协议分派 `ALL_PROXY`，不作为可用来源；只配 ALL_PROXY 时回退系统代理。
- 代理是否连通仍可能失败；失败证据看 `webhook_error_detail`（仍在 ADR-0002 的 webhook 机制内）。

> 重试与直连兜底补记（2026-11-23）：实测经代理出网偶发 TLS 握手阶段 `SSLEOFError`（~0.21s 瞬断）。`send_webhook()` 现对选定出口的传输层异常与 `HTTP 5xx` 最多试 3 次（首发＋2 次重试，退避 0.5s、1.0s）；若选用了代理且 3 次全败，再以 `ProxyHandler({})` 强制跳过环境与系统代理直连兜底 1 次（`HTTP 4xx` 立刻失败、不重试不兜底）。全败时 `webhook_error` 与 `webhook_error_detail` 保留主路由最后一次错误以对齐 `proxy`，并附 `attempts`、`fallback_direct`、`fallback_error`。本文「按系统代理优先、不改进程全局代理、不关证书校验」原则不变。
