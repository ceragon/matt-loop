#!/usr/bin/env python3
"""test_dispatch_split_required.py — 会话起始 skill 命令行形态闸测试（ADR-0017，#61，FakeHerdr 假世界）。

跑法：python3 tests/test_dispatch_split_required.py
覆盖（14 例，只钉外部行为：退出码/stdout/result.json 形状/台账/假世界日志）：
  1 纯函数计量：normalize_paste（CRLF/CR→LF、tab→4 空格）、utf16_units（中文 1/emoji 2）、
    has_internal_newline（尾换行不算多行、内部空行算）。
  2 触发＋多行 ≤700 → 拒：rc=2、kind=split_required、message 带 skill/行号/换行原因、
    hc 调用数 0（含 get）、台账不存在（id 不消耗）、result.json 与 stdout 逐字一致。
  3 触发＋单行 701 码元 → 拒（体积原因）。
  4 边界：700 过、701 拒；tab 膨胀越界（690 字符＋3 tab＝归一化 702 拒）。
  5 触发＋单行 ≤700 带短参 → 单发直过。
  6 尾换行：单行命令＋\\n 过、＋\\n\\n 过、＋内部正文行拒。
  7 命令在第 5 行（正文在前）＋多行 → 拒，行号=5。
  8 行中提及＋超体积多行 → 不触发、照常发（turn=5 亦过）。
  9 双命中（turn>0＋none＋多行）→ 报 split_required 非 reset_required。
 10 duplicate_release 优先于 split_required。
 11 拆轮同 id 重试：改文件为单行命令、同 id 成功。
 12 --reset clear＋多行 → 仍拒 split_required。
 13 新鲜路径（not_found＋launch/cwd/display_name 齐）＋多行 → 仍拒、零 hc 调用。
 14 emoji 体积：纯函数 350 个＝700、351 个＝702；端到端见例内注释。

T5a（ADR-0021，#99）：纯函数计量（1、14 前半）保留原位；完整轮用例迁 FakeHerdr＋
harness.run（唯一 CLI 缝），零 hc 调用断言用假世界日志 w.hc_calls；not_found 场景
＝世界里没有该终端。常量与助手本文件自带，不 import 供应方文件。
"""
import tempfile
from pathlib import Path

import harness
from harness import dispatch
from fake_herdr import FakeHerdr

ANCHOR_SRC = "请实现某个功能"
SCREEN = ANCHOR_SRC + "\n已完成该功能。"
EXT = {"needs_user": False, "questions": [],
       "conclusion": "", "blocked": "", "reply": SCREEN}


def _world(turn=5, not_found=False):
    """既有 agent t99＋main 空间 pane 已在＋稳定屏幕；not_found＝世界里没有该终端。"""
    w = FakeHerdr()
    sp = w.add_space(name="main")
    if not not_found:
        w.add_terminal(address="t99", name="claude-test", labels={"loop": "1"}, turn=turn)
        w.add_tab(sp, terminals=["t99"])
        w.screen("t99", SCREEN)
    return w


def _ext(pi_bin, args, turn_path):
    return dict(EXT)


def run_case(prompt, td, w, rid, reset=None, launch=None, cwd=None,
             display_name=None, target="t99"):
    """在给定临时目录里跑一轮 dispatch；返回 (rc, result, ledger_path, result_path)。"""
    pf = Path(td) / "prompt.md"
    pf.write_text(prompt, encoding="utf-8")
    argv = ["--target", target, "--release-id", rid,
            "--prompt-file", str(pf), "--loop-dir", str(Path(td) / ".loop"),
            "--space", "main", "--wall-clock", "5", "--check-in", "1",
            "--settle-seconds", "0.01", "--settle-checks", "2"]
    if reset is not None:
        argv += ["--reset", reset]
    if launch is not None:
        argv += ["--launch", launch]
    if cwd is not None:
        argv += ["--cwd", cwd]
    if display_name is not None:
        argv += ["--display-name", display_name]
    r = harness.run(argv, world=w, extract=_ext)
    if r.result_path.exists():
        assert r.result_path.read_text(encoding="utf-8").strip() == r.stdout.strip(), \
            "result 文件与 stdout 不一致"
    return r.rc, r.result, r.ledger_path, r.result_path


