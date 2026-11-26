#!/usr/bin/env python3
"""test_harness.py — 唯一 harness 自身的测试（T3，ADR-0016 决定 9）。

跑法：python3 tests/test_harness.py
只测 harness 的机械职责：装配依赖对象、调用分发脚本入口、把返回码与产物
整理成可断言的东西；全局副作用作用域内替换并恢复；时钟虚拟不真睡。
不解释业务含义（不断言业务分支）。
P2 收口后 harness 不再赋值任何 dispatch 模块全局：命令层、时钟、locate、
http_open/run_cmd、proxy_resolution 全经 Deps 注入；P3/⑤ 起学习环境也走
deps.env，harness 不再作用域改写 os.environ。
"""
import json
import os
import tempfile
import time
from pathlib import Path

import harness
from harness import dispatch
from fake_herdr import FakeHerdr
from helpers import PROMPT, ANCHOR, EXT, extraction


def world_with_terminal():
    w = FakeHerdr()
    w.add_terminal(address="t99", name="claude-test", labels={"loop": "1"})
    sp = w.add_space(name="main")
    w.add_tab(sp, terminals=["t99"])   # pane 已在：领养提前返回
    w.screen("t99", ANCHOR + "\n已完成该功能。")
    return w


def stub_ext(deps, request, turn_path, injection):
    return extraction(EXT)


def base_argv(td, rid, extra=()):
    pf = Path(td) / "prompt.md"
    pf.write_text(PROMPT, encoding="utf-8")
    return ["--target", "t99", "--release-id", rid,
            "--prompt-file", str(pf), "--loop-dir", str(Path(td) / ".loop"),
            "--space", "main",
            "--wall-clock", "5", "--check-in", "1",
            "--settle-seconds", "0.01", "--settle-checks", "2", *extra]


