#!/usr/bin/env python3
"""test_dispatch_webhook_diag.py — webhook 失败诊断与出网代理选择测试
（FakeHerdr 假世界，不打真实端点）。

跑法：python3 tests/test_dispatch_webhook_diag.py
缝：唯一 harness.run（CLI 契约，T5c/ADR-0021）。覆盖：
  1 连接被拒（URLError/ConnectionRefusedError）：type/reason_type/errno/errno_name。
  2 DNS 失败（URLError/gaierror）：errno_name 给 EAI_*，不给 POSIX 名字。
  3 SSL 证书错误（URLError/SSLCertVerificationError）：ssl_verify_code，且不把
    OpenSSL 错误码误记成系统 errno。
  4 超时（URLError/TimeoutError）：is_timeout=true。
  5 HTTP 401：http_status=401，webhook_error="HTTP 401"。
  6 默认跟随系统代理：env 只有 no_proxy/非标准键时不劫持，改用系统代理（7890），
    且代理地址不外泄。
  7 标准 http_proxy/https_proxy 显式配置时优先于系统代理。
  8 系统例外表命中 → 直连（source=bypass，configured=yes/matches=no）。
  9 系统代理探测失败 → 两值 unknown（不猜）。
每个端到端场景都断言：整轮仍 ok=true、webhook=failed、stdout/stderr/结果文件三处
诊断一致，且都不含 URL、密钥、Authorization 头或异常 message 里的哨兵串。

T5c（ADR-0021，#99）：1–5 经 harness 注入 proxy_resolution 密闭（宿主可能导出
HTTPS_PROXY）；6–8 的被测对象是真实 webhook_proxy_resolution 的代理选择本身，
完整轮保真实决议、网络探测改对宿主模块 urllib.request 的作用域 mock.patch.object
（macOS 专有属性 create=True 让 mock 负责增删恢复）；9 是 _proxy_diagnostic 单元
探针，保留直调、同一替换手法（ADR-0016 决定 7 残留洞纪律）。
R（#104）：共享常量与 world 构造器迁 helpers 单一出处。
"""
import contextlib
import errno
import json
import socket
import ssl
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

import harness
from harness import dispatch
from helpers import PROMPT, EXT, world, extraction, notify_fields

URL = "https://example.invalid/hook"
SECRET = "s3cret-token"
SENTINELS = ("example.invalid", SECRET, "Bearer", "Authorization",
             "reason-with-host-detail", "sysproxy.corp", "envproxy.corp",
             "hunter2", "3128")


def assert_clean(*texts):
    for text in texts:
        for bad in SENTINELS:
            assert bad not in text, (bad, text)


@contextlib.contextmanager
def net_probes(env_proxies=None, system_proxies=None, bypass=False, boom=False):
    """作用域替换宿主模块 urllib.request 的网络探测：环境变量代理、系统代理、
    系统例外表。macOS 专有属性 create=True（其它平台不存在，mock 负责增删恢复）；
    退出无条件恢复（ADR-0016 决定 7 残留洞纪律）。"""
    def fake_system():
        if boom:
            raise OSError("proxy probe failed")
        return dict(system_proxies or {})

    with mock.patch.object(urllib.request, "getproxies_environment",
                           lambda: dict(env_proxies or {})), \
         mock.patch.object(urllib.request, "getproxies_macosx_sysconf",
                           fake_system, create=True), \
         mock.patch.object(urllib.request, "proxy_bypass_macosx_sysconf",
                           lambda host: bypass, create=True), \
         mock.patch.object(urllib.request, "proxy_bypass_environment",
                           lambda host: False):
        yield


def run_case(rid, opener, proxy_res=None):
    """经唯一 harness.run 跑一轮完整 dispatch，读结果文件与通知文件（ADR-0014 双产物）；
    返回 (rc, result, stderr, 结果文件文本, 通知文件字节)。
    proxy_res 给定时经 harness 作用域注入；不给定保真实 webhook_proxy_resolution
    （6–8 代理选择本身是被测对象，探测经 net_probes 作用域替换）。"""
    with tempfile.TemporaryDirectory() as td:
        pf = Path(td) / "prompt.md"
        pf.write_text(PROMPT, encoding="utf-8")
        ld = Path(td) / ".loop"
        argv = ["--target", "t99", "--release-id", rid,
                "--prompt-file", str(pf), "--loop-dir", str(ld),
                "--space", "main",
                "--wall-clock", "5", "--check-in", "1",
                "--settle-seconds", "0.01", "--settle-checks", "2"]
        r = harness.run(argv, world=world(),
                        env={"LOOP_WEBHOOK_URL": URL, "LOOP_WEBHOOK_SECRET": SECRET},
                        extract=lambda deps, request, turn_path, injection: extraction(EXT),
                        http_open=opener, proxy_resolution=proxy_res)
        result_text = r.result_path.read_text(encoding="utf-8")
        notify_bytes = r.notify_path.read_bytes()
        return r.rc, r.result, r.stderr, result_text, notify_bytes


