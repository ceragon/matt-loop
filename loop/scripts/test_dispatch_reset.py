#!/usr/bin/env python3
"""test_dispatch_reset.py — reset 前置流程测试（mock hc/pi，不打真实 agent）。

跑法：python3.12 loop/scripts/test_dispatch_reset.py
覆盖：
  1 向后兼容：不传 --reset / --reset none，只发主任务一条。
  2 reset=clear：先发 /clear、落定后再发主任务，共两条；台账/result 记 reset。
  3 前置拒发（非 done）：整轮停，主任务未发，reset_sent=False。
  4 前置已发、主任务拒发：部分状态，reset_sent=True。
  5 reset_settle：屏幕仍在变→未落定；屏幕静置→落定。
"""
import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dispatch  # noqa: E402

PROMPT = "请实现某个功能\n第二行说明"
ANCHOR = PROMPT.splitlines()[0][:24]
SCREEN = ANCHOR + "\n已完成该功能。"
EXT = {"needs_user": False, "questions": [],
       "conclusion": "", "blocked": "", "reply": SCREEN}


class FakeHc:
    def __init__(self, state="idle", refuse_nth=None, refuse_kind="status_mismatch"):
        self.sends = []          # (text, if_idle)
        self.turn = 5
        self.state = state
        self.screen = SCREEN
        self.refuse_nth = refuse_nth   # 第 n 次 send 拒发（1 起）
        self.refuse_kind = refuse_kind
        self._n = 0

    def __call__(self, hc_bin, args, timeout_s):
        cmd = args[0]
        if cmd == "get":
            return {"state": "live", "turn": self.turn, "title": "claude-test",
                    "status": {"state": self.state, "title": "claude-test"},
                    "foreground": {"name": "claude"}}
        if cmd == "send":
            self._n += 1
            if self.refuse_nth is not None and self._n == self.refuse_nth:
                raise dispatch.HcFailure(self.refuse_kind, "refused", [], 1,
                                         {"kind": self.refuse_kind})
            text = args[-1]
            self.sends.append((text, "--if" in args))
            baseline = self.turn      # hc send 返回处理前的 turn 作基线
            self.turn += 1            # 处理后 turn +1，等待判据 turn > baseline
            return {"turn": baseline}
        if cmd == "read":
            return {"text": self.screen, "complete": True, "how": "current", "why": ""}
        if cmd == "wait":
            return {"reason": "status", "status": self.state, "turn": self.turn}
        raise AssertionError("unexpected hc cmd: " + cmd)


def run_case(reset_arg, fake, rid):
    with tempfile.TemporaryDirectory() as td:
        pf = Path(td) / "prompt.md"
        pf.write_text(PROMPT, encoding="utf-8")
        ld = Path(td) / ".loop"
        argv = ["--target", "t99", "--release-id", rid,
                "--prompt-file", str(pf), "--loop-dir", str(ld),
                "--wall-clock", "5", "--check-in", "1",
                "--settle-seconds", "0.01", "--settle-checks", "2"]
        if reset_arg is not None:
            argv += ["--reset", reset_arg]
        dispatch.hc_json = fake
        dispatch.extract = lambda pi_bin, args, raw_path: dict(EXT)
        dispatch.check_structure_and_tools = lambda: None
        dispatch.shutil.which = lambda name: "/fake/" + name
        dispatch.time.sleep = lambda s: None
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = dispatch.main(argv)
        result = json.loads(buf.getvalue().strip())
        ledger = json.loads((ld / "runs" / (rid + ".json")).read_text(encoding="utf-8"))
        return rc, result, ledger


def main():
    # 1 向后兼容：不传 --reset
    fake = FakeHc()
    rc, res, led = run_case(None, fake, "reset-compat-1")
    assert rc == 0 and res["ok"], res
    assert fake.sends == [(PROMPT, True)], fake.sends
    assert res["reset"] == "none" and led["reset"] == "none"
    assert "reset_send" not in led
    print("1 backward-compat (no --reset): single main send ok")

    # 1b --reset none 显式
    fake = FakeHc()
    rc, res, led = run_case("none", fake, "reset-compat-2")
    assert rc == 0 and fake.sends == [(PROMPT, True)] and res["reset"] == "none"
    print("1b --reset none: single main send ok")

    # 2 reset=clear：两条发送，前置在前
    fake = FakeHc()
    rc, res, led = run_case("clear", fake, "reset-clear-1")
    assert rc == 0 and res["ok"], res
    assert fake.sends == [("/clear", True), (PROMPT, True)], fake.sends
    assert res["reset"] == "clear" and led["reset"] == "clear"
    assert led.get("reset_send") and led.get("reset_done_at"), led
    assert led["phase"] == "done"
    print("2 reset=clear: /clear then main, ledger/result record reset ok")

    # 2b reset=new / compact 也发对应字面命令
    for r in ("new", "compact"):
        fake = FakeHc()
        rc, res, led = run_case(r, fake, "reset-lit-" + r)
        assert rc == 0 and fake.sends[0] == ("/" + r, True), (r, fake.sends)
        assert res["reset"] == r
    print("2b reset=new/compact: literal /new /compact ok")

    # 3 前置拒发（复核非 done）：整轮停，主任务未发
    fake = FakeHc(state="idle", refuse_nth=1)
    rc, res, led = run_case("clear", fake, "reset-refuse-pre")
    assert rc == 1 and not res["ok"], res
    assert res["error"]["kind"] == "send_refused", res
    assert res["reset"] == "clear" and res["error"]["reset_sent"] is False
    assert fake.sends == [], fake.sends
    assert led["phase"].startswith("error:")
    print("3 pre-step refused: round aborts, main not sent, reset_sent=False ok")

    # 4 前置已发、主任务拒发：部分状态 reset_sent=True
    fake = FakeHc(state="idle", refuse_nth=2)
    rc, res, led = run_case("clear", fake, "reset-refuse-main")
    assert rc == 1 and not res["ok"], res
    assert res["error"]["kind"] == "send_refused", res
    assert res["reset"] == "clear" and res["error"]["reset_sent"] is True
    assert fake.sends == [("/clear", True)], fake.sends
    assert led.get("reset_done_at"), led   # 前置确已落定
    print("4 pre-step sent, main refused: partial state reset_sent=True ok")

    # 5 reset_settle：屏幕变化→未落定；静置→落定
    class Args:
        settle_checks = 2
        settle_seconds = 0
        target = "t99"
    screens = iter(["a", "b", "c"])     # 一直在变
    dispatch.hc_json = lambda hc_bin, args, ts: (
        {"state": "live", "status": {"state": "idle"}, "turn": 5}
        if args[0] == "get" else {"text": next(screens)})
    dispatch.time.sleep = lambda s: None
    assert dispatch.reset_settle("/fake/hc", Args()) is False
    dispatch.hc_json = lambda hc_bin, args, ts: (
        {"state": "live", "status": {"state": "idle"}, "turn": 5}
        if args[0] == "get" else {"text": "stable"})
    assert dispatch.reset_settle("/fake/hc", Args()) is True
    print("5 reset_settle: changing screen not settled, stable screen settled ok")

    print("all reset tests passed")


if __name__ == "__main__":
    main()