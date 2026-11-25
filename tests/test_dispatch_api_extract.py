#!/usr/bin/env python3
"""test_dispatch_api_extract.py — 抽取直连 API 后端测试（ADR-0005，mock 网络，不打真网）。

跑法：python3 tests/test_dispatch_api_extract.py
覆盖：
  1 后端解析：默认 api；pi；大小写/空白容忍；非法值 bad_extract_backend（rc=2）。
  2 启动预检：api 缺 env → api_not_configured（rc=2，只列变量名），零发送、无台账；
    max_tokens 非法 → bad_api_max_tokens（rc=2）。
  3 请求形状：URL=base+/chat/completions（尾斜杠容忍）、Bearer 头、system=extract.md、
    user=指令＋内联原文、temp 0、max_tokens 默认 32768/env 覆盖、无 stream/response_format、
    超时=--extract-timeout；api 路径不找 pi。
  4 成功契约：result/台账带 extract_backend=api，选段字段与 pi 路径同形状。
  5 错误映射：HTTPError→extraction_failed（HTTP <码>，不读响应体）；TimeoutError 与
    URLError(TimeoutError)→tool_timeout；URLError(它因)→extraction_failed（类名）；
    响应缺 content/非 JSON→extraction_invalid。密钥与 URL 不进 stdout/result/stderr。
  6 pi 备选回归：backend=pi 走 extract_via_pi（mock run_cmd），--pi-model 透传，
    result extract_backend=pi。
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "loop" / "scripts"))
import dispatch  # noqa: E402
from test_dispatch_reset import FakeApp  # noqa: E402

PROMPT = "请实现某个功能\n第二行说明"
ANCHOR = PROMPT.splitlines()[0][:24]
SCREEN = ANCHOR + "\n已完成该功能。"
EXT = {"needs_user": False, "questions": [],
       "conclusion": "", "blocked": "", "reply": SCREEN}
KEY = "sk-test-SECRET-VALUE"
BASE = "https://gw.example/v1"
MODEL = "test-model"
ENV_KEYS = ["LOOP_EXTRACT_BACKEND", "LOOP_API_BASE", "LOOP_API_KEY",
            "LOOP_API_MODEL", "LOOP_API_MAX_TOKENS"]


@contextlib.contextmanager
def env(**kw):
    old = {k: os.environ.pop(k, None) for k in ENV_KEYS}
    os.environ.update({k: v for k, v in kw.items() if v is not None})
    try:
        yield
    finally:
        for k in ENV_KEYS:
            os.environ.pop(k, None)
            if old[k] is not None:
                os.environ[k] = old[k]


def api_env(**kw):
    d = {"LOOP_EXTRACT_BACKEND": "api", "LOOP_API_BASE": BASE + "/",
         "LOOP_API_KEY": KEY, "LOOP_API_MODEL": MODEL}
    d.update(kw)
    return env(**d)


class FakeHc:
    def __init__(self):
        self.sends = []
        self.turn = 5

    def __call__(self, hc_bin, args, timeout_s):
        cmd = args[0]
        if cmd == "get":
            return {"state": "live", "turn": self.turn, "title": "claude-test",
                    "address": "t99", "name": "claude-test",
                    "labels": {"loop": "1"},
                    "status": {"state": "idle", "title": "claude-test"},
                    "foreground": {"name": "claude", "argv": ["claude"]}}
        if cmd == "send":
            self.sends.append((args[-1], "--if" in args))
            baseline = self.turn
            self.turn += 1
            return {"turn": baseline}
        if cmd == "read":
            return {"text": SCREEN, "complete": True, "how": "current", "why": ""}
        if cmd == "wait":
            return {"reason": "status", "status": "idle", "turn": self.turn}
        raise AssertionError("unexpected hc cmd: " + cmd)


class FakeResp:
    def __init__(self, payload):
        self._b = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def ok_payload():
    return {"choices": [{"message": {"content": json.dumps(EXT, ensure_ascii=False)}}]}


def run_main(td, fake, rid, extra_argv=(), which_pi=True):
    pf = Path(td) / "prompt.md"
    pf.write_text(PROMPT, encoding="utf-8")
    ld = Path(td) / ".loop"
    argv = ["--target", "t99", "--release-id", rid,
            "--prompt-file", str(pf), "--loop-dir", str(ld),
            "--space", "main",
            "--wall-clock", "5", "--check-in", "1",
            "--settle-seconds", "0.01", "--settle-checks", "2", *extra_argv]
    dispatch.hc_json = fake
    dispatch.app_json = FakeApp()
    dispatch.check_structure_and_tools = lambda: None
    dispatch.shutil.which = lambda name: ("/fake/" + name) if (name != "pi" or which_pi) else None
    dispatch.time.sleep = lambda s: None
    dispatch.webhook_proxy_resolution = lambda url: ({}, "none")
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = dispatch.main(argv)
    result = json.loads(out.getvalue().strip())
    ledger_path = ld / "runs" / (rid + ".json")
    return rc, result, ledger_path, out.getvalue(), err.getvalue()


def main():
    # 1 后端解析
    with env():
        assert dispatch.resolve_extract_backend() == "api"
    with env(LOOP_EXTRACT_BACKEND="pi"):
        assert dispatch.resolve_extract_backend() == "pi"
    with env(LOOP_EXTRACT_BACKEND=" API "):
        assert dispatch.resolve_extract_backend() == "api"
    with env(LOOP_EXTRACT_BACKEND="foo"):
        try:
            dispatch.resolve_extract_backend()
            raise AssertionError("should raise")
        except dispatch.DispatchError as e:
            assert e.kind == "bad_extract_backend" and e.returncode == 2
    print("1 backend resolution ok")

    # 2 启动预检：缺 env，零发送无台账
    with env(LOOP_EXTRACT_BACKEND="api"):
        with tempfile.TemporaryDirectory() as td:
            fake = FakeHc()
            rc, res, lp, out, err = run_main(td, fake, "api-pre-1")
            assert rc == 2 and res["error"]["kind"] == "api_not_configured", res
            for n in ("LOOP_API_BASE", "LOOP_API_KEY", "LOOP_API_MODEL"):
                assert n in res["error"]["message"]
            assert fake.sends == [] and not lp.exists()
    with api_env(LOOP_API_MAX_TOKENS="abc"):
        try:
            dispatch.api_config()
            raise AssertionError("should raise")
        except dispatch.DispatchError as e:
            assert e.kind == "bad_api_max_tokens" and e.returncode == 2
    with api_env(LOOP_API_MAX_TOKENS="0"):
        try:
            dispatch.api_config()
            raise AssertionError("should raise")
        except dispatch.DispatchError as e:
            assert e.kind == "bad_api_max_tokens"
    print("2 startup preflight ok")

    # 3+4 请求形状与成功契约（api 路径不找 pi）
    captured = {}

    def fake_open(req, timeout, proxies):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.header_items())
        captured["body"] = json.loads(req.data.decode("utf-8"))
        captured["timeout"] = timeout
        captured["proxies"] = proxies
        return FakeResp(ok_payload())

    dispatch.webhook_open = fake_open
    with api_env():
        with tempfile.TemporaryDirectory() as td:
            fake = FakeHc()
            rc, res, lp, out, err = run_main(td, fake, "api-ok-1", which_pi=False)
            assert rc == 0 and res["ok"], res
            assert res["extract_backend"] == "api"
            led = json.loads(lp.read_text(encoding="utf-8"))
            assert led["params"]["extract_backend"] == "api"
            assert captured["url"] == BASE + "/chat/completions"
            assert captured["headers"].get("Authorization") == "Bearer " + KEY
            b = captured["body"]
            assert b["model"] == MODEL and b["temperature"] == 0
            assert b["max_tokens"] == 32768
            assert "stream" not in b and "response_format" not in b
            assert b["messages"][0]["role"] == "system"
            assert b["messages"][0]["content"] == (
                Path(dispatch.__file__).resolve().parent.parent / "prompts" / "extract.md"
            ).read_text(encoding="utf-8")
            assert b["messages"][1]["role"] == "user"
            assert SCREEN in b["messages"][1]["content"]
            assert captured["timeout"] == 1200.0
            assert res["needs_user"] is False and res["questions"] == []
            assert fake.sends == [(PROMPT, True)]
    with api_env(LOOP_API_MAX_TOKENS="8192"):
        with tempfile.TemporaryDirectory() as td:
            rc, res, _, _, _ = run_main(td, FakeHc(), "api-ok-2")
            assert rc == 0 and captured["body"]["max_tokens"] == 8192
    print("3+4 request shape and success contract ok")

    # 5 错误映射＋零回显
    def raising_open(exc):
        def f(req, timeout, proxies):
            raise exc
        return f

    cases = [
        (urllib.error.HTTPError(BASE + "/chat/completions", 429, "rate", {}, None),
         1, "extraction_failed", "API HTTP 429"),
        (TimeoutError(), 1, "tool_timeout", None),
        (urllib.error.URLError(TimeoutError()), 1, "tool_timeout", None),
        (urllib.error.URLError(ConnectionRefusedError()), 1, "extraction_failed", "API URLError"),
    ]
    for i, (exc, rc_want, kind, msg) in enumerate(cases):
        dispatch.webhook_open = raising_open(exc)
        with api_env():
            with tempfile.TemporaryDirectory() as td:
                rc, res, _, out, err = run_main(td, FakeHc(), f"api-err-{i}")
                assert rc == rc_want and res["error"]["kind"] == kind, (kind, res)
                if msg:
                    assert msg in res["error"]["message"]
                for s in (out, err, json.dumps(res, ensure_ascii=False)):
                    assert KEY not in s and "gw.example" not in s, "密钥/URL 回显"
    dispatch.webhook_open = lambda req, timeout, proxies: FakeResp({})
    with api_env():
        with tempfile.TemporaryDirectory() as td:
            rc, res, _, _, _ = run_main(td, FakeHc(), "api-bad-1")
            assert rc == 1 and res["error"]["kind"] == "extraction_invalid", res
    dispatch.webhook_open = lambda req, timeout, proxies: FakeResp(
        {"choices": [{"message": {"content": "不是 JSON"}}]})
    with api_env():
        with tempfile.TemporaryDirectory() as td:
            rc, res, _, _, _ = run_main(td, FakeHc(), "api-bad-2")
            assert rc == 1 and res["error"]["kind"] == "extraction_invalid", res
    print("5 error mapping and zero-echo ok")

    # 6 pi 备选回归
    pi_argv = {}

    def fake_run_cmd(argv, timeout):
        pi_argv["argv"] = argv
        return subprocess.CompletedProcess(argv, 0, json.dumps(EXT, ensure_ascii=False), "")

    dispatch.run_cmd = fake_run_cmd
    with env(LOOP_EXTRACT_BACKEND="pi"):
        with tempfile.TemporaryDirectory() as td:
            rc, res, lp, _, _ = run_main(td, FakeHc(), "pi-reg-1",
                                         extra_argv=("--pi-model", "m1"))
            assert rc == 0 and res["ok"] and res["extract_backend"] == "pi", res
            assert "--model" in pi_argv["argv"] and "m1" in pi_argv["argv"]
            assert "--no-tools" in pi_argv["argv"]
            led = json.loads(lp.read_text(encoding="utf-8"))
            assert led["params"]["extract_backend"] == "pi"
    print("6 pi fallback regression ok")

    print("ALL OK")


if __name__ == "__main__":
    main()