#!/usr/bin/env python3
"""test_dispatch_webhook.py — 可选 webhook 完成通知测试（mock hc/pi/urllib，不打真实端点）。

跑法：python3.12 loop/scripts/test_dispatch_webhook.py
覆盖：
  1 默认关：未配 env，webhook=off，不 POST，无 webhook_error。
  2 配 env＋POST 成功：webhook=sent，Authorization: Bearer、无 X-Loop-Secret、成功正文严格 11 字段投影（release_id/target 在内、task_name 只在全量结果）、无 expect_proc、密钥不进 stdout。
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
"""
import contextlib
import io
import json
import os
import ssl
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dispatch  # noqa: E402
from test_dispatch_reset import FakeHc, run_case  # noqa: E402

URL = "https://example.invalid/hook"
SECRET = "s3cret-token"


class FakeResp:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return b"ok"


def set_env(url=None, secret=None):
    for k, v in (("LOOP_WEBHOOK_URL", url), ("LOOP_WEBHOOK_SECRET", secret)):
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def main():
    real_open = dispatch.webhook_open
    real_proxy_res = dispatch.webhook_proxy_resolution
    real_sleep = dispatch.time.sleep
    posted = []
    sleeps = []

    def use(fn):
        dispatch.webhook_open = lambda req, timeout, proxies: fn(req, timeout, proxies)

    def ok_urlopen(req, timeout=None, proxies=None):
        posted.append((req, timeout, proxies))
        return FakeResp()

    def fail_urlopen(req, timeout=None, proxies=None):
        posted.append((req, timeout, proxies))
        raise OSError("connection refused")

    try:
        dispatch.webhook_proxy_resolution = lambda url: ({}, "none")
        dispatch.time.sleep = lambda s: sleeps.append(s)
        # 1 默认关：未配 env
        set_env(None, None)
        posted.clear()
        use(ok_urlopen)
        rc, res, led = run_case(None, FakeHc(), "wh-off-1")
        assert rc == 0 and res["ok"] and res["webhook"] == "off", res
        assert posted == [], posted
        assert "webhook_error" not in res, res
        print("1 default off: no env -> webhook=off, no POST ok")

        # 2 配 env＋POST 成功
        set_env(URL, SECRET)
        posted.clear()
        use(ok_urlopen)
        rc, res, led = run_case(None, FakeHc(), "wh-sent-1")
        assert rc == 0 and res["ok"] and res["webhook"] == "sent", res
        assert len(posted) == 1, posted
        req, timeout, _p = posted[0]
        assert req.full_url == URL and timeout == dispatch.WEBHOOK_TIMEOUT_S
        assert req.get_method() == "POST"
        assert req.get_header("Authorization") == "Bearer " + SECRET, req.headers
        assert req.get_header("X-loop-secret") is None, req.headers  # 旧头不再发送
        body = json.loads(req.data.decode("utf-8"))
        assert body["ok"] is True and body["target"] == "t99", body
        assert body["release_id"] == "wh-sent-1" and res["release_id"] == "wh-sent-1", body
        # 成功通知是 11 字段投影（ADR-0014）：task_name 等审计字段只在全量结果，不进 POST 正文
        assert set(body) == set(dispatch.NOTIFY_SUCCESS_FIELDS), body
        assert "task_name" not in body and res["task_name"] == dispatch.generate_short_task_name("wh-sent-1"), body
        assert "expect_proc" not in body and "expect_proc" not in res, body
        assert "webhook" not in body  # POST 发生在记 webhook 字段之前
        assert SECRET not in json.dumps(res, ensure_ascii=False)  # 密钥不进 stdout
        assert "webhook_error" not in res, res
        print("2 env set + POST ok: webhook=sent, 11-field projection, Authorization Bearer ok")

        # 3 配 env＋POST 失败（OSError，无代理）：重试共 3 次，整轮仍 ok，webhook_error 记类名
        set_env(URL, SECRET)
        posted.clear()
        use(fail_urlopen)
        errbuf = io.StringIO()
        with contextlib.redirect_stderr(errbuf):
            rc, res, led = run_case(None, FakeHc(), "wh-fail-1")
        assert rc == 0 and res["ok"] and res["webhook"] == "failed", res
        assert res["webhook_error"] == "OSError", res
        assert res["webhook_error_detail"]["attempts"] == 3, res
        assert "fallback_direct" not in res["webhook_error_detail"], res
        assert len(posted) == 3, posted
        errtext = errbuf.getvalue()
        assert "OSError" in errtext and SECRET not in errtext and URL not in errtext, errtext
        print("3 POST fails (direct): 3 attempts, webhook=failed, webhook_error=OSError ok")

        # 4 error 路径也发 webhook
        set_env(URL, SECRET)
        posted.clear()
        use(ok_urlopen)
        rc, res, led = run_case(None, FakeHc(refuse_nth=1), "wh-err-1")
        assert rc == 1 and not res["ok"] and res["webhook"] == "sent", res
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
            set_env(u, s)
            posted.clear()
            use(ok_urlopen)
            rc, res, led = run_case(None, FakeHc(), "wh-half-" + tag)
            assert rc == 0 and res["webhook"] == "off" and posted == [], (tag, res)
        print("5 half-configured (only URL or only secret): still off ok")

        # 6 HTTP 401：不重试，记状态码，密钥/URL/头内容不进 stdout/结果/stderr
        def http401_urlopen(req, timeout=None, proxies=None):
            posted.append((req, timeout, proxies))
            raise urllib.error.HTTPError(URL, 401, "Unauthorized", {}, None)

        set_env(URL, SECRET)
        posted.clear()
        use(http401_urlopen)
        errbuf = io.StringIO()
        with contextlib.redirect_stderr(errbuf):
            rc, res, led = run_case(None, FakeHc(), "wh-401-1")
        assert rc == 0 and res["ok"] and res["webhook"] == "failed", res
        assert res["webhook_error"] == "HTTP 401", res
        assert res["webhook_error_detail"]["attempts"] == 1, res
        assert len(posted) == 1, posted
        dumped = json.dumps(res, ensure_ascii=False)
        assert SECRET not in dumped and URL not in dumped and "Bearer" not in dumped, res
        errtext = errbuf.getvalue()
        assert "HTTP 401" in errtext, errtext
        assert SECRET not in errtext and URL not in errtext and "Bearer" not in errtext, errtext
        print("6 HTTP 401: no retry, attempts=1, no secret/URL in stdout/result/stderr ok")

        # 7 URLError：只记类名，不记 str(e)（reason 可能带主机信息）
        def urlerror_urlopen(req, timeout=None, proxies=None):
            posted.append((req, timeout, proxies))
            raise urllib.error.URLError("reason-with-host-detail")

        set_env(URL, SECRET)
        posted.clear()
        use(urlerror_urlopen)
        errbuf = io.StringIO()
        with contextlib.redirect_stderr(errbuf):
            rc, res, led = run_case(None, FakeHc(), "wh-urlerr-1")
        assert rc == 0 and res["ok"] and res["webhook"] == "failed", res
        assert res["webhook_error"] == "URLError", res
        assert len(posted) == 3, posted
        assert "reason-with-host-detail" not in json.dumps(res, ensure_ascii=False)
        assert "reason-with-host-detail" not in errbuf.getvalue()
        print("7 URLError: webhook_error='URLError', str(e) never logged ok")

        # 8 早停 error（bad_release_id，未算 task_name）：带 release_id/target，不带 task_name/expect_proc
        set_env(URL, SECRET)
        posted.clear()
        use(ok_urlopen)
        with tempfile.TemporaryDirectory() as td:
            pf = Path(td) / "prompt.md"
            pf.write_text("hi\n", encoding="utf-8")
            dispatch.check_structure_and_tools = lambda: None
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = dispatch.main([
                    "--target", "t99", "--release-id", "_bad-id",
                    "--prompt-file", str(pf), "--loop-dir", td,
                    "--space", "main", "--expect-proc", "claude",
                ])
        res = json.loads(buf.getvalue().strip())
        assert rc == 1 and not res["ok"] and res["error"]["kind"] == "bad_release_id", res
        assert len(posted) == 1, posted
        body = json.loads(posted[0][0].data.decode("utf-8"))
        assert body["release_id"] == "_bad-id" and body["target"] == "t99", body
        assert "task_name" not in body and "expect_proc" not in body, body
        assert res["release_id"] == "_bad-id" and res["target"] == "t99", res
        assert "task_name" not in res and "expect_proc" not in res, res
        print("8 early error (bad_release_id): release_id/target present, no task_name/expect_proc ok")

        # 9 瞬断重试成功（前 2 次 SSLEOFError，第 3 次成功），退避 (0.5, 1.0)
        set_env(URL, SECRET)
        proxy = {"https": "http://envproxy.corp:8080"}
        dispatch.webhook_proxy_resolution = lambda url: (dict(proxy), "env")
        posted.clear()
        sleeps.clear()
        dispatch.time.sleep = lambda s: sleeps.append(s)

        def flaky_eof_open(req, timeout, proxies):
            posted.append((req, timeout, proxies))
            if len(posted) < 3:
                raise urllib.error.URLError(ssl.SSLEOFError(8, "EOF"))
            return FakeResp()

        use(flaky_eof_open)
        status, wh_err, wh_detail = dispatch.send_webhook(b'{"ok": true}')
        assert status == "sent" and wh_err is None and wh_detail is None
        assert [p[2] for p in posted] == [proxy, proxy, proxy], posted
        assert sleeps == [0.5, 1.0], sleeps
        print("9 transient SSLEOFError recovers on 3rd attempt with backoff (0.5, 1.0) ok")

        # 10 HTTP 502 可重试：第 1 次 502、第 2 次成功
        posted.clear()
        sleeps.clear()

        def flaky_502_open(req, timeout, proxies):
            posted.append((req, timeout, proxies))
            if len(posted) == 1:
                raise urllib.error.HTTPError(URL, 502, "Bad Gateway", {}, None)
            return FakeResp()

        use(flaky_502_open)
        status, wh_err, wh_detail = dispatch.send_webhook(b'{"ok": true}')
        assert status == "sent" and wh_err is None and wh_detail is None
        assert len(posted) == 2 and sleeps == [0.5], (posted, sleeps)
        print("10 HTTP 502 retries and succeeds on 2nd attempt ok")

        # 11 走代理时 HTTP 401 不重试也不直连兜底
        posted.clear()
        sleeps.clear()
        use(http401_urlopen)
        errbuf = io.StringIO()
        with contextlib.redirect_stderr(errbuf):
            status, wh_err, wh_detail = dispatch.send_webhook(b'{"ok": true}')
        assert status == "failed" and wh_err == "HTTP 401"
        assert wh_detail["attempts"] == 1 and "fallback_direct" not in wh_detail, wh_detail
        assert len(posted) == 1 and sleeps == [], (posted, sleeps)
        print("11 HTTP 401 with proxy: no retry, no direct fallback ok")

        # 12 代理 3 次全败 → 直连兜底（proxies={}）成功，且第 4 次 Request 独立无代理残留
        posted.clear()
        sleeps.clear()

        def proxy_fail_direct_ok(req, timeout, proxies):
            # 模拟 urllib ProxyHandler.proxy_open 会原地改写 req.host
            if proxies:
                req.set_proxy("envproxy.corp:8080", "https")
                posted.append((req, timeout, proxies, req.host))
                raise urllib.error.URLError(ssl.SSLEOFError(8, "EOF"))
            posted.append((req, timeout, proxies, req.host))
            return FakeResp()

        use(proxy_fail_direct_ok)
        status, wh_err, wh_detail = dispatch.send_webhook(b'{"ok": true}')
        assert status == "sent" and wh_err is None and wh_detail is None
        assert [(p[2], p[3]) for p in posted] == [
            (proxy, "envproxy.corp:8080"),
            (proxy, "envproxy.corp:8080"),
            (proxy, "envproxy.corp:8080"),
            ({}, "example.invalid"),
        ], posted
        assert sleeps == [0.5, 1.0], sleeps
        print("12 3 proxy failures fall back to fresh direct Request (proxies={}) and succeed ok")

        # 13 代理 3 次全败（SSLEOFError）＋直连也败（TimeoutError）：保留主路由诊断＋fallback 字段
        posted.clear()
        sleeps.clear()

        def proxy_eof_direct_timeout(req, timeout, proxies):
            posted.append((req, timeout, proxies))
            if proxies:
                raise urllib.error.URLError(ssl.SSLEOFError(8, "EOF"))
            raise TimeoutError("direct timed out")

        use(proxy_eof_direct_timeout)
        errbuf = io.StringIO()
        with contextlib.redirect_stderr(errbuf):
            status, wh_err, wh_detail = dispatch.send_webhook(b'{"ok": true}')
        assert status == "failed" and wh_err == "URLError", (status, wh_err)
        assert wh_detail["reason_type"] == "SSLEOFError", wh_detail
        assert wh_detail["attempts"] == 4, wh_detail
        assert wh_detail["fallback_direct"] is True and wh_detail["fallback_error"] == "TimeoutError", wh_detail
        assert len(posted) == 4 and [p[2] for p in posted] == [proxy, proxy, proxy, {}], posted
        print("13 proxy + direct both fail: primary detail preserved + attempts/fallback fields ok")

        # 14 webhook_open(..., {}) 即使环境设了 HTTPS_PROXY 也强制 ProxyHandler({}) 跳过代理
        real_build = urllib.request.build_opener
        built = []

        class FakeOpener:
            def open(self, req, timeout=None):
                return FakeResp()

        def capture_build(*handlers):
            built.append(handlers)
            return FakeOpener()

        os.environ["HTTPS_PROXY"] = "http://envproxy.corp:8080"
        try:
            urllib.request.build_opener = capture_build
            req = urllib.request.Request(URL, data=b"{}", method="POST")
            real_open(req, 5.0, {})
            assert len(built) == 1 and len(built[0]) == 1
            assert isinstance(built[0][0], urllib.request.ProxyHandler)
            assert built[0][0].proxies == {}, built[0][0].proxies
            print("14 webhook_open(..., {}) builds ProxyHandler({}) ignoring HTTPS_PROXY env ok")
        finally:
            urllib.request.build_opener = real_build
            os.environ.pop("HTTPS_PROXY", None)
    finally:
        dispatch.webhook_open = real_open
        dispatch.webhook_proxy_resolution = real_proxy_res
        dispatch.time.sleep = real_sleep
        set_env(None, None)

    print("all webhook tests passed")


if __name__ == "__main__":
    main()