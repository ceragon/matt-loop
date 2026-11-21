#!/usr/bin/env python3
"""test_dispatch_webhook_diag.py — webhook 失败诊断测试（mock hc/pi/urllib，不打真实端点）。

跑法：python3 loop/scripts/test_dispatch_webhook_diag.py
覆盖：
  1 连接被拒（URLError/ConnectionRefusedError）：type/reason_type/errno/errno_name。
  2 DNS 失败（URLError/gaierror）：errno_name 给 EAI_*，不给 POSIX 名字。
  3 SSL 证书错误（URLError/SSLCertVerificationError）：ssl_verify_code，且不把
    OpenSSL 错误码误记成系统 errno。
  4 超时（URLError/TimeoutError）：is_timeout=true。
  5 HTTP 401：http_status=401，webhook_error="HTTP 401"。
  6 配了代理且命中：只记 configured/matches，不泄漏代理地址、端口、凭据。
  7 代理被 bypass、只有 ALL_PROXY：matches 分别 no/unknown，不猜。
  8 getproxies 探测失败：两值均 unknown。
每个端到端场景都断言：整轮仍 ok=true、webhook=failed、stdout/stderr/结果文件三处
诊断一致，且都不含 URL、密钥、Authorization 头或异常 message 里的哨兵串。
"""
import contextlib
import io
import json
import os
import shutil
import socket
import ssl
import sys
import tempfile
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dispatch  # noqa: E402
from test_dispatch_reset import EXT, PROMPT, FakeHc  # noqa: E402

URL = "https://example.invalid/hook"
SECRET = "s3cret-token"
SENTINELS = ("example.invalid", SECRET, "Bearer", "Authorization",
             "reason-with-host-detail")


def set_env(url=None, secret=None):
    for k, v in (("LOOP_WEBHOOK_URL", url), ("LOOP_WEBHOOK_SECRET", secret)):
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def assert_clean(*texts):
    for text in texts:
        for bad in SENTINELS:
            assert bad not in text, (bad, text)


def run_case(rid, urlopen):
    """跑一轮完整 dispatch，保留临时目录以读结果文件；返回 (rc, result, stderr, 结果文件文本)。"""
    td = tempfile.mkdtemp()
    try:
        pf = Path(td) / "prompt.md"
        pf.write_text(PROMPT, encoding="utf-8")
        ld = Path(td) / ".loop"
        argv = ["--target", "t99", "--release-id", rid,
                "--prompt-file", str(pf), "--loop-dir", str(ld),
                "--wall-clock", "5", "--check-in", "1",
                "--settle-seconds", "0.01", "--settle-checks", "2"]
        dispatch.hc_json = FakeHc()
        dispatch.extract = lambda pi_bin, args, raw_path: dict(EXT)
        dispatch.check_structure_and_tools = lambda: None
        dispatch.shutil.which = lambda name: "/fake/" + name
        dispatch.time.sleep = lambda s: None
        dispatch.urllib.request.urlopen = urlopen
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = dispatch.main(argv)
        result_text = (ld / "runs" / (rid + ".result.json")).read_text(encoding="utf-8")
        return rc, json.loads(out.getvalue().strip()), err.getvalue(), result_text
    finally:
        shutil.rmtree(td, ignore_errors=True)


def fail_with(exc, rid):
    """让 urlopen 抛 exc，返回整轮 (rc, result, stderr, 结果文件文本)。"""
    def fake_urlopen(req, timeout=None):
        raise exc
    return run_case(rid, fake_urlopen)


def check_common(rc, res, errtext, result_text):
    assert rc == 0 and res["ok"] is True, res          # 通知失败不连累整轮
    assert res["webhook"] == "failed", res
    assert result_text.strip() == json.dumps(res, ensure_ascii=False), "result 文件须与 stdout 逐字一致"
    detail = res["webhook_error_detail"]
    marker = json.dumps(detail, ensure_ascii=False, sort_keys=True)
    assert marker in errtext, (marker, errtext)        # stderr 与结果文件同一份诊断
    assert_clean(json.dumps(res, ensure_ascii=False), errtext, result_text)
    return detail


def set_proxies(mapping=None, bypass=False, boom=False):
    def fake_getproxies():
        if boom:
            raise OSError("proxy probe failed")
        return dict(mapping or {})

    def fake_bypass(host):
        return bypass

    dispatch.urllib.request.getproxies = fake_getproxies
    dispatch.urllib.request.proxy_bypass = fake_bypass


