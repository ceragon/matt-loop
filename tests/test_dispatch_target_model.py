#!/usr/bin/env python3
"""test_dispatch_target_model.py — 切模型前置流程测试（FakeHerdr 假世界，不打真实 agent）。

跑法：python3 tests/test_dispatch_target_model.py
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

T5a（ADR-0021，#99）：完整轮用例迁 FakeHerdr＋harness.run（唯一 CLI 缝）；
拒发场景用假世界故障旋钮 w.fail("send", nth=…)。常量与助手本文件自带，
不 import 供应方文件。
"""
import tempfile
from pathlib import Path

import harness
from fake_herdr import FakeHerdr

PROMPT = "请实现某个功能\n第二行说明"
ANCHOR = PROMPT.splitlines()[0][:24]
SCREEN = ANCHOR + "\n已完成该功能。"
EXT = {"needs_user": False, "questions": [],
       "conclusion": "", "blocked": "", "reply": SCREEN}


def _world(turn=5, refuse_nth=None):
    """既有 idle agent t99＋main 空间 pane 已在＋稳定屏幕；refuse_nth 定位第几次 send 拒发。"""
    w = FakeHerdr()
    w.add_terminal(address="t99", name="claude-test", labels={"loop": "1"}, turn=turn)
    sp = w.add_space(name="main")
    w.add_tab(sp, terminals=["t99"])
    w.screen("t99", SCREEN)
    if refuse_nth is not None:
        w.fail("send", "error", kind="status_mismatch", nth=refuse_nth)
    return w


def _argv(td, rid, reset=None, model=None, prompt=PROMPT, pre_ledger=False):
    pf = Path(td) / "prompt.md"
    pf.write_text(prompt, encoding="utf-8")
    ld = Path(td) / ".loop"
    if pre_ledger:
        (ld / "runs").mkdir(parents=True)
        (ld / "runs" / (rid + ".json")).write_text("{}", encoding="utf-8")
    a = ["--target", "t99", "--release-id", rid,
         "--prompt-file", str(pf), "--loop-dir", str(ld),
         "--space", "main", "--wall-clock", "5", "--check-in", "1",
         "--settle-seconds", "0.01", "--settle-checks", "2"]
    if reset is not None:
        a += ["--reset", reset]
    if model is not None:
        a += ["--target-model", model]
    return a


def _ext(pi_bin, args, turn_path):
    return dict(EXT)


def run_case(reset_arg, model_arg, w, rid, prompt=PROMPT, pre_ledger=False):
    with tempfile.TemporaryDirectory() as td:
        r = harness.run(_argv(td, rid, reset=reset_arg, model=model_arg,
                              prompt=prompt, pre_ledger=pre_ledger),
                        world=w, extract=_ext)
        return r.rc, r.result, r.ledger


