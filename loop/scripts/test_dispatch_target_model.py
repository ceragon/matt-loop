#!/usr/bin/env python3
"""test_dispatch_target_model.py — 切模型前置流程测试（mock hc/pi，不打真实 agent）。

跑法：python3.12 loop/scripts/test_dispatch_target_model.py
覆盖（ADR-0006）：
  1 向后兼容：不传 --target-model，只发主任务一条，结果无 target_model。
  2 单独带 --target-model：先发 /model <名字>、落定后再发主任务；台账/result 记 target_model。
  3 --reset ＋ --target-model 同传：顺序固定 /clear → /model → 主任务。
  4 名字原样透传（provider/id:thinking 形状），脚本不映射不校验。
  5 切模型前置拒发：整轮停，主任务未发，model_sent=False。
  6 重置已发、切模型前置拒发：reset_sent=True、model_sent=False。
  7 两前置已发、主任务拒发：reset_sent=True、model_sent=True（部分状态）。
  8 会话起始 skill＋turn>0＋reset=none：切模型不能替代重置，仍 reset_required（id 不消耗）。
  9 turn==0 豁免＋切模型前置照跑。
 10 duplicate_release 优先于一切前置。
"""
import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dispatch  # noqa: E402
from test_dispatch_reset import FakeApp  # noqa: E402
import os  # noqa: E402
# 本文件 mock extract，走 pi 后端保持旧行为（ADR-0005 起默认 api 直连）。
os.environ["LOOP_EXTRACT_BACKEND"] = "pi"


PROMPT = "请实现某个功能\n第二行说明"
ANCHOR = PROMPT.splitlines()[0][:24]
SCREEN = ANCHOR + "\n已完成该功能。"
EXT = {"needs_user": False, "questions": [],
       "conclusion": "", "blocked": "", "reply": SCREEN}


class FakeHc:
    def __init__(self, state="idle", refuse_nth=None, refuse_kind="status_mismatch", turn=5):
        self.sends = []          # (text, if_idle)
        self.turn = turn
        self.state = state
        self.screen = SCREEN
        self.refuse_nth = refuse_nth   # 第 n 次 send 拒发（1 起）
        self.refuse_kind = refuse_kind
        self._n = 0

    def __call__(self, hc_bin, args, timeout_s):
        cmd = args[0]
        if cmd == "get":
            return {"state": "live", "turn": self.turn, "title": "claude-test",
                    "address": "t99", "name": "claude-test",
                    "labels": {"loop": "1"},
                    "status": {"state": self.state, "title": "claude-test"},
                    "foreground": {"name": "claude", "argv": ["claude"]}}
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


def run_case(reset_arg, model_arg, fake, rid, prompt=PROMPT, pre_ledger=False):
    with tempfile.TemporaryDirectory() as td:
        pf = Path(td) / "prompt.md"
        pf.write_text(prompt, encoding="utf-8")
        ld = Path(td) / ".loop"
        if pre_ledger:
            (ld / "runs").mkdir(parents=True)
            (ld / "runs" / (rid + ".json")).write_text("{}", encoding="utf-8")
        argv = ["--target", "t99", "--release-id", rid,
                "--prompt-file", str(pf), "--loop-dir", str(ld),
                "--space", "main",
                "--wall-clock", "5", "--check-in", "1",
                "--settle-seconds", "0.01", "--settle-checks", "2"]
        if reset_arg is not None:
            argv += ["--reset", reset_arg]
        if model_arg is not None:
            argv += ["--target-model", model_arg]
        dispatch.hc_json = fake
        dispatch.app_json = FakeApp()
        dispatch.extract = lambda pi_bin, args, raw_path: dict(EXT)
        dispatch.check_structure_and_tools = lambda: None
        dispatch.shutil.which = lambda name: "/fake/" + name
        dispatch.time.sleep = lambda s: None
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = dispatch.main(argv)
        result = json.loads(buf.getvalue().strip())
        led_path = ld / "runs" / (rid + ".json")
        ledger = json.loads(led_path.read_text(encoding="utf-8")) if led_path.exists() else None
        return rc, result, ledger


