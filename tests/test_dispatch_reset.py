#!/usr/bin/env python3
"""test_dispatch_reset.py — reset 前置流程测试（mock hc/pi，不打真实 agent）。

跑法：python3.12 tests/test_dispatch_reset.py
覆盖：
  1 向后兼容：不传 --reset / --reset none，只发主任务一条。
  2 reset=clear：先发 /clear、落定后再发主任务，共两条；台账/result 记 reset。
  3 前置拒发（非 done）：整轮停，主任务未发，reset_sent=False。
  4 前置已发、主任务拒发：部分状态，reset_sent=True。
  5 前置落定复核（CLI 对照对，T4 缝上重述）：屏幕一直变＋--reset→前置墙钟 timeout、
    主任务未发、台账 error phase；屏幕先变后静置→落定 done。

T4（ADR-0021）：本文件业务用例已迁 FakeHerdr＋harness.run（唯一 CLI 缝）。
R（ADR-0021，#104）：残留兼容面（FakeHc/FakeApp/run_case/import 期环境写入）已删，
棘轮白名单条目随之删除；隔离由 harness DEFAULT_ENV（pi 后端、学习 off）承担。
共享常量与 world/ext 构造器迁 helpers 单一出处；_argv 带 reset/落定参数，留本地。
"""
import tempfile
from pathlib import Path

import harness
from helpers import PROMPT, SCREEN, EXT, world, ext


def _argv(td, rid, reset=None, wall_clock="5", settle_seconds="0.01", settle_checks="2"):
    pf = Path(td) / "prompt.md"
    pf.write_text(PROMPT, encoding="utf-8")
    a = ["--target", "t99", "--release-id", rid,
         "--prompt-file", str(pf), "--loop-dir", str(Path(td) / ".loop"),
         "--space", "main", "--wall-clock", wall_clock, "--check-in", "1",
         "--settle-seconds", settle_seconds, "--settle-checks", settle_checks]
    if reset is not None:
        a += ["--reset", reset]
    return a


_ext = ext(EXT)   # extract 桩：六字段正典形状（helpers.ext 构造器）


def main():
    # 1 向后兼容：不传 --reset
    w = world()
    with tempfile.TemporaryDirectory() as td:
        r = harness.run(_argv(td, "reset-compat-1"), world=w, extract=_ext)
    assert r.rc == 0 and r.result["ok"], r.result
    assert w.sends == [("t99", PROMPT, True)], w.sends
    assert r.result["reset"] == "none" and r.ledger["reset"] == "none"
    assert "reset_send" not in r.ledger
    print("1 backward-compat (no --reset): single main send ok")

    # 1b --reset none 显式
    w = world()
    with tempfile.TemporaryDirectory() as td:
        r = harness.run(_argv(td, "reset-compat-2", reset="none"), world=w, extract=_ext)
    assert r.rc == 0 and w.sends == [("t99", PROMPT, True)] and r.result["reset"] == "none"
    print("1b --reset none: single main send ok")

    # 2 reset=clear：两条发送，前置在前
    w = world()
    with tempfile.TemporaryDirectory() as td:
        r = harness.run(_argv(td, "reset-clear-1", reset="clear"), world=w, extract=_ext)
    assert r.rc == 0 and r.result["ok"], r.result
    assert w.sends == [("t99", "/clear", True), ("t99", PROMPT, True)], w.sends
    assert r.result["reset"] == "clear" and r.ledger["reset"] == "clear"
    assert r.ledger.get("reset_send") and r.ledger.get("reset_done_at"), r.ledger
    assert r.ledger["phase"] == "done"
    print("2 reset=clear: /clear then main, ledger/result record reset ok")

    # 2b reset=new / compact 也发对应字面命令
    for rst in ("new", "compact"):
        w = world()
        with tempfile.TemporaryDirectory() as td:
            r = harness.run(_argv(td, "reset-lit-" + rst, reset=rst), world=w, extract=_ext)
        assert r.rc == 0 and w.sends[0] == ("t99", "/" + rst, True), (rst, w.sends)
        assert r.result["reset"] == rst
    print("2b reset=new/compact: literal /new /compact ok")

    # 3 前置拒发（复核非 done）：整轮停，主任务未发
    w = world()
    w.fail("send", "error", kind="status_mismatch", nth=1)
    with tempfile.TemporaryDirectory() as td:
        r = harness.run(_argv(td, "reset-refuse-pre", reset="clear"), world=w, extract=_ext)
    assert r.rc == 1 and not r.result["ok"], r.result
    assert r.result["error"]["kind"] == "send_refused", r.result
    assert r.result["reset"] == "clear" and r.result["error"]["reset_sent"] is False
    assert w.sends == [], w.sends
    assert r.ledger["phase"].startswith("error:")
    print("3 pre-step refused: round aborts, main not sent, reset_sent=False ok")

    # 4 前置已发、主任务拒发：部分状态 reset_sent=True
    w = world()
    w.fail("send", "error", kind="status_mismatch", nth=2)
    with tempfile.TemporaryDirectory() as td:
        r = harness.run(_argv(td, "reset-refuse-main", reset="clear"), world=w, extract=_ext)
    assert r.rc == 1 and not r.result["ok"], r.result
    assert r.result["error"]["kind"] == "send_refused", r.result
    assert r.result["reset"] == "clear" and r.result["error"]["reset_sent"] is True
    assert w.sends == [("t99", "/clear", True)], w.sends
    assert r.ledger.get("reset_done_at"), r.ledger   # 前置确已落定
    print("4 pre-step sent, main refused: partial state reset_sent=True ok")

    # 5 前置落定复核（CLI 对照对，缝上重述）：屏幕一直变→前置墙钟 timeout、主任务未发；
    #   屏幕先变后静置→落定 done。reset_settle 函数本体不动（残留洞随 P2 收口）。
    # 5a 屏幕一直变＋--reset：复核窗口内屏幕逐次不同，虚拟墙钟耗尽→timeout。
    w = world()
    w.screen_sequence("t99", ["变一", "变二", "变三"])
    with tempfile.TemporaryDirectory() as td:
        r = harness.run(_argv(td, "reset-settle-change", reset="clear",
                              wall_clock="0.05", settle_seconds="1", settle_checks="2"),
                        world=w, extract=_ext)
    assert r.rc == 1 and r.result["error"]["kind"] == "timeout", r.result
    assert w.sends == [("t99", "/clear", True)], w.sends   # 主任务未发
    assert r.ledger["phase"] == "error:timeout", r.ledger
    # 5b 屏幕先变后静置：序列耗尽重复最后一条→静置落定→主任务发送→done。
    w = world()
    w.screen_sequence("t99", ["变一", "变二", SCREEN])
    with tempfile.TemporaryDirectory() as td:
        r = harness.run(_argv(td, "reset-settle-stable", reset="clear"), world=w, extract=_ext)
    assert r.rc == 0 and r.result["ok"], r.result
    assert w.sends == [("t99", "/clear", True), ("t99", PROMPT, True)], w.sends
    assert r.ledger["phase"] == "done" and r.ledger.get("reset_done_at"), r.ledger
    print("5 settle recheck (CLI pair): changing->timeout/main unsent, settling->done ok")

    print("all reset tests passed")


if __name__ == "__main__":
    main()