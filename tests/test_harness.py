#!/usr/bin/env python3
"""test_harness.py — 唯一 harness 自身的测试（T3，ADR-0016 决定 9）。

跑法：python3 tests/test_harness.py
只测 harness 的机械职责：装配依赖对象、调用分发脚本入口、把返回码与产物
整理成可断言的东西；全局副作用作用域内替换并恢复；时钟虚拟不真睡。
不解释业务含义（不断言业务分支）。
"""
import json
import os
import tempfile
import time
from pathlib import Path

import harness
from harness import dispatch
from fake_herdr import FakeHerdr

PROMPT = "请实现某个功能\n第二行说明"
ANCHOR = PROMPT.splitlines()[0][:24]
EXT = {"needs_user": False, "questions": [], "conclusion": "", "blocked": "",
       "reply": ANCHOR + "\n已完成该功能。", "next_skill": ""}


def world_with_terminal():
    w = FakeHerdr()
    w.add_terminal(address="t99", name="claude-test", labels={"loop": "1"})
    sp = w.add_space(name="main")
    w.add_tab(sp, terminals=["t99"])   # pane 已在：领养提前返回
    w.screen("t99", ANCHOR + "\n已完成该功能。")
    return w


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
        r = harness.run(base_argv(td, "harness-1"), world=w,
                        extract=lambda pi_bin, args, raw: dict(EXT))
        assert r.rc == 0, r.result          # 装配正确：一轮跑通（不断言业务分支）
        assert json.loads(r.stdout.strip()) == r.result
        assert isinstance(r.ledger, dict)   # 台账产物被整理出来
        assert r.result_path.read_text(encoding="utf-8").strip() == r.stdout.strip()
        assert r.ledger_path.exists()
    print("1 run collects rc, stdout result, ledger, result file")

    # 2 全局副作用归零：模块全局与 os.environ 跑后原样
    before_env = dict(os.environ)
    saved = (dispatch.hc_json, dispatch.app_json, dispatch.time, dispatch.find_tool)
    w = world_with_terminal()
    with tempfile.TemporaryDirectory() as td:
        harness.run(base_argv(td, "harness-2"), world=w,
                    extract=lambda pi_bin, args, raw: dict(EXT))
    assert (dispatch.hc_json, dispatch.app_json, dispatch.time, dispatch.find_tool) == saved
    assert dict(os.environ) == before_env
    print("2 module globals and os.environ restored")

    # 3 虚拟时钟：settle 静置只推进虚拟时间，不真睡
    w = world_with_terminal()
    clock = harness.VirtualClock()
    with tempfile.TemporaryDirectory() as td:
        argv = base_argv(td, "harness-3",
                         extra=["--settle-seconds", "5"])
        t0 = time.monotonic()
        r = harness.run(argv, world=w, clock=clock,
                        extract=lambda pi_bin, args, raw: dict(EXT))
        elapsed = time.monotonic() - t0
        assert r.rc == 0, r.result
        assert clock.t >= 10, clock.t     # 2 次静置 × 5 秒全记在虚拟时钟上
        assert elapsed < 2, elapsed       # 真实时间没睡
    print("3 virtual clock advances without real sleeping")

    # 4 残留全局洞（reset_settle 读模块级 hc_json/time）也被作用域内覆盖：
    #   --reset 前置路径若没被覆盖会撞真实 hc_json（missing_tool）而停轮；
    #   跑通（rc 0）即证覆盖生效，不断言前置发送序列本身。
    w = world_with_terminal()
    with tempfile.TemporaryDirectory() as td:
        r = harness.run(base_argv(td, "harness-4", extra=["--reset", "clear"]),
                        world=w, extract=lambda pi_bin, args, raw: dict(EXT))
        assert r.rc == 0, r.result
    print("4 residual module-global paths covered by scoped injection")

    # 5 env 注入到达分发脚本（隔离映射作用域内应用、跑后恢复）：backend=api 缺配置
    #   必在启动预检停轮（rc 非 0 即证 env 到达）；具体业务 kind 由既有专项测试覆盖。
    w = world_with_terminal()
    with tempfile.TemporaryDirectory() as td:
        r = harness.run(base_argv(td, "harness-5"), world=w,
                        env={"LOOP_EXTRACT_BACKEND": "api"},   # 缺 API 配置
                        extract=lambda pi_bin, args, raw: dict(EXT))
        assert r.rc == 2, r.result
    assert dict(os.environ) == before_env
    print("5 env mapping reaches dispatch, os.environ restored after run")

    # 6 残留洞吸收：合并 env 作用域内应用到 os.environ（覆盖直读 os.environ 的读者，
    #   如 learning.enabled），跑后恢复原样；原本不存在的键退出删除。
    before_env = dict(os.environ)   # LOOP_PROBE_NEW 是虚构键，本就不在环境里
    seen = {}

    def probe_extract(pi_bin, args, turn_path):
        seen["LOOP_LEARNING"] = os.environ.get("LOOP_LEARNING")
        seen["LOOP_PROBE_NEW"] = os.environ.get("LOOP_PROBE_NEW")
        return dict(EXT)

    w = world_with_terminal()
    with tempfile.TemporaryDirectory() as td:
        r = harness.run(base_argv(td, "harness-6"), world=w,
                        env={"LOOP_PROBE_NEW": "probe-val"}, extract=probe_extract)
        assert r.rc == 0, r.result
    assert seen["LOOP_LEARNING"] == "off", seen          # 默认隔离到达 os.environ 读者
    assert seen["LOOP_PROBE_NEW"] == "probe-val", seen   # 注入键作用域内可见
    assert "LOOP_PROBE_NEW" not in os.environ            # 原本不存在→退出删除
    assert dict(os.environ) == before_env
    print("6 env mapping applied to os.environ in scope, restored on exit")

    # 7 残留洞吸收：注入 http_open/run_cmd 时作用域内替换模块级 webhook_open/run_cmd，
    #   退出无条件恢复；不注入则模块级原样不动。
    saved_open, saved_cmd = dispatch.webhook_open, dispatch.run_cmd
    marker_open = lambda req, timeout, proxies: None
    marker_cmd = lambda argv, timeout_s: None
    seen2 = {}

    def probe_extract2(pi_bin, args, turn_path):
        seen2["open"] = dispatch.webhook_open
        seen2["cmd"] = dispatch.run_cmd
        return dict(EXT)

    w = world_with_terminal()
    with tempfile.TemporaryDirectory() as td:
        r = harness.run(base_argv(td, "harness-7"), world=w,
                        http_open=marker_open, run_cmd=marker_cmd, extract=probe_extract2)
        assert r.rc == 0, r.result
    assert seen2["open"] is marker_open and seen2["cmd"] is marker_cmd
    assert dispatch.webhook_open is saved_open and dispatch.run_cmd is saved_cmd
    seen3 = {}

    def probe_extract3(pi_bin, args, turn_path):
        seen3["open"] = dispatch.webhook_open
        seen3["cmd"] = dispatch.run_cmd
        return dict(EXT)

    w = world_with_terminal()
    with tempfile.TemporaryDirectory() as td:
        harness.run(base_argv(td, "harness-7b"), world=w, extract=probe_extract3)
    assert seen3["open"] is saved_open and seen3["cmd"] is saved_cmd
    print("7 injected http_open/run_cmd replace module globals in scope, restored")

    print("all harness tests passed")


if __name__ == "__main__":
    main()