def main():
    # 1 纯函数计量：归一化、UTF-16 码元、内部换行判定
    assert dispatch.normalize_paste("a\r\nb\rc\td") == "a\nb\nc    d"
    assert dispatch.normalize_paste("x\t\ty\r\n") == "x        y\n"
    assert dispatch.utf16_units("abc") == 3
    assert dispatch.utf16_units("中文") == 2          # 中文 1 码元/字
    assert dispatch.utf16_units("🎉") == 2            # emoji 2 码元（代理对）
    assert dispatch.utf16_units("中🎉a") == 4
    assert dispatch.has_internal_newline("cmd") is False
    assert dispatch.has_internal_newline("cmd\n") is False       # 尾换行豁免
    assert dispatch.has_internal_newline("cmd\n\n\n") is False   # 连续尾换行也豁免
    assert dispatch.has_internal_newline("cmd\nbody") is True
    assert dispatch.has_internal_newline("cmd\n\nbody\n") is True  # 内部空行算
    print("1 pure metering: normalize_paste / utf16_units / has_internal_newline ok")

    # 2 触发＋多行 ≤700：任何目标交互之前拒发，零副作用、id 不消耗
    with tempfile.TemporaryDirectory() as td:
        w = _world(turn=5)
        rc, res, lp, rp = run_case("/wayfinder #7\n请绘图", td, w, "split-2")
        assert rc == 2, rc
        assert not res["ok"] and res["error"]["kind"] == "split_required", res
        msg = res["error"]["message"]
        assert "/wayfinder" in msg and "第 1 行" in msg and "含内部换行" in msg, msg
        assert res["error"]["hints"], res
        assert w.hc_calls == [], w.hc_calls              # 零 hc 调用（含 get）
        assert w.sends == [], w.sends                    # 零发送
        assert not lp.exists(), "拒发不应写台账"
        assert rp.exists(), "拒发也要写 result.json"
        print("2 trigger + multiline <=700: refused before any hc call, rc=2, id unconsumed")

    # 3 触发＋单行 701 码元：体积原因拒发（单行长参数无豁免）
    with tempfile.TemporaryDirectory() as td:
        w = _world(turn=0)
        rc, res, lp, _ = run_case("/wayfinder " + "x" * 690, td, w, "split-3")
        assert rc == 2 and res["error"]["kind"] == "split_required", res
        msg = res["error"]["message"]
        assert "/wayfinder" in msg and "701" in msg and "700" in msg, msg
        assert "含内部换行" not in msg, msg               # 单行只报体积原因
        assert w.hc_calls == [] and not lp.exists()
        print("3 trigger + single-line 701 units: refused with size reason")

    # 4 边界：700 过、701 拒；tab 膨胀（690 字符＋3 tab→归一化 702）拒
    with tempfile.TemporaryDirectory() as td:
        w = _world(turn=0)
        p = "/wayfinder " + "x" * 689                    # 11+689=700
        rc, res, lp, _ = run_case(p, td, w, "split-4a")
        assert rc == 0 and res["ok"], res
        assert w.sends == [("t99", p, True)] and lp.exists()
    with tempfile.TemporaryDirectory() as td:
        w = _world(turn=0)
        rc, res, lp, _ = run_case("/wayfinder " + "x" * 690, td, w, "split-4b")
        assert rc == 2 and res["error"]["kind"] == "split_required", res
        assert w.hc_calls == [] and not lp.exists()
    with tempfile.TemporaryDirectory() as td:
        w = _world(turn=0)
        rc, res, lp, _ = run_case("/wayfinder " + "x" * 679 + "\t\t\t", td, w, "split-4c")
        assert rc == 2 and res["error"]["kind"] == "split_required", res
        assert "702" in res["error"]["message"], res     # tab→4 空格后 690+12=702
        assert w.hc_calls == [] and not lp.exists()
    print("4 boundary: 700 pass, 701 refuse, tab-expanded 702 refuse")

    # 5 触发＋单行 ≤700 带短参：单发直过
    with tempfile.TemporaryDirectory() as td:
        w = _world(turn=0)
        rc, res, lp, _ = run_case("/wayfinder #7", td, w, "split-5")
        assert rc == 0 and res["ok"], res
        assert w.sends == [("t99", "/wayfinder #7", True)], w.sends
        assert lp.exists()
        print("5 trigger + single-line short arg: single send ok")

    # 6 尾换行豁免：＋\n 过、＋\n\n 过、＋内部正文行拒
    for suffix, ok_expected in (("\n", True), ("\n\n", True), ("\n正文", False)):
        with tempfile.TemporaryDirectory() as td:
            w = _world(turn=0)
            p = "/wayfinder #7" + suffix
            rc, res, lp, _ = run_case(p, td, w, "split-6-" + str(len(suffix)))
            if ok_expected:
                assert rc == 0 and res["ok"], (suffix, res)
                assert w.sends == [("t99", p, True)], w.sends   # 原文照发（含尾换行）
                assert lp.exists()
            else:
                assert rc == 2 and res["error"]["kind"] == "split_required", (suffix, res)
                assert w.hc_calls == [] and not lp.exists()
    print("6 trailing newlines exempt (\\n, \\n\\n); internal body line refused")

    # 7 命令在第 5 行（正文在前）：任意行首口径，行号=5
    with tempfile.TemporaryDirectory() as td:
        w = _world(turn=0)
        p = "第一行正文\n第二行正文\n第三行正文\n第四行正文\n/grill-with-docs #61"
        rc, res, lp, _ = run_case(p, td, w, "split-7")
        assert rc == 2 and res["error"]["kind"] == "split_required", res
        msg = res["error"]["message"]
        assert "/grill-with-docs" in msg and "第 5 行" in msg, msg
        assert w.hc_calls == [] and not lp.exists()
        print("7 command on line 5 (body first): refused, lineno=5")

    # 8 行中提及＋超体积多行：不触发、照常发（turn=5 亦过，向后兼容）
    with tempfile.TemporaryDirectory() as td:
        w = _world(turn=5)
        p = "先讨论一下 /wayfinder 的方案\n" + "长" * 800 + "\n再补一行说明"
        rc, res, lp, _ = run_case(p, td, w, "split-8")
        assert rc == 0 and res["ok"], res
        assert w.sends == [("t99", p, True)], w.sends     # 原文照发，不归一化
        assert lp.exists()
        print("8 mid-line mention + oversized multiline: not triggered, sent as-is")

    # 9 双命中（turn>0＋reset=none＋多行触发）：split_required 先于 reset_required
    with tempfile.TemporaryDirectory() as td:
        w = _world(turn=5)
        rc, res, lp, _ = run_case("/wayfinder #7\n请绘图", td, w, "split-9")
        assert rc == 2 and res["error"]["kind"] == "split_required", res
        assert w.hc_calls == [] and not lp.exists()
        print("9 double hit: split_required reported before reset_required")

    # 10 duplicate_release 优先于 split_required
    with tempfile.TemporaryDirectory() as td:
        w = _world()
        rc, _, lp, _ = run_case(ANCHOR_SRC, td, w, "split-10")
        assert rc == 0 and lp.exists()
        w2 = _world()
        rc, res, _, _ = run_case("/wayfinder #7\n请绘图", td, w2, "split-10")
        assert rc == 1 and res["error"]["kind"] == "duplicate_release", res
        assert w2.hc_calls == []
        print("10 duplicate_release takes precedence over split_required")

    # 11 拆轮同 id 重试：拒后改文件为单行命令、同 id 成功（id 未被消耗）
    with tempfile.TemporaryDirectory() as td:
        w = _world(turn=0)
        rc, res, lp, _ = run_case("/wayfinder #7\n请绘图", td, w, "split-11")
        assert rc == 2 and res["error"]["kind"] == "split_required", res
        assert not lp.exists()
        w2 = _world(turn=0)
        rc, res, lp, _ = run_case("/wayfinder #7", td, w2, "split-11")
        assert rc == 0 and res["ok"], res
        assert w2.sends == [("t99", "/wayfinder #7", True)], w2.sends
        assert lp.exists()
        print("11 same-id retry with single-line command file: ok")

    # 12 --reset clear＋多行：重置不豁免形态闸，仍拒 split_required
    with tempfile.TemporaryDirectory() as td:
        w = _world(turn=5)
        rc, res, lp, _ = run_case("/wayfinder #7\n请绘图", td, w, "split-12", reset="clear")
        assert rc == 2 and res["error"]["kind"] == "split_required", res
        assert res["reset"] == "clear" and res["error"]["reset_sent"] is False, res
        assert w.hc_calls == [] and not lp.exists()
        print("12 --reset clear + multiline: still refused (reset does not exempt)")

    # 13 新鲜路径（not_found＋launch/cwd/display_name 齐）＋多行：仍拒、零 hc 调用
    with tempfile.TemporaryDirectory() as td:
        w = _world(not_found=True)
        rc, res, lp, _ = run_case("/wayfinder #7\n请绘图", td, w, "split-13",
                                  launch="claude", cwd="/proj", display_name="Bot_测试")
        assert rc == 2 and res["error"]["kind"] == "split_required", res
        assert w.hc_calls == [], w.hc_calls              # 连 get 都没发，创建/领养未触发
        assert not lp.exists()
        print("13 fresh path (not_found + launch params) + multiline: still refused, zero hc calls")

    # 14 emoji 体积（用户裁定 2）：纯函数整 350/351；端到端不是整 350——最短触发命令
    #    /wayfinder 占 10 码元，345×2+10=700 过、346×2+10=702 拒。
    assert dispatch.utf16_units("🎉" * 350) == 700
    assert dispatch.utf16_units("🎉" * 351) == 702
    with tempfile.TemporaryDirectory() as td:
        w = _world(turn=0)
        p = "/wayfinder" + "🎉" * 345                    # 10+690=700
        rc, res, lp, _ = run_case(p, td, w, "split-14a")
        assert rc == 0 and res["ok"], res
        assert w.sends == [("t99", p, True)] and lp.exists()
    with tempfile.TemporaryDirectory() as td:
        w = _world(turn=0)
        rc, res, lp, _ = run_case("/wayfinder" + "🎉" * 346, td, w, "split-14b")
        assert rc == 2 and res["error"]["kind"] == "split_required", res
        assert "702" in res["error"]["message"], res
        assert w.hc_calls == [] and not lp.exists()
    print("14 emoji sizing: 350=700 / 351=702 pure; e2e 345 pass, 346 refuse")

    print("ALL OK")


if __name__ == "__main__":
    main()
