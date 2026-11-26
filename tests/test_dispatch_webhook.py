#!/usr/bin/env python3
"""test_dispatch_webhook.py — 可选 webhook 完成通知测试（FakeHerdr 假世界，不打真实端点）。

跑法：python3 tests/test_dispatch_webhook.py
缝：唯一 harness.run（CLI 契约，T5c/ADR-0021）；env / extract / http_open /
proxy_resolution / clock 注入，断言只认 rc、stdout JSON、result.json 与 POST 请求
（URL/头/正文/proxies）。覆盖：
  1 默认关：未配 env，webhook=off，不 POST，无 webhook_error。
  2 配 env＋POST 成功：webhook=sent，Authorization: Bearer、无 X-Loop-Secret、成功正文严格 13 字段投影（release_id/target 在内、task_name 只在全量结果）、无 expect_proc、密钥不进 stdout。
  3 配 env＋POST 失败（OSError，无代理）：重试至 3 次，webhook=failed＋webhook_error=OSError＋attempts=3，整轮仍 ok。
  4 error 路径也发 webhook、带 webhook 与 release_id/target/task_name（无 prompt_sha256/expect_proc）。
  5 只配 URL 或只配密钥：仍 off。
  6 HTTP 401：不重试不兜底（attempts=1），webhook=failed＋webhook_error="HTTP 401"，密钥/URL 不进 stdout/结果/stderr。
  7 URLError：只记类名，不记 str(e)。
  8 早停 error（bad_release_id，未算 task_name）：仍带 release_id/target，且即使传 --expect-proc 也不进载荷。
  9 瞬断重试成功（前 2 次 SSLEOFError，第 3 次成功）：退避 (0.5, 1.0)，webhook=sent。
  10 HTTP 502 可重试：第 1 次 502、第 2 次成功 → webhook=sent。
  11 走代理时 HTTP 401 不重试也不直连兜底：仅 1 次、无 fallback_direct。
  12 代理 3 次全败 → 直连兜底（proxies={}）成功：3 次代理 + 1 次直连，Request 独立无代理残留，webhook=sent。
  13 代理 3 次全败（SSLEOFError）＋直连兜底也败（TimeoutError）：保留主路由诊断，带 attempts=4、fallback_direct=True、fallback_error="TimeoutError"。
  14 webhook_open(..., {}) 即使 env 有 HTTPS_PROXY 也传 ProxyHandler({}) 强制真直连。

T5c（ADR-0021，#99）：9–13 原 send_webhook 直调上移 CLI 缝（完整轮＋flaky http_open＋
经既有 clock= 参数自带记录时钟，滤掉落定静置前缀后退避子序列精确对照，原全序列断言等强）。
14 是 build_opener 单元探针（测真实 webhook_open 适配器本身），保留直调；替换手法改对
宿主模块 urllib.request 的作用域 mock.patch.object＋harness.scoped_env，退出无条件恢复
（ADR-0016 决定 7 残留洞纪律）。R（#104）：共享常量与 world 构造器迁 helpers 单一出处；FakeResp（b"ok" 版）留本地。
"""
import json
import ssl
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

import harness
from harness import dispatch
from helpers import PROMPT, EXT, world, extraction

URL = "https://example.invalid/hook"
SECRET = "s3cret-token"


class FakeResp:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return b"ok"


class RecordingClock(harness.VirtualClock):
    """自带记录时钟：sleep 序列留痕供退避断言，仍只推进虚拟时间。"""

    def __init__(self):
        super().__init__()
        self.sleeps = []

    def sleep(self, s):
        self.sleeps.append(s)
        super().sleep(s)


def _backoff(clock):
    """sleep 序列里的 webhook 退避子序列：滤掉落定静置前缀后与契约值精确对照
    （原直调用例的全序列断言等强保留，CLI 缝下静置 sleep 不属退避契约）。"""
    return [s for s in clock.sleeps if s in (0.5, 1.0)]


def run_round(rid, opener, *, env=(URL, SECRET), w=None, proxy_res=None,
              clock=None, extra_argv=()):
    """经唯一 harness.run 跑一轮完整 dispatch；env=(url, secret)，None 成员＝该键不存在。
    返回 harness 观察对象（rc/result/stderr/产物路径）。"""
    with tempfile.TemporaryDirectory() as td:
        pf = Path(td) / "prompt.md"
        pf.write_text(PROMPT, encoding="utf-8")
        argv = ["--target", "t99", "--release-id", rid,
                "--prompt-file", str(pf), "--loop-dir", str(Path(td) / ".loop"),
                "--space", "main",
                "--wall-clock", "5", "--check-in", "1",
                "--settle-seconds", "0.01", "--settle-checks", "2", *extra_argv]
        return harness.run(
            argv, world=w if w is not None else world(),
            env={"LOOP_WEBHOOK_URL": env[0], "LOOP_WEBHOOK_SECRET": env[1]},
            extract=lambda deps, request, turn_path, injection: extraction(EXT),
            http_open=opener,
            proxy_resolution=proxy_res if proxy_res is not None else (lambda url: ({}, "none")),
            clock=clock)