def main():
    # 1 向后兼容：不传 --target-model
    w = _world()
    rc, res, led = run_case(None, None, w, "tm-compat-1")
    assert rc == 0 and res["ok"], res
    assert w.sends == [("t99", PROMPT, True)], w.sends
    assert "target_model" not in res, res
    assert led["target_model"] is None and "model_send" not in led, led
    print("1 backward-compat (no --target-model): single main send, no target_model ok")

    # 2 单独带 --target-model：前置在前，主任务在后
    w = _world()
    rc, res, led = run_case(None, "sonnet", w, "tm-standalone-1")
    assert rc == 0 and res["ok"], res
    assert w.sends == [("t99", "/model sonnet", True), ("t99", PROMPT, True)], w.sends
    assert res["target_model"] == "sonnet" and led["target_model"] == "sonnet"
    assert led.get("model_send") and led.get("model_done_at"), led
    assert "reset_send" not in led and led["phase"] == "done"
    print("2 standalone --target-model: /model then main, ledger/result record target_model ok")

    # 3 reset ＋ model 同传：顺序固定 /clear → /model → 主任务
    w = _world()
    rc, res, led = run_case("clear", "opus", w, "tm-combo-1")
    assert rc == 0 and res["ok"], res
    assert w.sends == [("t99", "/clear", True), ("t99", "/model opus", True),
                       ("t99", PROMPT, True)], w.sends
    assert res["reset"] == "clear" and res["target_model"] == "opus"
    assert led.get("reset_done_at") and led.get("model_done_at"), led
    print("3 reset+model: /clear -> /model -> main, fixed order ok")

    # 4 名字原样透传（provider/id:thinking 形状），不映射不校验
    w = _world()
    name = "anthropic/claude-sonnet-4-5:high"
    rc, res, led = run_case(None, name, w, "tm-literal-1")
    assert rc == 0 and w.sends[0] == ("t99", "/model " + name, True), w.sends
    assert res["target_model"] == name
    print("4 literal passthrough (provider/id:thinking): no mapping, no validation ok")

    # 5 切模型前置拒发（复核非 done）：整轮停，主任务未发
    w = _world(refuse_nth=1)
    rc, res, led = run_case(None, "sonnet", w, "tm-refuse-pre")
    assert rc == 1 and not res["ok"], res
    assert res["error"]["kind"] == "send_refused", res
    assert res["target_model"] == "sonnet" and res["error"]["model_sent"] is False
    assert w.sends == [], w.sends
    assert led["phase"].startswith("error:")
    print("5 model pre-step refused: round aborts, main not sent, model_sent=False ok")

    # 6 重置已发、切模型前置拒发：reset_sent=True、model_sent=False
    w = _world(refuse_nth=2)
    rc, res, led = run_case("clear", "opus", w, "tm-refuse-model")
    assert rc == 1 and not res["ok"], res
    assert res["error"]["kind"] == "send_refused", res
    assert res["error"]["reset_sent"] is True and res["error"]["model_sent"] is False
    assert w.sends == [("t99", "/clear", True)], w.sends
    assert led.get("reset_done_at"), led
    print("6 reset sent, model pre-step refused: reset_sent=True model_sent=False ok")

    # 7 两前置已发、主任务拒发：reset_sent=True、model_sent=True
    w = _world(refuse_nth=3)
    rc, res, led = run_case("clear", "opus", w, "tm-refuse-main")
    assert rc == 1 and not res["ok"], res
    assert res["error"]["kind"] == "send_refused", res
    assert res["error"]["reset_sent"] is True and res["error"]["model_sent"] is True
    assert w.sends == [("t99", "/clear", True), ("t99", "/model opus", True)], w.sends
    assert led.get("model_done_at"), led   # 切模型前置确已落定
    print("7 both pre-steps sent, main refused: reset_sent=True model_sent=True ok")

    # 8 会话起始 skill＋turn>0＋reset=none：切模型不能替代重置
    w = _world(turn=5)
    rc, res, led = run_case(None, "opus", w, "tm-discipline-1", prompt="/wayfinder #7")
    assert rc == 2 and not res["ok"], res
    assert res["error"]["kind"] == "reset_required", res
    assert res["target_model"] == "opus" and res["error"]["model_sent"] is False
    assert w.sends == [], w.sends
    assert led is None, "拒发应在写台账前，放行 id 不消耗"
    print("8 start-skill turn>0 reset=none + model: still reset_required, id unconsumed ok")

    # 9 turn==0 豁免＋切模型前置照跑
    w = _world(turn=0)
    rc, res, led = run_case(None, "gemini-3-pro", w, "tm-fresh-1", prompt="/wayfinder #7")
    assert rc == 0 and res["ok"], res
    assert w.sends == [("t99", "/model gemini-3-pro", True),
                       ("t99", "/wayfinder #7", True)], w.sends
    assert res["target_model"] == "gemini-3-pro" and res["reset"] == "none"
    print("9 turn==0 exempt + model pre-step still runs ok")

    # 10 duplicate_release 优先于一切前置
    w = _world()
    rc, res, led = run_case("clear", "opus", w, "tm-dup-1", pre_ledger=True)
    assert rc == 1 and not res["ok"], res
    assert res["error"]["kind"] == "duplicate_release", res
    assert w.sends == [], w.sends
    print("10 duplicate_release takes precedence over pre-steps ok")

    print("all target-model tests passed")


if __name__ == "__main__":
    main()
