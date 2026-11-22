#!/usr/bin/env python3
"""test_dispatch_reset_discipline.py — 会话起始 skill 纪律测试（ADR-0004，mock hc/pi）。

跑法：python3 loop/scripts/test_dispatch_reset_discipline.py
覆盖：
  1 纯函数 find_session_start_trigger：五个 skill 行首命中（带参/无空格带参/行前空白/
    多行行号/CRLF/报长名）；行中提及、/implementing、/implement-foo、/prototype2、
    无斜杠裸名都不触发；多处命中报第一处。
  2 触发＋--reset=none：写台账前拒发，rc=2、kind=reset_required、message 带 skill 与行号；
    零发送、台账不存在（放行 id 不消耗）、result.json 与 stdout 逐字一致。
  3 同一放行 id 补 --reset clear 重试成功：前置＋主任务两条，证明 id 未被消耗。
  4 触发＋new/clear/compact 都放行（compact 也算重置，Q4 定案）。
  5 非触发提示词（行中提及）＋不传 --reset：照常单发，向后兼容。
  6 台账已存在时 duplicate_release 优先于 reset_required。
"""
import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dispatch  # noqa: E402
import os  # noqa: E402
# 本文件 mock extract，走 pi 后端保持旧行为（ADR-0005 起默认 api 直连）。
os.environ["LOOP_EXTRACT_BACKEND"] = "pi"


ANCHOR_SRC = "请实现某个功能"
SCREEN = ANCHOR_SRC + "\n已完成该功能。"
EXT = {"needs_user": False, "questions": [],
       "conclusion": "", "blocked": "", "reply": SCREEN}


class FakeHc:
    def __init__(self):
        self.sends = []          # (text, if_idle)
        self.turn = 5
        self.state = "idle"
        self.screen = SCREEN

    def __call__(self, hc_bin, args, timeout_s):
        cmd = args[0]
        if cmd == "get":
            return {"state": "live", "turn": self.turn, "title": "claude-test",
                    "status": {"state": self.state, "title": "claude-test"},
                    "foreground": {"name": "claude"}}
        if cmd == "send":
            text = args[-1]
            self.sends.append((text, "--if" in args))
            baseline = self.turn
            self.turn += 1
            return {"turn": baseline}
        if cmd == "read":
            return {"text": self.screen, "complete": True, "how": "current", "why": ""}
        if cmd == "wait":
            return {"reason": "status", "status": self.state, "turn": self.turn}
        raise AssertionError("unexpected hc cmd: " + cmd)


def run_case(prompt, reset_arg, rid, td, fake):
    """在给定临时目录里跑一轮 dispatch；返回 (rc, result, ledger_path, result_path)。"""
    pf = Path(td) / "prompt.md"
    pf.write_text(prompt, encoding="utf-8")
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
    out = buf.getvalue().strip()
    result = json.loads(out)
    ledger_path = ld / "runs" / (rid + ".json")
    result_path = ld / "runs" / (rid + ".result.json")
    if result_path.exists():
        assert result_path.read_text(encoding="utf-8").strip() == out, "result 文件与 stdout 不一致"
    return rc, result, ledger_path, result_path


def main():
    f = dispatch.find_session_start_trigger

    # 1 纯函数：命中
    for skill in dispatch.SESSION_START_SKILLS:
        assert f("/" + skill) == (skill, 1), skill
        assert f("/" + skill + " #7") == (skill, 1), skill
    assert f("/wayfinder#7") == ("wayfinder", 1)          # 无空格带参
    assert f("  /implement #3") == ("implement", 1)        # 行前空格
    assert f("\t/prototype q1") == ("prototype", 1)        # 行前 tab
    assert f("先读票。\n第二行\n/grill-with-docs 开始") == ("grill-with-docs", 3)
    assert f("/wayfinder #7\r\n后续") == ("wayfinder", 1)  # CRLF
    assert f("/implement-spec #3") == ("implement-spec", 1)  # 报长名
    assert f("/implement #1\n/wayfinder #2") == ("implement", 1)  # 多处命中报第一处
    # 1b 纯函数：不触发
    assert f("答完这几个问题再跑 /implement") is None      # 行中提及
    assert f("/implementing") is None
    assert f("/implement-foo") is None
    assert f("/prototype2") is None
    assert f("/grill-with-docs-extra") is None
    assert f("wayfinder 地图更新了") is None               # 无斜杠裸名
    assert f("普通回复，没有命令") is None
    print("1 find_session_start_trigger: hits and non-hits ok")

    # 2 触发＋none：台账前拒发，id 不消耗
    with tempfile.TemporaryDirectory() as td:
        fake = FakeHc()
        rc, res, lp, rp = run_case("/wayfinder #7", None, "disc-1", td, fake)
        assert rc == 2, rc
        assert not res["ok"] and res["error"]["kind"] == "reset_required", res
        assert "/wayfinder" in res["error"]["message"] and "第 1 行" in res["error"]["message"]
        assert res["error"]["hints"], res
        assert fake.sends == [], fake.sends                # 零发送
        assert not lp.exists(), "拒发不应写台账"
        assert rp.exists(), "拒发也要写 result.json"
        assert "reset" not in res                          # reset=none 不带 reset 键
        print("2 trigger + none: refused before ledger, rc=2, id unconsumed")

        # 3 同 id 补 --reset clear 重试成功
        fake2 = FakeHc()
        rc, res, lp, rp = run_case("/wayfinder #7", "clear", "disc-1", td, fake2)
        assert rc == 0 and res["ok"], res
        assert fake2.sends == [("/clear", True), ("/wayfinder #7", True)], fake2.sends
        assert res["reset"] == "clear"
        assert lp.exists()
        print("3 same release id retry with --reset clear: ok")

    # 4 new/clear/compact 都放行
    for i, mode in enumerate(("new", "clear", "compact")):
        with tempfile.TemporaryDirectory() as td:
            fake = FakeHc()
            rc, res, lp, _ = run_case("/implement #9", mode, f"disc-4{mode}", td, fake)
            assert rc == 0 and res["ok"], (mode, res)
            assert fake.sends == [("/" + mode, True), ("/implement #9", True)], fake.sends
    print("4 trigger + new/clear/compact: all accepted")

    # 5 非触发＋不传 --reset：照常单发
    with tempfile.TemporaryDirectory() as td:
        fake = FakeHc()
        p = "这几个问题答完再跑 /implement，先给结论"
        rc, res, _, _ = run_case(p, None, "disc-5", td, fake)
        assert rc == 0 and res["ok"], res
        assert fake.sends == [(p, True)], fake.sends
    print("5 mid-line mention + no reset: single send, backward compatible")

    # 6 duplicate_release 优先
    with tempfile.TemporaryDirectory() as td:
        fake = FakeHc()
        rc, _, lp, _ = run_case(ANCHOR_SRC, None, "disc-6", td, fake)
        assert rc == 0 and lp.exists()
        fake2 = FakeHc()
        rc, res, _, _ = run_case("/wayfinder #7", None, "disc-6", td, fake2)
        assert rc == 1 and res["error"]["kind"] == "duplicate_release", res
        assert fake2.sends == []
    print("6 duplicate_release takes precedence over reset_required")

    print("ALL OK")


if __name__ == "__main__":
    main()