def main():
    # 1 跑一轮：返回码与产物（stdout JSON、台账、result 文件）整理齐全
    w = world_with_terminal()
    with tempfile.TemporaryDirectory() as td:
        r = harness.run(base_argv(td, "harness-1"), world=w, extract=stub_ext)
        assert r.rc == 0, r.result          # 装配正确：一轮跑通（不断言业务分支）
        assert json.loads(r.stdout.strip()) == r.result
        assert isinstance(r.ledger, dict)   # 台账产物被整理出来
        assert r.result_path.read_text(encoding="utf-8").strip() == r.stdout.strip()
        assert r.ledger_path.exists()
    print("1 run collects rc, stdout result, ledger, result file")

    # 2 全局副作用归零：harness 不再替换模块全局，跑后原样；os.environ 也恢复。
    before_env = dict(os.environ)
    saved = (dispatch.hc_json, dispatch.app_json, dispatch.find_tool,
             dispatch.webhook_open, dispatch.run_cmd,
             dispatch.webhook_proxy_resolution)
    w = world_with_terminal()
    with tempfile.TemporaryDirectory() as td:
        harness.run(base_argv(td, "harness-2"), world=w, extract=stub_ext)
    assert (dispatch.hc_json, dispatch.app_json, dispatch.find_tool,
            dispatch.webhook_open, dispatch.run_cmd,
            dispatch.webhook_proxy_resolution) == saved
    assert dict(os.environ) == before_env
    print("2 module globals untouched, os.environ restored")

    # 3 虚拟时钟：settle 静置只推进虚拟时间，不真睡
    w = world_with_terminal()
    clock = harness.VirtualClock()
    with tempfile.TemporaryDirectory() as td:
        argv = base_argv(td, "harness-3",
                         extra=["--settle-seconds", "5"])
        t0 = time.monotonic()
        r = harness.run(argv, world=w, clock=clock, extract=stub_ext)
        elapsed = time.monotonic() - t0
        assert r.rc == 0, r.result
        assert clock.t >= 10, clock.t     # 2 次静置 × 5 秒全记在虚拟时钟上
        assert elapsed < 2, elapsed       # 真实时间没睡
    print("3 virtual clock advances without real sleeping")

    # 4 前置路径全部经 Deps（不再有模块级 hc_json/time 残留）：--reset 跑通即证。
    w = world_with_terminal()
    with tempfile.TemporaryDirectory() as td:
        r = harness.run(base_argv(td, "harness-4", extra=["--reset", "clear"]),
                        world=w, extract=stub_ext)
        assert r.rc == 0, r.result
    print("4 reset prestep runs entirely through Deps injection")

    # 5 env 注入到达分发脚本（Deps.env）：backend=api 缺配置必在启动预检停轮。
    w = world_with_terminal()
    with tempfile.TemporaryDirectory() as td:
        r = harness.run(base_argv(td, "harness-5"), world=w,
                        env={"LOOP_EXTRACT_BACKEND": "api"},   # 缺 API 配置
                        extract=stub_ext)
        assert r.rc == 2, r.result
    assert dict(os.environ) == before_env
    print("5 env mapping reaches dispatch via Deps.env, os.environ restored")

    # 6 学习 env 经 Deps.env 到达 learning（⑤ 收口后不再作用域改写 os.environ）：
    #   LOOP_LEARNING_DIR 指到临时目录，一轮开启学习后该目录出现学习落盘即证 env 到位。
    before_env = dict(os.environ)
    w = world_with_terminal()
    with tempfile.TemporaryDirectory() as td:
        ldir = Path(td) / "learn"
        r = harness.run(base_argv(td, "harness-6"), world=w,
                        env={"LOOP_LEARNING": "on", "LOOP_LEARNING_DIR": str(ldir)},
                        extract=stub_ext)
        assert r.rc == 0, r.result
        assert ldir.is_dir() and (ldir / "pending.json").is_file(), \
            "学习存储落盘在 env 指定的目录"
    assert dict(os.environ) == before_env
    print("6 learning env reaches learning_dir via Deps.env, os.environ untouched")

    # 7 http_open/run_cmd 经 Deps 注入（不再替换模块全局）；探针在抽取里各调一次。
    saved_open, saved_cmd = dispatch.webhook_open, dispatch.run_cmd
    calls = []
    marker_open = lambda req, timeout, proxies: calls.append(("open", req, proxies))
    marker_cmd = lambda argv, timeout_s: calls.append(("cmd", list(argv), timeout_s))

    def probe_extract2(deps, request, turn_path, injection):
        deps.run_cmd(["probe-cmd"], 7)
        deps.http_open("probe-req", 5, {"https": "p"})
        return extraction(EXT)

    w = world_with_terminal()
    with tempfile.TemporaryDirectory() as td:
        r = harness.run(base_argv(td, "harness-7"), world=w,
                        http_open=marker_open, run_cmd=marker_cmd,
                        extract=probe_extract2)
        assert r.rc == 0, r.result
    assert ("cmd", ["probe-cmd"], 7) in calls, calls
    assert ("open", "probe-req", {"https": "p"}) in calls, calls
    assert dispatch.webhook_open is saved_open and dispatch.run_cmd is saved_cmd
    print("7 injected http_open/run_cmd reach Deps surface, module globals untouched")

    # 9 proxy_resolution 经 Deps 注入（不再替换模块全局）；探针在抽取里调一次。
    saved_proxy = dispatch.webhook_proxy_resolution
    marker_proxy = lambda url: ({"https": "http://proxy.invalid:1"}, "env")
    proxy_seen = []

    def probe_extract4(deps, request, turn_path, injection):
        proxy_seen.append(deps.proxy_resolution("https://example.invalid/x"))
        return extraction(EXT)

    w = world_with_terminal()
    with tempfile.TemporaryDirectory() as td:
        r = harness.run(base_argv(td, "harness-9"), world=w,
                        proxy_resolution=marker_proxy, extract=probe_extract4)
        assert r.rc == 0, r.result
    assert proxy_seen == [({"https": "http://proxy.invalid:1"}, "env")], proxy_seen
    assert dispatch.webhook_proxy_resolution is saved_proxy
    print("9 injected proxy_resolution reaches Deps surface, module global untouched")

    # 9c 异常退出也无条件恢复：轮内抛异常（extract 探针炸）时模块全局与 os.environ
    #   仍恢复原样（harness 已不写 os.environ，这里顺带守住），异常原样外抛。
    before_env = dict(os.environ)
    saved_all = (dispatch.hc_json, dispatch.app_json, dispatch.find_tool,
                 dispatch.webhook_open, dispatch.run_cmd,
                 dispatch.webhook_proxy_resolution)

    def boom_extract(deps, request, turn_path, injection):
        raise RuntimeError("probe boom")

    w = world_with_terminal()
    with tempfile.TemporaryDirectory() as td:
        try:
            harness.run(base_argv(td, "harness-9c"), world=w,
                        http_open=marker_open, run_cmd=marker_cmd,
                        proxy_resolution=marker_proxy, extract=boom_extract)
            raise AssertionError("异常须原样外抛")
        except RuntimeError as e:
            assert str(e) == "probe boom", e
    assert (dispatch.hc_json, dispatch.app_json, dispatch.find_tool,
            dispatch.webhook_open, dispatch.run_cmd,
            dispatch.webhook_proxy_resolution) == saved_all
    assert dict(os.environ) == before_env
    print("9c module globals and os.environ restored on exception exit")

    print("all harness tests passed")


if __name__ == "__main__":
    main()
