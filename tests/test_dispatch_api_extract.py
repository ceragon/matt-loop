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

T4（ADR-0021）：本文件业务用例已迁 FakeHerdr＋harness.run（唯一 CLI 缝）；后端专项用例把
真实抽取入口 dispatch.extract 作为 extract 依赖传入，经 harness 残留洞吸收（env 应用到
os.environ、注入 http_open/run_cmd 替换模块级）密闭跑真实抽取链。下方 FakeHc、FakeResp、
env、api_env 与 PROMPT/SCREEN/EXT/BASE/KEY/MODEL 是**残留兼容面**，供未迁消费方
（next_skill/learning/turn_cut）跨文件 import，原地保留、随 R 删除；棘轮白名单在册。
无人引用的旧跑用例助手 run_main/ok_payload 已在本片删除。
"""
import contextlib
import json
import os
import subprocess
import sys
import tempfile
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "loop" / "scripts"))
import dispatch  # noqa: E402
import harness  # noqa: E402
from fake_herdr import FakeHerdr  # noqa: E402

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


# ---- 残留兼容面（随 R 删）：env/api_env 环境写入助手与旧桩，供未迁消费方跨文件 import ----
# 本文件 main() 已迁 harness（环境经注入 dict / harness env，不再写 os.environ）；保留仅为
# 消费方（next_skill/learning/turn_cut import env/api_env/FakeHc/FakeResp/常量）。棘轮白名单在册。
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


def _world():
    """既有 idle agent t99＋main 空间 pane 已在＋稳定屏幕（回读逐字校验语料）。"""
    w = FakeHerdr()
    w.add_terminal(address="t99", name="claude-test", labels={"loop": "1"})
    sp = w.add_space(name="main")
    w.add_tab(sp, terminals=["t99"])
    w.screen("t99", SCREEN)
    return w


def _argv(td, rid, extra=()):
    pf = Path(td) / "prompt.md"
    pf.write_text(PROMPT, encoding="utf-8")
    return ["--target", "t99", "--release-id", rid,
            "--prompt-file", str(pf), "--loop-dir", str(Path(td) / ".loop"),
            "--space", "main", "--wall-clock", "5", "--check-in", "1",
            "--settle-seconds", "0.01", "--settle-checks", "2", *extra]


def _api_env(**kw):
    # LOOP_API_MAX_TOKENS 钉 ""（api_config 视空串为默认 32768）：harness 作用域内写入
    # 覆盖宿主可能导出的同名键，保住「默认 max_tokens」断言的隔离（退出恢复）。
    d = {"LOOP_EXTRACT_BACKEND": "api", "LOOP_API_BASE": BASE + "/",
         "LOOP_API_KEY": KEY, "LOOP_API_MODEL": MODEL, "LOOP_API_MAX_TOKENS": ""}
    d.update(kw)
    return d


def _ok_payload():
    return {"choices": [{"message": {"content": json.dumps(EXT, ensure_ascii=False)}}]}


def main():
    # 1 后端解析（纯函数直调，注入显式 env dict，零全局副作用）
    assert dispatch.resolve_extract_backend({}) == "api"
    assert dispatch.resolve_extract_backend({"LOOP_EXTRACT_BACKEND": "pi"}) == "pi"
    assert dispatch.resolve_extract_backend({"LOOP_EXTRACT_BACKEND": " API "}) == "api"
    try:
        dispatch.resolve_extract_backend({"LOOP_EXTRACT_BACKEND": "foo"})
        raise AssertionError("should raise")
    except dispatch.DispatchError as e:
        assert e.kind == "bad_extract_backend" and e.returncode == 2
    print("1 backend resolution ok")

    # 2 启动预检：缺 env，零发送无台账（经 harness env 注入）
    w = _world()
    with tempfile.TemporaryDirectory() as td:
        r = harness.run(_argv(td, "api-pre-1"), world=w,
                        env={"LOOP_EXTRACT_BACKEND": "api"})
        assert r.rc == 2 and r.result["error"]["kind"] == "api_not_configured", r.result
        for n in ("LOOP_API_BASE", "LOOP_API_KEY", "LOOP_API_MODEL"):
            assert n in r.result["error"]["message"]
        assert w.sends == [] and r.ledger is None
    for bad in ("abc", "0"):
        try:
            dispatch.api_config(_api_env(LOOP_API_MAX_TOKENS=bad))
            raise AssertionError("should raise")
        except dispatch.DispatchError as e:
            assert e.kind == "bad_api_max_tokens" and e.returncode == 2
    print("2 startup preflight ok")

    # 3+4 请求形状与成功契约（真实抽取链经 harness 残留洞吸收密闭跑；api 路径不找 pi）
    captured = {}

    def fake_open(req, timeout, proxies):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.header_items())
        captured["body"] = json.loads(req.data.decode("utf-8"))
        captured["timeout"] = timeout
        # proxies 原样收下但不自证其值：代理决议读真实机器环境（机器相关），归 webhook 套件（T5）。
        return FakeResp(_ok_payload())

    w = _world()
    with tempfile.TemporaryDirectory() as td:
        # api 路径不找 pi 的缝上重述：本轮经网络出口（http_open）跑通、未注入 run_cmd，
        # 若走 pi 子进程会撞真实 run_cmd 而失败——成功＋captured 请求即证 api 直连。
        r = harness.run(_argv(td, "api-ok-1"), world=w, env=_api_env(),
                        extract=dispatch.extract, http_open=fake_open)
        assert r.rc == 0 and r.result["ok"], r.result
        assert r.result["extract_backend"] == "api"
        assert r.ledger["params"]["extract_backend"] == "api"
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
        assert r.result["needs_user"] is False and r.result["questions"] == []
        assert w.sends == [("t99", PROMPT, True)]
    w = _world()
    with tempfile.TemporaryDirectory() as td:
        r = harness.run(_argv(td, "api-ok-2"), world=w,
                        env=_api_env(LOOP_API_MAX_TOKENS="8192"),
                        extract=dispatch.extract, http_open=fake_open)
        assert r.rc == 0 and captured["body"]["max_tokens"] == 8192
    print("3+4 request shape and success contract ok")

    # 5 错误映射＋零回显（注入 http_open 抛错，真实抽取链映射）
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
        w = _world()
        with tempfile.TemporaryDirectory() as td:
            r = harness.run(_argv(td, f"api-err-{i}"), world=w, env=_api_env(),
                            extract=dispatch.extract, http_open=raising_open(exc))
            assert r.rc == rc_want and r.result["error"]["kind"] == kind, (kind, r.result)
            if msg:
                assert msg in r.result["error"]["message"]
            for s in (r.stdout, r.stderr, json.dumps(r.result, ensure_ascii=False)):
                assert KEY not in s and "gw.example" not in s, "密钥/URL 回显"
    for payload in ({}, {"choices": [{"message": {"content": "不是 JSON"}}]}):
        w = _world()
        with tempfile.TemporaryDirectory() as td:
            r = harness.run(_argv(td, "api-bad"), world=w, env=_api_env(),
                            extract=dispatch.extract,
                            http_open=lambda req, timeout, proxies: FakeResp(payload))
            assert r.rc == 1 and r.result["error"]["kind"] == "extraction_invalid", r.result
    print("5 error mapping and zero-echo ok")

    # 6 pi 备选回归（注入 run_cmd，真实 extract_via_pi 链）
    pi_argv = {}

    def fake_run_cmd(argv, timeout):
        pi_argv["argv"] = argv
        return subprocess.CompletedProcess(argv, 0, json.dumps(EXT, ensure_ascii=False), "")

    w = _world()
    with tempfile.TemporaryDirectory() as td:
        r = harness.run(_argv(td, "pi-reg-1", extra=("--pi-model", "m1")), world=w,
                        env={"LOOP_EXTRACT_BACKEND": "pi"},
                        extract=dispatch.extract, run_cmd=fake_run_cmd)
        assert r.rc == 0 and r.result["ok"] and r.result["extract_backend"] == "pi", r.result
        assert "--model" in pi_argv["argv"] and "m1" in pi_argv["argv"]
        assert "--no-tools" in pi_argv["argv"]
        assert r.ledger["params"]["extract_backend"] == "pi"
    print("6 pi fallback regression ok")

    print("ALL OK")


if __name__ == "__main__":
    main()