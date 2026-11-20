#!/usr/bin/env python3
"""test_dispatch_webhook.py — 可选 webhook 完成通知测试（mock hc/pi/urllib，不打真实端点）。

跑法：python3.12 loop/scripts/test_dispatch_webhook.py
覆盖：
  1 默认关：未配 env，webhook=off，不 POST。
  2 配 env＋POST 成功：webhook=sent，POST 结果 JSON、密钥走 header、不进 stdout。
  3 配 env＋POST 失败：webhook=failed，整轮仍 ok（结果文件是事实源）。
  4 error 路径也发 webhook、带 webhook 字段。
  5 只配 URL 或只配密钥：仍 off。
"""
import json
import os
import sys
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
    real_urlopen = dispatch.urllib.request.urlopen
    posted = []

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
        dispatch.urllib.request.urlopen = ok_urlopen
        rc, res, led = run_case(None, FakeHc(), "wh-off-1")
        assert rc == 0 and res["ok"] and res["webhook"] == "off", res
        assert posted == [], posted
        print("1 default off: no env -> webhook=off, no POST ok")

        # 2 配 env＋POST 成功
        set_env(URL, SECRET)
        posted.clear()
        dispatch.urllib.request.urlopen = ok_urlopen
        rc, res, led = run_case(None, FakeHc(), "wh-sent-1")
        assert rc == 0 and res["ok"] and res["webhook"] == "sent", res
        assert len(posted) == 1, posted
        req, timeout = posted[0]
        assert req.full_url == URL and timeout == dispatch.WEBHOOK_TIMEOUT_S
        assert req.get_method() == "POST"
        assert SECRET in req.headers.values(), req.headers
        body = json.loads(req.data.decode("utf-8"))
        assert body["ok"] is True and body["target"] == "t99", body
        assert "webhook" not in body  # POST 发生在记 webhook 字段之前
        assert SECRET not in json.dumps(res, ensure_ascii=False)  # 密钥不进 stdout
        print("2 env set + POST ok: webhook=sent, secret in header not stdout ok")

        # 3 配 env＋POST 失败：整轮仍 ok
        set_env(URL, SECRET)
        posted.clear()
        dispatch.urllib.request.urlopen = fail_urlopen
        rc, res, led = run_case(None, FakeHc(), "wh-fail-1")
        assert rc == 0 and res["ok"] and res["webhook"] == "failed", res
        assert len(posted) == 1
        print("3 POST fails: webhook=failed, round still ok ok")

        # 4 error 路径也发 webhook
        set_env(URL, SECRET)
        posted.clear()
        dispatch.urllib.request.urlopen = ok_urlopen
        rc, res, led = run_case(None, FakeHc(refuse_nth=1), "wh-err-1")
        assert rc == 1 and not res["ok"] and res["webhook"] == "sent", res
        assert len(posted) == 1
        body = json.loads(posted[0][0].data.decode("utf-8"))
        assert body["ok"] is False, body
        print("4 error path also notifies webhook ok")

        # 5 只配一半：仍 off
        for tag, u, s in (("url", URL, None), ("secret", None, SECRET)):
            set_env(u, s)
            posted.clear()
            dispatch.urllib.request.urlopen = ok_urlopen
            rc, res, led = run_case(None, FakeHc(), "wh-half-" + tag)
            assert rc == 0 and res["webhook"] == "off" and posted == [], (tag, res)
        print("5 half-configured (only URL or only secret): still off ok")
    finally:
        dispatch.urllib.request.urlopen = real_urlopen
        set_env(None, None)

    print("all webhook tests passed")


if __name__ == "__main__":
    main()