def fail_with(exc, rid):
    """让 webhook 出口抛 exc；返回整轮 (rc, result, stderr, 结果文件文本, 通知字节)。
    基础场景注入无代理决议，保证诊断断言不受宿主代理环境影响。"""
    def fake_open(req, timeout, proxies):
        raise exc
    return run_case(rid, fake_open, proxy_res=lambda url: ({}, "none"))


def check_common(rc, res, errtext, result_text, notify_bytes):
    assert rc == 0 and res["ok"] is True, res          # 通知失败不连累整轮
    assert res["webhook"] == "failed", res
    assert result_text.strip() == json.dumps(res, ensure_ascii=False), "result 文件须与 stdout 逐字一致"
    detail = res["webhook_error_detail"]
    marker = json.dumps(detail, ensure_ascii=False, sort_keys=True)
    assert marker in errtext, (marker, errtext)        # stderr 与结果文件同一份诊断
    assert_clean(json.dumps(res, ensure_ascii=False), errtext, result_text)
    # 双产物（ADR-0014，ADR-0026 瘦身）：发送诊断只归全量结果；通知投影恒带 7＋有内容才带、不含 webhook 诊断
    notify = json.loads(notify_bytes.decode("utf-8"))
    assert set(notify) == notify_fields(res), notify
    for k in ("webhook", "webhook_error", "webhook_error_detail"):
        assert k not in notify, notify
    assert_clean(notify_bytes.decode("utf-8"))
    return detail


