#!/usr/bin/env python3
"""test_dispatch_reset_discipline.py — 会话起始 skill 纪律测试（ADR-0004，FakeHerdr 假世界）。

跑法：python3 tests/test_dispatch_reset_discipline.py
覆盖：
  1 纯函数 find_session_start_trigger：五个 skill 行首命中（带参/无空格带参/行前空白/
    多行行号/CRLF/报长名）；行中提及、/implementing、/implement-foo、/prototype2、
    无斜杠裸名都不触发；多处命中报第一处。
  2 触发＋--reset=none＋turn>0：写台账前拒发，rc=2、kind=reset_required、message 带 skill/行号/turn；
    零发送、台账不存在（放行 id 不消耗）、result.json 与 stdout 逐字一致。
  3 同一放行 id 补 --reset clear 重试成功：前置＋主任务两条，证明 id 未被消耗。
  4 触发＋new/clear/compact 都放行（compact 也算重置，Q4 定案）。
  5 非触发提示词（行中提及）＋不传 --reset：照常单发，向后兼容。
  6 台账已存在时 duplicate_release 优先于 reset_required。
  7 触发＋--reset=none＋turn==0（全新空会话）：自动豁免、单发直过、不发重置前置。
  8 触发＋显式 --reset new＋turn==0：仍按显式参数发前置。
  9 触发＋--reset=none＋turn 缺失或非整数（如 None/False）：不豁免，仍报 reset_required（turn 未知）。
 10 目标 working 时 not_idle 优先于 reset_required（预检在 hc get 之后）。

T5a（ADR-0021，#99）：纯函数用例（1）保留原位；完整轮用例（2–10）迁 FakeHerdr＋
harness.run（唯一 CLI 缝）。turn 缺失/非整数场景经假世界 turn=None/False 表达
（test_fake_herdr 例 25）。R（#104）：SCREEN/ANCHOR_SRC/EXT 与 ext 构造器迁 helpers 单一出处（EXT 经 EXT5 别名）；
_world(turn/status) 变体留本地。
"""
import tempfile
from pathlib import Path

import harness
from harness import dispatch
from fake_herdr import FakeHerdr
from helpers import SCREEN, ext
from helpers import ANCHOR as ANCHOR_SRC, EXT5 as EXT

# 期望值独立声明（#133 R4）：生产侧只有 SESSION_START_RE 正则，本清单是测试的
# 外部真值，不从被测模块导入——改漏一处即红。
SESSION_START_SKILLS = (
    "wayfinder", "grill-with-docs", "implement", "implement-spec", "prototype",
)


def _world(turn=5, status="idle"):
    """既有 agent t99＋main 空间 pane 已在＋稳定屏幕；turn=None 缺失、False 非整数。"""
    w = FakeHerdr()
    w.add_terminal(address="t99", name="claude-test", labels={"loop": "1"},
                   turn=turn, status=status)
    sp = w.add_space(name="main")
    w.add_tab(sp, terminals=["t99"])
    w.screen("t99", SCREEN)
    return w


_ext = ext(EXT)   # extract 桩：五字段旧形状（helpers.ext 构造器）


def run_case(prompt, reset_arg, rid, td, w):
    """在给定临时目录里跑一轮 dispatch；返回 (rc, result, ledger_path, result_path)。"""
    pf = Path(td) / "prompt.md"
    pf.write_text(prompt, encoding="utf-8")
    argv = ["--target", "t99", "--release-id", rid,
            "--prompt-file", str(pf), "--loop-dir", str(Path(td) / ".loop"),
            "--space", "main", "--wall-clock", "5", "--check-in", "1",
            "--settle-seconds", "0.01", "--settle-checks", "2"]
    if reset_arg is not None:
        argv += ["--reset", reset_arg]
    r = harness.run(argv, world=w, extract=_ext)
    if r.result_path.exists():
        assert r.result_path.read_text(encoding="utf-8").strip() == r.stdout.strip(), \
            "result 文件与 stdout 不一致"
    return r.rc, r.result, r.ledger_path, r.result_path