def main():
    posted = []

    def ok_urlopen(req, timeout=None, proxies=None):
        posted.append((req, timeout, proxies))
        return FakeResp()

    def fail_urlopen(req, timeout=None, proxies=None):
        posted.append((req, timeout, proxies))
        raise OSError("connection refused")

    # 1 默认关：未配 env
    posted.clear()
    r = run_round("wh-off-1", ok_urlopen, env=(None, None))
    res = r.result
    assert r.rc == 0 and res["ok"] and res["webhook"] == "off", res
    assert posted == [], posted
    assert "webhook_error" not in res, res
    print("1 default off: no env -> webhook=off, no POST ok")

    # 2 配 env＋POST 成功
    posted.clear()
    r = run_round("wh-sent-1", ok_urlopen)
    res = r.result
    assert r.rc == 0 and res["ok"] and res["webhook"] == "sent", res
    assert len(posted) == 1, posted
    req, timeout, _p = posted[0]
    assert req.full_url == URL and timeout == dispatch.WEBHOOK_TIMEOUT_S
    assert req.get_method() == "POST"
    assert req.get_header("Authorization") == "Bearer " + SECRET, req.headers
    assert req.get_header("X-loop-secret") is None, req.headers  # 旧头不再发送
    body = json.loads(req.data.decode("utf-8"))
    assert body["ok"] is True and body["target"] == "t99", body
    assert body["release_id"] == "wh-sent-1" and res["release_id"] == "wh-sent-1", body
    # 成功通知是 13 字段投影（ADR-0014，ADR-0019 增补）：task_name 等审计字段只在全量结果，不进 POST 正文
    assert set(body) == set(dispatch.NOTIFY_SUCCESS_FIELDS), body
    assert "task_name" not in body and res["task_name"] == dispatch.generate_short_task_name("wh-sent-1"), body
    assert "expect_proc" not in body and "expect_proc" not in res, body
    assert "webhook" not in body  # POST 发生在记 webhook 字段之前
    assert SECRET not in json.dumps(res, ensure_ascii=False)  # 密钥不进 stdout
    assert "webhook_error" not in res, res
    print("2 env set + POST ok: webhook=sent, 13-field projection, Authorization Bearer ok")

    # 3 配 env＋POST 失败（OSError，无代理）：重试共 3 次，整轮仍 ok，webhook_error 记类名
    posted.clear()
    r = run_round("wh-fail-1", fail_urlopen)
    res = r.result
    assert r.rc == 0 and res["ok"] and res["webhook"] == "failed", res
    assert res["webhook_error"] == "OSError", res
    assert res["webhook_error_detail"]["attempts"] == 3, res
    assert "fallback_direct" not in res["webhook_error_detail"], res
    assert len(posted) == 3, posted
    errtext = r.stderr
    assert "OSError" in errtext and SECRET not in errtext and URL not in errtext, errtext
    print("3 POST fails (direct): 3 attempts, webhook=failed, webhook_error=OSError ok")

    # 4 error 路径也发 webhook
    posted.clear()
    w = world()
    w.fail("send", "error", kind="status_mismatch", nth=1)   # 主任务拒发 → error 路径（旧桩同款 kind）
    r = run_round("wh-err-1", ok_urlopen, w=w)
    res = r.result
    assert r.rc == 1 and not res["ok"] and res["webhook"] == "sent", res
    assert "webhook_error" not in res, res
    assert len(posted) == 1
    body = json.loads(posted[0][0].data.decode("utf-8"))
    assert body["ok"] is False, body
    assert body["release_id"] == "wh-err-1" and body["target"] == "t99", body
    assert body["task_name"] == dispatch.generate_short_task_name("wh-err-1"), body
    assert "prompt_sha256" not in body and "expect_proc" not in body, body
    print("4 error path also notifies webhook with release_id/target/task_name ok")

    # 5 只配一半：仍 off
    for tag, u, s in (("url", URL, None), ("secret", None, SECRET)):
        posted.clear()
        r = run_round("wh-half-" + tag, ok_urlopen, env=(u, s))
        assert r.rc == 0 and r.result["webhook"] == "off" and posted == [], (tag, r.result)
    print("5 half-configured (only URL or only secret): still off ok")

    # 6 HTTP 401：不重试，记状态码，密钥/URL/头内容不进 stdout/结果/stderr
    def http401_urlopen(req, timeout=None, proxies=None):
        posted.append((req, timeout, proxies))
        raise urllib.error.HTTPError(URL, 401, "Unauthorized", {}, None)

    posted.clear()
    r = run_round("wh-401-1", http401_urlopen)
    res = r.result
    assert r.rc == 0 and res["ok"] and res["webhook"] == "failed", res
    assert res["webhook_error"] == "HTTP 401", res
    assert res["webhook_error_detail"]["attempts"] == 1, res
    assert len(posted) == 1, posted
    dumped = json.dumps(res, ensure_ascii=False)
    assert SECRET not in dumped and URL not in dumped and "Bearer" not in dumped, res
    errtext = r.stderr
    assert "HTTP 401" in errtext, errtext
    assert SECRET not in errtext and URL not in errtext and "Bearer" not in errtext, errtext
    print("6 HTTP 401: no retry, attempts=1, no secret/URL in stdout/result/stderr ok")

    # 7 URLError：只记类名，不记 str(e)（reason 可能带主机信息）
    def urlerror_urlopen(req, timeout=None, proxies=None):
        posted.append((req, timeout, proxies))
        raise urllib.error.URLError("reason-with-host-detail")

    posted.clear()
    r = run_round("wh-urlerr-1", urlerror_urlopen)
    res = r.result
    assert r.rc == 0 and res["ok"] and res["webhook"] == "failed", res
    assert res["webhook_error"] == "URLError", res
    assert len(posted) == 3, posted
    assert "reason-with-host-detail" not in json.dumps(res, ensure_ascii=False)
    assert "reason-with-host-detail" not in r.stderr
    print("7 URLError: webhook_error='URLError', str(e) never logged ok")

    # 8 早停 error（bad_release_id，未算 task_name）：带 release_id/target，不带 task_name/expect_proc
    posted.clear()
    r = run_round("_bad-id", ok_urlopen, extra_argv=("--expect-proc", "claude"))
    res = r.result
    assert r.rc == 1 and not res["ok"] and res["error"]["kind"] == "bad_release_id", res
    assert len(posted) == 1, posted
    body = json.loads(posted[0][0].data.decode("utf-8"))
    assert body["release_id"] == "_bad-id" and body["target"] == "t99", body
    assert "task_name" not in body and "expect_proc" not in body, body
    assert res["release_id"] == "_bad-id" and res["target"] == "t99", res
    assert "task_name" not in res and "expect_proc" not in res, res
    print("8 early error (bad_release_id): release_id/target present, no task_name/expect_proc ok")

    # 9 瞬断重试成功（前 2 次 SSLEOFError，第 3 次成功），退避 (0.5, 1.0)
    #   （T5c 上移 CLI 缝：完整轮＋flaky http_open＋clock= 记录退避）
    proxy = {"https": "http://envproxy.corp:8080"}
    proxy_res = lambda url: (dict(proxy), "env")
    posted.clear()
    clock9 = RecordingClock()

    def flaky_eof_open(req, timeout, proxies):
        posted.append((req, timeout, proxies))
        if len(posted) < 3:
            raise urllib.error.URLError(ssl.SSLEOFError(8, "EOF"))
        return FakeResp()

    r = run_round("wh-eof-1", flaky_eof_open, proxy_res=proxy_res, clock=clock9)
    res = r.result
    assert res["webhook"] == "sent" and "webhook_error" not in res and "webhook_error_detail" not in res, res
    assert [p[2] for p in posted] == [proxy, proxy, proxy], posted
    assert _backoff(clock9) == [0.5, 1.0], clock9.sleeps
    print("9 transient SSLEOFError recovers on 3rd attempt with backoff (0.5, 1.0) ok")

    # 10 HTTP 502 可重试：第 1 次 502、第 2 次成功
    posted.clear()
    clock10 = RecordingClock()

    def flaky_502_open(req, timeout, proxies):
        posted.append((req, timeout, proxies))
        if len(posted) == 1:
            raise urllib.error.HTTPError(URL, 502, "Bad Gateway", {}, None)
        return FakeResp()

    r = run_round("wh-502-1", flaky_502_open, proxy_res=proxy_res, clock=clock10)
    res = r.result
    assert res["webhook"] == "sent" and "webhook_error" not in res and "webhook_error_detail" not in res, res
    assert len(posted) == 2 and _backoff(clock10) == [0.5], (posted, clock10.sleeps)
    print("10 HTTP 502 retries and succeeds on 2nd attempt ok")

    # 11 走代理时 HTTP 401 不重试也不直连兜底
    posted.clear()
    clock11 = RecordingClock()
    r = run_round("wh-401p-1", http401_urlopen, proxy_res=proxy_res, clock=clock11)
    res = r.result
    assert res["webhook"] == "failed" and res["webhook_error"] == "HTTP 401", res
    detail = res["webhook_error_detail"]
    assert detail["attempts"] == 1 and "fallback_direct" not in detail, detail
    assert len(posted) == 1, posted
    assert _backoff(clock11) == [], clock11.sleeps
    print("11 HTTP 401 with proxy: no retry, no direct fallback ok")

    # 12 代理 3 次全败 → 直连兜底（proxies={}）成功，且第 4 次 Request 独立无代理残留
    posted.clear()
    clock12 = RecordingClock()

    def proxy_fail_direct_ok(req, timeout, proxies):
        # 模拟 urllib ProxyHandler.proxy_open 会原地改写 req.host
        if proxies:
            req.set_proxy("envproxy.corp:8080", "https")
            posted.append((req, timeout, proxies, req.host))
            raise urllib.error.URLError(ssl.SSLEOFError(8, "EOF"))
        posted.append((req, timeout, proxies, req.host))
        return FakeResp()

    r = run_round("wh-fallback-1", proxy_fail_direct_ok, proxy_res=proxy_res, clock=clock12)
    res = r.result
    assert res["webhook"] == "sent" and "webhook_error" not in res and "webhook_error_detail" not in res, res
    assert [(p[2], p[3]) for p in posted] == [
        (proxy, "envproxy.corp:8080"),
        (proxy, "envproxy.corp:8080"),
        (proxy, "envproxy.corp:8080"),
        ({}, "example.invalid"),
    ], posted
    assert _backoff(clock12) == [0.5, 1.0], clock12.sleeps
    print("12 3 proxy failures fall back to fresh direct Request (proxies={}) and succeed ok")

    # 13 代理 3 次全败（SSLEOFError）＋直连也败（TimeoutError）：保留主路由诊断＋fallback 字段
    posted.clear()

    def proxy_eof_direct_timeout(req, timeout, proxies):
        posted.append((req, timeout, proxies))
        if proxies:
            raise urllib.error.URLError(ssl.SSLEOFError(8, "EOF"))
        raise TimeoutError("direct timed out")

    r = run_round("wh-bothfail-1", proxy_eof_direct_timeout, proxy_res=proxy_res)
    res = r.result
    assert res["webhook"] == "failed" and res["webhook_error"] == "URLError", res
    detail = res["webhook_error_detail"]
    assert detail["reason_type"] == "SSLEOFError", detail
    assert detail["attempts"] == 4, detail
    assert detail["fallback_direct"] is True and detail["fallback_error"] == "TimeoutError", detail
    assert len(posted) == 4 and [p[2] for p in posted] == [proxy, proxy, proxy, {}], posted
    print("13 proxy + direct both fail: primary detail preserved + attempts/fallback fields ok")

    # 14 webhook_open(..., {}) 即使环境设了 HTTPS_PROXY 也强制 ProxyHandler({}) 跳过代理
    #   （单元探针：测真实 webhook_open 适配器本身；对宿主模块作用域替换，退出恢复）
    built = []

    class FakeOpener:
        def open(self, req, timeout=None):
            return FakeResp()

    def capture_build(*handlers):
        built.append(handlers)
        return FakeOpener()

    with harness.scoped_env({"HTTPS_PROXY": "http://envproxy.corp:8080"}), \
         mock.patch.object(urllib.request, "build_opener", capture_build):
        req = urllib.request.Request(URL, data=b"{}", method="POST")
        dispatch.webhook_open(req, 5.0, {})
        assert len(built) == 1 and len(built[0]) == 1
        assert isinstance(built[0][0], urllib.request.ProxyHandler)
        assert built[0][0].proxies == {}, built[0][0].proxies
    print("14 webhook_open(..., {}) builds ProxyHandler({}) ignoring HTTPS_PROXY env ok")

    print("all webhook tests passed")


if __name__ == "__main__":
    main()