def main():
    no_proxy = {"configured": "no", "matches": "no", "source": "none"}

    # 1 连接被拒：reason 是 ConnectionRefusedError，message 里塞哨兵也不得外泄
    exc = urllib.error.URLError(
        ConnectionRefusedError(errno.ECONNREFUSED, "refused while posting to " + URL))
    rc, res, errtext, rtext, nbytes = fail_with(exc, "diag-refused-1")
    d = check_common(rc, res, errtext, rtext, nbytes)
    assert res["webhook_error"] == "URLError", res
    assert d["type"] == "URLError" and d["reason_type"] == "ConnectionRefusedError", d
    assert d["errno"] == errno.ECONNREFUSED and d["errno_name"] == "ECONNREFUSED", d
    assert d["is_timeout"] is False, d
    assert d["proxy"] == no_proxy, d
    assert "http_status" not in d and "ssl_verify_code" not in d, d
    print("1 connection refused -> errno ECONNREFUSED ok")

    # 2 DNS：gaierror 的 errno 是 EAI_*，不能按 POSIX errno 命名
    exc = urllib.error.URLError(
        socket.gaierror(socket.EAI_NONAME, "nodename for " + URL + " not known"))
    rc, res, errtext, rtext, nbytes = fail_with(exc, "diag-dns-1")
    d = check_common(rc, res, errtext, rtext, nbytes)
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
    rc, res, errtext, rtext, nbytes = fail_with(exc, "diag-ssl-1")
    d = check_common(rc, res, errtext, rtext, nbytes)
    assert d["reason_type"] == "SSLCertVerificationError", d
    assert d["ssl_verify_code"] == 18, d
    assert "errno" not in d and "errno_name" not in d, d
    assert "verify_message" not in d and "self-signed" not in json.dumps(d), d
    print("3 SSL cert error -> ssl_verify_code 18, no fake errno ok")

    # 4 超时：外层 URLError 包 TimeoutError
    exc = urllib.error.URLError(TimeoutError("handshake timed out for " + URL))
    rc, res, errtext, rtext, nbytes = fail_with(exc, "diag-timeout-1")
    d = check_common(rc, res, errtext, rtext, nbytes)
    assert d["type"] == "URLError" and d["reason_type"] == "TimeoutError", d
    assert d["is_timeout"] is True, d
    assert "http_status" not in d and "ssl_verify_code" not in d, d
    print("4 timeout -> is_timeout=true ok")

    # 5 HTTP 401：保留状态码，不读 reason 短语/响应体/头
    exc = urllib.error.HTTPError(
        URL, 401, "Unauthorized", {"Authorization": "Bearer " + SECRET}, None)
    rc, res, errtext, rtext, nbytes = fail_with(exc, "diag-401-1")
    d = check_common(rc, res, errtext, rtext, nbytes)
    assert res["webhook_error"] == "HTTP 401", res
    assert d["type"] == "HTTPError" and d["http_status"] == 401, d
    assert d["is_timeout"] is False, d
    assert "reason_type" not in d, d
    assert "Unauthorized" not in errtext and "s3cret-token" not in errtext, errtext
    print("5 HTTP 401 -> http_status 401, no reason phrase/headers ok")

    # 6 默认跟随系统代理：env 只有 no_proxy＋非标准键（正是本机现场），
    #   不能用 getproxies() 的非空结果挡住系统代理；代理地址/端口不得外泄。
    seen = []

    def system_proxy_open(req, timeout, proxies):
        seen.append(proxies)
        raise urllib.error.URLError(ConnectionRefusedError(errno.ECONNREFUSED, "refused"))

    with net_probes({"no": "localhost,127.0.0.1",
                     "jev_upstream": "http://127.0.0.1:7890"},
                    {"https": "http://user:hunter2@sysproxy.corp:3128"}):
        rc, res, errtext, rtext, nbytes = run_case("diag-proxy-sys-1", system_proxy_open)
    d = check_common(rc, res, errtext, rtext, nbytes)
    assert d["proxy"] == {"configured": "yes", "matches": "yes", "source": "system"}, d
    assert d["attempts"] == 4 and d["fallback_direct"] is True and d["fallback_error"] == "URLError", d
    sys_proxy = {"https": "http://user:hunter2@sysproxy.corp:3128"}
    assert seen == [sys_proxy, sys_proxy, sys_proxy, {}], seen
    assert "sysproxy.corp" not in errtext and "hunter2" not in rtext, errtext
    print("6 default system proxy (env pollution ignored) -> system yes/yes + 3+1 attempts ok")

    # 7 标准 https_proxy 显式配置时优先于系统代理
    seen.clear()
    with net_probes({"https": "http://envproxy.corp:8080"},
                    {"https": "http://sysproxy.corp:3128"}):
        rc, res, errtext, rtext, nbytes = run_case("diag-proxy-env-1", system_proxy_open)
    d = check_common(rc, res, errtext, rtext, nbytes)
    assert d["proxy"] == {"configured": "yes", "matches": "yes", "source": "env"}, d
    assert d["attempts"] == 4 and d["fallback_direct"] is True and d["fallback_error"] == "URLError", d
    env_proxy = {"https": "http://envproxy.corp:8080"}
    assert seen == [env_proxy, env_proxy, env_proxy, {}], seen
    assert "envproxy.corp" not in errtext and "sysproxy.corp" not in rtext, errtext
    print("7 explicit https_proxy wins over system -> env + 3+1 attempts ok")

    # 8 系统例外表命中：有代理配置，但本请求直连（无直连兜底，只重试 3 次）
    seen.clear()
    with net_probes({}, {"https": "http://sysproxy.corp:3128"}, bypass=True):
        rc, res, errtext, rtext, nbytes = run_case("diag-proxy-bypass-1", system_proxy_open)
    d = check_common(rc, res, errtext, rtext, nbytes)
    assert d["proxy"] == {"configured": "yes", "matches": "no", "source": "bypass"}, d
    assert d["attempts"] == 3 and "fallback_direct" not in d, d
    assert seen == [{}, {}, {}], seen
    print("8 system exception table -> bypass, direct (3 attempts) ok")

    # 9 系统代理探测失败：不猜，两值 unknown（单元探针：直调 _proxy_diagnostic）
    with net_probes(boom=True):
        diag = dispatch._proxy_diagnostic(URL)
    assert diag == {"configured": "unknown", "matches": "unknown",
                    "source": "unknown"}, diag
    print("9 proxy probe fails -> unknown ok")

    print("all webhook diagnostics tests passed")


if __name__ == "__main__":
    main()
