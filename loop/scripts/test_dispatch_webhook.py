#!/usr/bin/env python3
"""test_dispatch_webhook.py — 可选 webhook 完成通知测试（mock hc/pi/urllib，不打真实端点）。

跑法：python3.12 loop/scripts/test_dispatch_webhook.py
覆盖：
  1 默认关：未配 env，webhook=off，不 POST，无 webhook_error。
  2 配 env＋POST 成功：webhook=sent，Authorization: Bearer、无 X-Loop-Secret、带 release_id/target/task_name、无 expect_proc、密钥不进 stdout。
  3 配 env＋POST 失败（OSError）：webhook=failed＋webhook_error 类名，整轮仍 ok。
  4 error 路径也发 webhook、带 webhook 与 release_id/target/task_name（无 prompt_sha256/expect_proc）。
  5 只配 URL 或只配密钥：仍 off。
  6 HTTP 401：webhook=failed＋webhook_error="HTTP 401"，密钥/URL 不进 stdout/结果/stderr。
  7 URLError：webhook_error="URLError"（只记类名，不记 str(e)）。
  8 早停 error（bad_release_id，未算 task_name）：仍带 release_id/target，且即使传 --expect-proc 也不进载荷。
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import urllib.error
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
    posted = []

    def use(fn):
        dispatch.webhook_open = lambda req, timeout, proxies: fn(req, timeout)

    def ok_urlopen(req, timeout=None):
        posted.append((req, timeout))
        return FakeResp()

    def fail_urlopen(req, timeout=None):
        posted.append((req, timeout))
        raise OSError("connection refused")

    try:
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
        req, timeout = posted[0]
        assert req.full_url == URL and timeout == dispatch.WEBHOOK_TIMEOUT_S
        assert req.get_method() == "POST"
        assert req.get_header("Authorization") == "Bearer " + SECRET, req.headers
        assert req.get_header("X-loop-secret") is None, req.headers  # 旧头不再发送
        body = json.loads(req.data.decode("utf-8"))
        assert body["ok"] is True and body["target"] == "t99", body
        assert body["release_id"] == "wh-sent-1" and res["release_id"] == "wh-sent-1", body
        assert body["task_name"] == dispatch.generate_short_task_name("wh-sent-1"), body
        assert "expect_proc" not in body and "expect_proc" not in res, body
        assert "webhook" not in body  # POST 发生在记 webhook 字段之前
        assert SECRET not in json.dumps(res, ensure_ascii=False)  # 密钥不进 stdout
        assert "webhook_error" not in res, res
        print("2 env set + POST ok: webhook=sent, Authorization Bearer, no X-Loop-Secret ok")

        # 3 配 env＋POST 失败（OSError）：整轮仍 ok，webhook_error 记类名
        set_env(URL, SECRET)
        posted.clear()
        use(fail_urlopen)
        errbuf = io.StringIO()
        with contextlib.redirect_stderr(errbuf):
            rc, res, led = run_case(None, FakeHc(), "wh-fail-1")
        assert rc == 0 and res["ok"] and res["webhook"] == "failed", res
        assert res["webhook_error"] == "OSError", res
        assert len(posted) == 1
        errtext = errbuf.getvalue()
        assert "OSError" in errtext and SECRET not in errtext and URL not in errtext, errtext
        print("3 POST fails: webhook=failed, webhook_error=OSError, round still ok ok")

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

        # 6 HTTP 401：记状态码，密钥/URL/头内容不进 stdout/结果/stderr
        def http401_urlopen(req, timeout=None):
            posted.append((req, timeout))
            raise urllib.error.HTTPError(URL, 401, "Unauthorized", {}, None)

        set_env(URL, SECRET)
        posted.clear()
        use(http401_urlopen)
        errbuf = io.StringIO()
        with contextlib.redirect_stderr(errbuf):
            rc, res, led = run_case(None, FakeHc(), "wh-401-1")
        assert rc == 0 and res["ok"] and res["webhook"] == "failed", res
        assert res["webhook_error"] == "HTTP 401", res
        dumped = json.dumps(res, ensure_ascii=False)
        assert SECRET not in dumped and URL not in dumped and "Bearer" not in dumped, res
        errtext = errbuf.getvalue()
        assert "HTTP 401" in errtext, errtext
        assert SECRET not in errtext and URL not in errtext and "Bearer" not in errtext, errtext
        print("6 HTTP 401: webhook_error='HTTP 401', no secret/URL in stdout/result/stderr ok")

        # 7 URLError：只记类名，不记 str(e)（reason 可能带主机信息）
        def urlerror_urlopen(req, timeout=None):
            posted.append((req, timeout))
            raise urllib.error.URLError("reason-with-host-detail")

        set_env(URL, SECRET)
        posted.clear()
        use(urlerror_urlopen)
        errbuf = io.StringIO()
        with contextlib.redirect_stderr(errbuf):
            rc, res, led = run_case(None, FakeHc(), "wh-urlerr-1")
        assert rc == 0 and res["ok"] and res["webhook"] == "failed", res
        assert res["webhook_error"] == "URLError", res
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
                    "--expect-proc", "claude",
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
    finally:
        dispatch.webhook_open = real_open
        set_env(None, None)

    print("all webhook tests passed")


if __name__ == "__main__":
    main()