def main():
    f = dispatch.find_session_start_trigger

    # 1 纯函数：命中
    for skill in SESSION_START_SKILLS:
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

    # 2 触发＋none＋turn>0：台账前拒发，id 不消耗
    with tempfile.TemporaryDirectory() as td:
        w = _world(turn=5)
        rc, res, lp, rp = run_case("/wayfinder #7", None, "disc-1", td, w)
        assert rc == 2, rc
        assert not res["ok"] and res["error"]["kind"] == "reset_required", res
        msg = res["error"]["message"]
        assert "/wayfinder" in msg and "第 1 行" in msg and "turn=5" in msg, msg
        assert res["error"]["hints"], res
        assert w.sends == [], w.sends                    # 零发送
        assert not lp.exists(), "拒发不应写台账"
        assert rp.exists(), "拒发也要写 result.json"
        assert "reset" not in res                          # reset=none 不带 reset 键
        print("2 trigger + none + turn>0: refused before ledger, rc=2, id unconsumed")

        # 3 同 id 补 --reset clear 重试成功
        w2 = _world()
        rc, res, lp, rp = run_case("/wayfinder #7", "clear", "disc-1", td, w2)
        assert rc == 0 and res["ok"], res
        assert w2.sends == [("t99", "/clear", True), ("t99", "/wayfinder #7", True)], w2.sends
        assert res["reset"] == "clear"
        assert lp.exists()
        print("3 same release id retry with --reset clear: ok")

    # 4 new/clear/compact 都放行
    for i, mode in enumerate(("new", "clear", "compact")):
        with tempfile.TemporaryDirectory() as td:
            w = _world()
            rc, res, lp, _ = run_case("/implement #9", mode, f"disc-4{mode}", td, w)
            assert rc == 0 and res["ok"], (mode, res)
            assert w.sends == [("t99", "/" + mode, True), ("t99", "/implement #9", True)], w.sends
    print("4 trigger + new/clear/compact: all accepted")

    # 5 非触发＋不传 --reset：照常单发
    with tempfile.TemporaryDirectory() as td:
        w = _world()
        p = "这几个问题答完再跑 /implement，先给结论"
        rc, res, _, _ = run_case(p, None, "disc-5", td, w)
        assert rc == 0 and res["ok"], res
        assert w.sends == [("t99", p, True)], w.sends
    print("5 mid-line mention + no reset: single send, backward compatible")

    # 6 duplicate_release 优先
    with tempfile.TemporaryDirectory() as td:
        w = _world()
        rc, _, lp, _ = run_case(ANCHOR_SRC, None, "disc-6", td, w)
        assert rc == 0 and lp.exists()
        w2 = _world()
        rc, res, _, _ = run_case("/wayfinder #7", None, "disc-6", td, w2)
        assert rc == 1 and res["error"]["kind"] == "duplicate_release", res
        assert w2.sends == []
    print("6 duplicate_release takes precedence over reset_required")

    # 7 turn == 0 自动豁免：全新空会话不传 --reset 直接发
    with tempfile.TemporaryDirectory() as td:
        w = _world(turn=0)
        rc, res, lp, _ = run_case("/wayfinder #7", None, "disc-7", td, w)
        assert rc == 0 and res["ok"], res
        assert w.sends == [("t99", "/wayfinder #7", True)], w.sends
        assert res["reset"] == "none"
        assert lp.exists()
    print("7 turn==0 + none: exempted, single send ok")

    # 8 turn == 0 但显式传 --reset new：仍按显式参数执行前置
    with tempfile.TemporaryDirectory() as td:
        w = _world(turn=0)
        rc, res, _, _ = run_case("/wayfinder #7", "new", "disc-8", td, w)
        assert rc == 0 and res["ok"], res
        assert w.sends == [("t99", "/new", True), ("t99", "/wayfinder #7", True)], w.sends
        assert res["reset"] == "new"
    print("8 turn==0 + explicit --reset new: still sends /new first")

    # 9 turn 缺失或布尔 False：不豁免，仍拒发 reset_required（turn 未知）
    for idx, bad_turn in enumerate((None, False)):
        with tempfile.TemporaryDirectory() as td:
            w = _world(turn=bad_turn)
            rc, res, lp, _ = run_case("/wayfinder #7", None, f"disc-9-{idx}", td, w)
            assert rc == 2, (bad_turn, rc)
            assert res["error"]["kind"] == "reset_required", res
            assert "turn 未知" in res["error"]["message"], res
            assert w.sends == []
            assert not lp.exists()
    print("9 missing/bool turn + none: still refused (turn 未知)")

    # 10 working 状态时 not_idle 先于 reset_required
    with tempfile.TemporaryDirectory() as td:
        w = _world(turn=5, status="working")
        rc, res, lp, _ = run_case("/wayfinder #7", None, "disc-10", td, w)
        assert rc == 1 and res["error"]["kind"] == "not_idle", res
        assert w.sends == []
        assert not lp.exists()
    print("10 working state: not_idle takes precedence over reset_required")

    print("ALL OK")


if __name__ == "__main__":
    main()