def main():
    # 1 向后兼容：不传 --target-model
    fake = FakeHc()
    rc, res, led = run_case(None, None, fake, "tm-compat-1")
    assert rc == 0 and res["ok"], res
    assert fake.sends == [(PROMPT, True)], fake.sends
    assert "target_model" not in res, res
    assert led["target_model"] is None and "model_send" not in led, led
    print("1 backward-compat (no --target-model): single main send, no target_model ok")

    # 2 单独带 --target-model：前置在前，主任务在后
    fake = FakeHc()
    rc, res, led = run_case(None, "sonnet", fake, "tm-standalone-1")
    assert rc == 0 and res["ok"], res
    assert fake.sends == [("/model sonnet", True), (PROMPT, True)], fake.sends
    assert res["target_model"] == "sonnet" and led["target_model"] == "sonnet"
    assert led.get("model_send") and led.get("model_done_at"), led
    assert "reset_send" not in led and led["phase"] == "done"
    print("2 standalone --target-model: /model then main, ledger/result record target_model ok")

    # 3 reset ＋ model 同传：顺序固定 /clear → /model → 主任务
    fake = FakeHc()
    rc, res, led = run_case("clear", "opus", fake, "tm-combo-1")
    assert rc == 0 and res["ok"], res
    assert fake.sends == [("/clear", True), ("/model opus", True), (PROMPT, True)], fake.sends
    assert res["reset"] == "clear" and res["target_model"] == "opus"
    assert led.get("reset_done_at") and led.get("model_done_at"), led
    print("3 reset+model: /clear -> /model -> main, fixed order ok")

    # 4 名字原样透传（provider/id:thinking 形状），不映射不校验
    fake = FakeHc()
    name = "anthropic/claude-sonnet-4-5:high"
    rc, res, led = run_case(None, name, fake, "tm-literal-1")
    assert rc == 0 and fake.sends[0] == ("/model " + name, True), fake.sends
    assert res["target_model"] == name
    print("4 literal passthrough (provider/id:thinking): no mapping, no validation ok")

    # 5 切模型前置拒发（复核非 done）：整轮停，主任务未发
    fake = FakeHc(state="idle", refuse_nth=1)
    rc, res, led = run_case(None, "sonnet", fake, "tm-refuse-pre")
    assert rc == 1 and not res["ok"], res
    assert res["error"]["kind"] == "send_refused", res
    assert res["target_model"] == "sonnet" and res["error"]["model_sent"] is False
    assert fake.sends == [], fake.sends
    assert led["phase"].startswith("error:")
    print("5 model pre-step refused: round aborts, main not sent, model_sent=False ok")

    # 6 重置已发、切模型前置拒发：reset_sent=True、model_sent=False
    fake = FakeHc(state="idle", refuse_nth=2)
    rc, res, led = run_case("clear", "opus", fake, "tm-refuse-model")
    assert rc == 1 and not res["ok"], res
    assert res["error"]["kind"] == "send_refused", res
    assert res["error"]["reset_sent"] is True and res["error"]["model_sent"] is False
    assert fake.sends == [("/clear", True)], fake.sends
    assert led.get("reset_done_at"), led
    print("6 reset sent, model pre-step refused: reset_sent=True model_sent=False ok")

    # 7 两前置已发、主任务拒发：reset_sent=True、model_sent=True
    fake = FakeHc(state="idle", refuse_nth=3)
    rc, res, led = run_case("clear", "opus", fake, "tm-refuse-main")
    assert rc == 1 and not res["ok"], res
    assert res["error"]["kind"] == "send_refused", res
    assert res["error"]["reset_sent"] is True and res["error"]["model_sent"] is True
    assert fake.sends == [("/clear", True), ("/model opus", True)], fake.sends
    assert led.get("model_done_at"), led   # 切模型前置确已落定
    print("7 both pre-steps sent, main refused: reset_sent=True model_sent=True ok")

    # 8 会话起始 skill＋turn>0＋reset=none：切模型不能替代重置
    fake = FakeHc(turn=5)
    rc, res, led = run_case(None, "opus", fake, "tm-discipline-1",
                            prompt="/wayfinder #7\n请绘图")
    assert rc == 2 and not res["ok"], res
    assert res["error"]["kind"] == "reset_required", res
    assert res["target_model"] == "opus" and res["error"]["model_sent"] is False
    assert fake.sends == [], fake.sends
    assert led is None, "拒发应在写台账前，放行 id 不消耗"
    print("8 start-skill turn>0 reset=none + model: still reset_required, id unconsumed ok")

    # 9 turn==0 豁免＋切模型前置照跑
    fake = FakeHc(turn=0)
    rc, res, led = run_case(None, "gemini-3-pro", fake, "tm-fresh-1",
                            prompt="/wayfinder #7\n请绘图")
    assert rc == 0 and res["ok"], res
    assert fake.sends == [("/model gemini-3-pro", True),
                          ("/wayfinder #7\n请绘图", True)], fake.sends
    assert res["target_model"] == "gemini-3-pro" and res["reset"] == "none"
    print("9 turn==0 exempt + model pre-step still runs ok")

    # 10 duplicate_release 优先于一切前置
    fake = FakeHc()
    rc, res, led = run_case("clear", "opus", fake, "tm-dup-1", pre_ledger=True)
    assert rc == 1 and not res["ok"], res
    assert res["error"]["kind"] == "duplicate_release", res
    assert fake.sends == [], fake.sends
    print("10 duplicate_release takes precedence over pre-steps ok")

    print("all target-model tests passed")


if __name__ == "__main__":
    main()