def main():
    real_urlopen = dispatch.urllib.request.urlopen
    real_getproxies = dispatch.urllib.request.getproxies
    real_bypass = dispatch.urllib.request.proxy_bypass
    try:
        set_env(URL, SECRET)
        set_proxies()  # 默认无代理，保证断言确定性

        # 1 连接被拒：reason 是 ConnectionRefusedError，message 里塞哨兵也不得外泄
        exc = urllib.error.URLError(
            ConnectionRefusedError(61, "refused while posting to " + URL))
        rc, res, errtext, rtext = fail_with(exc, "diag-refused-1")
        d = check_common(rc, res, errtext, rtext)
        assert res["webhook_error"] == "URLError", res
        assert d["type"] == "URLError" and d["reason_type"] == "ConnectionRefusedError", d
        assert d["errno"] == 61 and d["errno_name"] == "ECONNREFUSED", d
        assert d["is_timeout"] is False, d
        assert d["proxy"] == {"configured": "no", "matches": "no"}, d
        assert "http_status" not in d and "ssl_verify_code" not in d, d
        print("1 connection refused -> errno 61/ECONNREFUSED ok")

        # 2 DNS：gaierror 的 errno 是 EAI_*，不能按 POSIX errno 命名
        exc = urllib.error.URLError(
            socket.gaierror(socket.EAI_NONAME, "nodename for " + URL + " not known"))
        rc, res, errtext, rtext = fail_with(exc, "diag-dns-1")
        d = check_common(rc, res, errtext, rtext)
        assert d["reason_type"] == "gaierror", d
        assert d["errno"] == socket.EAI_NONAME and d["errno_name"] == "EAI_NONAME", d
        assert d["is_timeout"] is False, d
        assert "http_status" not in d, d
        print("2 DNS failure -> EAI_NONAME ok")

        # 3 SSL 证书错误：记 verify_code，不把 OpenSSL 错误码当系统 errno
        cert_err = ssl.SSLCertVerificationError(
            1, "certificate verify failed: Hostname mismatch for " + URL)
        cert_err.verify_code = 18
        cert_err.verify_message = "self-signed certificate for example.invalid"
        exc = urllib.error.URLError(cert_err)
        rc, res, errtext, rtext = fail_with(exc, "diag-ssl-1")
        d = check_common(rc, res, errtext, rtext)
        assert d["reason_type"] == "SSLCertVerificationError", d
        assert d["ssl_verify_code"] == 18, d
        assert "errno" not in d and "errno_name" not in d, d
        assert "verify_message" not in d and "self-signed" not in json.dumps(d), d
        print("3 SSL cert error -> ssl_verify_code 18, no fake errno ok")

        # 4 超时：外层 URLError 包 TimeoutError
        exc = urllib.error.URLError(TimeoutError("handshake timed out for " + URL))
        rc, res, errtext, rtext = fail_with(exc, "diag-timeout-1")
        d = check_common(rc, res, errtext, rtext)
        assert d["type"] == "URLError" and d["reason_type"] == "TimeoutError", d
        assert d["is_timeout"] is True, d
        assert "http_status" not in d and "ssl_verify_code" not in d, d
        print("4 timeout -> is_timeout=true ok")

        # 5 HTTP 401：保留状态码，不读 reason 短语/响应体/头
        exc = urllib.error.HTTPError(
            URL, 401, "Unauthorized", {"Authorization": "Bearer " + SECRET}, None)
        rc, res, errtext, rtext = fail_with(exc, "diag-401-1")
        d = check_common(rc, res, errtext, rtext)
        assert res["webhook_error"] == "HTTP 401", res
        assert d["type"] == "HTTPError" and d["http_status"] == 401, d
        assert d["is_timeout"] is False, d
        assert "reason_type" not in d, d
        assert "Unauthorized" not in errtext and "s3cret-token" not in errtext, errtext
        print("5 HTTP 401 -> http_status 401, no reason phrase/headers ok")

        # 6 配了代理且命中：只记 yes/yes，代理地址/端口/凭据不外泄
        set_proxies({"https": "http://user:hunter2@proxy.corp:3128"})
        exc = urllib.error.URLError(ConnectionRefusedError(61, "refused"))
        rc, res, errtext, rtext = fail_with(exc, "diag-proxy-hit-1")
        d = check_common(rc, res, errtext, rtext)
        assert d["proxy"] == {"configured": "yes", "matches": "yes"}, d
        for bad in ("proxy.corp", "3128", "hunter2"):
            assert bad not in errtext and bad not in rtext, (bad, errtext)
        print("6 proxy configured+matched -> yes/yes, no address/creds ok")

        # 7 代理被 bypass / 只有 ALL_PROXY：不猜
        set_proxies({"https": "http://proxy.corp:3128"}, bypass=True)
        exc = urllib.error.URLError(ConnectionRefusedError(61, "refused"))
        rc, res, errtext, rtext = fail_with(exc, "diag-proxy-bypass-1")
        d = check_common(rc, res, errtext, rtext)
        assert d["proxy"] == {"configured": "yes", "matches": "no"}, d
        set_proxies({"all": "http://proxy.corp:3128"})
        rc, res, errtext, rtext = fail_with(exc, "diag-proxy-all-1")
        d = check_common(rc, res, errtext, rtext)
        assert d["proxy"] == {"configured": "yes", "matches": "unknown"}, d
        print("7 proxy bypassed/all-only -> no/unknown ok")

        # 8 getproxies 探测失败：两值均 unknown
        set_proxies(boom=True)
        rc, res, errtext, rtext = fail_with(exc, "diag-proxy-unknown-1")
        d = check_common(rc, res, errtext, rtext)
        assert d["proxy"] == {"configured": "unknown", "matches": "unknown"}, d
        print("8 proxy probe fails -> unknown/unknown ok")
    finally:
        dispatch.urllib.request.urlopen = real_urlopen
        dispatch.urllib.request.getproxies = real_getproxies
        dispatch.urllib.request.proxy_bypass = real_bypass
        set_env(None, None)

    print("all webhook diagnostics tests passed")


if __name__ == "__main__":
    main()
