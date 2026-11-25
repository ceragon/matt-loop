#!/usr/bin/env python3
"""test_dispatch_split_required.py — 会话起始 skill 命令行形态闸测试（ADR-0017，#61，mock hc/pi）。

跑法：python3 loop/scripts/test_dispatch_split_required.py
覆盖（14 例，只钉外部行为：退出码/stdout/result.json 形状/台账/mock hc 调用数）：
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


ANCHOR_SRC = "请实现某个功能"
SCREEN = ANCHOR_SRC + "\n已完成该功能。"
EXT = {"needs_user": False, "questions": [],
       "conclusion": "", "blocked": "", "reply": SCREEN}


class FakeHc:
    """discipline 版 FakeHc＋全调用记录（含 get，证零副作用）＋not_found 开关（新鲜路径）。"""

    def __init__(self, turn=5, state="idle", not_found=False):
        self.sends = []          # (text, if_idle)
        self.calls = []          # 每次 hc 调用的完整 args（含 get）
        self.turn = turn
        self.state = state
        self.screen = SCREEN
        self.not_found = not_found

    def __call__(self, hc_bin, args, timeout_s):
        self.calls.append(list(args))
        cmd = args[0]
        if cmd == "get":
            if self.not_found:
                raise dispatch.HcFailure("not_found", "无此终端", [], 1, None)
            g = {"state": "live", "title": "claude-test",
                 "address": "t99", "name": "claude-test",
                 "labels": {"loop": "1"},
                 "status": {"state": self.state, "title": "claude-test"},
                 "foreground": {"name": "claude", "argv": ["claude"]}}
            if self.turn is not None:
                g["turn"] = self.turn
            return g
        if cmd == "send":
            text = args[-1]
            self.sends.append((text, "--if" in args))
            baseline = self.turn if isinstance(self.turn, int) and not isinstance(self.turn, bool) else 0
            self.turn = baseline + 1
            return {"turn": baseline}
        if cmd == "read":
            return {"text": self.screen, "complete": True, "how": "current", "why": ""}
        if cmd == "wait":
            return {"reason": "status", "status": self.state, "turn": self.turn}
        raise AssertionError("unexpected hc cmd: " + cmd)


def run_case(prompt, td, fake, rid, reset=None, launch=None, cwd=None,
             display_name=None, target="t99"):
    """在给定临时目录里跑一轮 dispatch；返回 (rc, result, ledger_path, result_path)。"""
    pf = Path(td) / "prompt.md"
    pf.write_text(prompt, encoding="utf-8")
    ld = Path(td) / ".loop"
    argv = ["--target", target, "--release-id", rid,
            "--prompt-file", str(pf), "--loop-dir", str(ld),
            "--space", "main",
            "--wall-clock", "5", "--check-in", "1",
            "--settle-seconds", "0.01", "--settle-checks", "2"]
    if reset is not None:
        argv += ["--reset", reset]
    if launch is not None:
        argv += ["--launch", launch]
    if cwd is not None:
        argv += ["--cwd", cwd]
    if display_name is not None:
        argv += ["--display-name", display_name]
    dispatch.hc_json = fake
    dispatch.app_json = FakeApp()
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
        fake = FakeHc(turn=5)
        rc, res, lp, rp = run_case("/wayfinder #7\n请绘图", td, fake, "split-2")
        assert rc == 2, rc
        assert not res["ok"] and res["error"]["kind"] == "split_required", res
        msg = res["error"]["message"]
        assert "/wayfinder" in msg and "第 1 行" in msg and "含内部换行" in msg, msg
        assert res["error"]["hints"], res
        assert fake.calls == [], fake.calls                # 零 hc 调用（含 get）
        assert fake.sends == [], fake.sends                # 零发送
        assert not lp.exists(), "拒发不应写台账"
        assert rp.exists(), "拒发也要写 result.json"
        print("2 trigger + multiline <=700: refused before any hc call, rc=2, id unconsumed")

    # 3 触发＋单行 701 码元：体积原因拒发（单行长参数无豁免）
    with tempfile.TemporaryDirectory() as td:
        fake = FakeHc(turn=0)
        rc, res, lp, _ = run_case("/wayfinder " + "x" * 690, td, fake, "split-3")
        assert rc == 2 and res["error"]["kind"] == "split_required", res
        msg = res["error"]["message"]
        assert "/wayfinder" in msg and "701" in msg and "700" in msg, msg
        assert "含内部换行" not in msg, msg               # 单行只报体积原因
        assert fake.calls == [] and not lp.exists()
        print("3 trigger + single-line 701 units: refused with size reason")

    # 4 边界：700 过、701 拒；tab 膨胀（690 字符＋3 tab→归一化 702）拒
    with tempfile.TemporaryDirectory() as td:
        fake = FakeHc(turn=0)
        p = "/wayfinder " + "x" * 689                    # 11+689=700
        rc, res, lp, _ = run_case(p, td, fake, "split-4a")
        assert rc == 0 and res["ok"], res
        assert fake.sends == [(p, True)] and lp.exists()
    with tempfile.TemporaryDirectory() as td:
        fake = FakeHc(turn=0)
        rc, res, lp, _ = run_case("/wayfinder " + "x" * 690, td, fake, "split-4b")
        assert rc == 2 and res["error"]["kind"] == "split_required", res
        assert fake.calls == [] and not lp.exists()
    with tempfile.TemporaryDirectory() as td:
        fake = FakeHc(turn=0)
        rc, res, lp, _ = run_case("/wayfinder " + "x" * 679 + "\t\t\t", td, fake, "split-4c")
        assert rc == 2 and res["error"]["kind"] == "split_required", res
        assert "702" in res["error"]["message"], res     # tab→4 空格后 690+12=702
        assert fake.calls == [] and not lp.exists()
    print("4 boundary: 700 pass, 701 refuse, tab-expanded 702 refuse")

    # 5 触发＋单行 ≤700 带短参：单发直过
    with tempfile.TemporaryDirectory() as td:
        fake = FakeHc(turn=0)
        rc, res, lp, _ = run_case("/wayfinder #7", td, fake, "split-5")
        assert rc == 0 and res["ok"], res
        assert fake.sends == [("/wayfinder #7", True)], fake.sends
        assert lp.exists()
        print("5 trigger + single-line short arg: single send ok")

    # 6 尾换行豁免：＋\n 过、＋\n\n 过、＋内部正文行拒
    for suffix, ok_expected in (("\n", True), ("\n\n", True), ("\n正文", False)):
        with tempfile.TemporaryDirectory() as td:
            fake = FakeHc(turn=0)
            p = "/wayfinder #7" + suffix
            rc, res, lp, _ = run_case(p, td, fake, "split-6-" + str(len(suffix)))
            if ok_expected:
                assert rc == 0 and res["ok"], (suffix, res)
                assert fake.sends == [(p, True)], fake.sends   # 原文照发（含尾换行）
                assert lp.exists()
            else:
                assert rc == 2 and res["error"]["kind"] == "split_required", (suffix, res)
                assert fake.calls == [] and not lp.exists()
    print("6 trailing newlines exempt (\\n, \\n\\n); internal body line refused")

    # 7 命令在第 5 行（正文在前）：任意行首口径，行号=5
    with tempfile.TemporaryDirectory() as td:
        fake = FakeHc(turn=0)
        p = "第一行正文\n第二行正文\n第三行正文\n第四行正文\n/grill-with-docs #61"
        rc, res, lp, _ = run_case(p, td, fake, "split-7")
        assert rc == 2 and res["error"]["kind"] == "split_required", res
        msg = res["error"]["message"]
        assert "/grill-with-docs" in msg and "第 5 行" in msg, msg
        assert fake.calls == [] and not lp.exists()
        print("7 command on line 5 (body first): refused, lineno=5")

    # 8 行中提及＋超体积多行：不触发、照常发（turn=5 亦过，向后兼容）
    with tempfile.TemporaryDirectory() as td:
        fake = FakeHc(turn=5)
        p = "先讨论一下 /wayfinder 的方案\n" + "长" * 800 + "\n再补一行说明"
        rc, res, lp, _ = run_case(p, td, fake, "split-8")
        assert rc == 0 and res["ok"], res
        assert fake.sends == [(p, True)], fake.sends     # 原文照发，不归一化
        assert lp.exists()
        print("8 mid-line mention + oversized multiline: not triggered, sent as-is")

    # 9 双命中（turn>0＋reset=none＋多行触发）：split_required 先于 reset_required
    with tempfile.TemporaryDirectory() as td:
        fake = FakeHc(turn=5)
        rc, res, lp, _ = run_case("/wayfinder #7\n请绘图", td, fake, "split-9")
        assert rc == 2 and res["error"]["kind"] == "split_required", res
        assert fake.calls == [] and not lp.exists()
        print("9 double hit: split_required reported before reset_required")

    # 10 duplicate_release 优先于 split_required
    with tempfile.TemporaryDirectory() as td:
        fake = FakeHc()
        rc, _, lp, _ = run_case(ANCHOR_SRC, td, fake, "split-10")
        assert rc == 0 and lp.exists()
        fake2 = FakeHc()
        rc, res, _, _ = run_case("/wayfinder #7\n请绘图", td, fake2, "split-10")
        assert rc == 1 and res["error"]["kind"] == "duplicate_release", res
        assert fake2.calls == []
        print("10 duplicate_release takes precedence over split_required")

    # 11 拆轮同 id 重试：拒后改文件为单行命令、同 id 成功（id 未被消耗）
    with tempfile.TemporaryDirectory() as td:
        fake = FakeHc(turn=0)
        rc, res, lp, _ = run_case("/wayfinder #7\n请绘图", td, fake, "split-11")
        assert rc == 2 and res["error"]["kind"] == "split_required", res
        assert not lp.exists()
        fake2 = FakeHc(turn=0)
        rc, res, lp, _ = run_case("/wayfinder #7", td, fake2, "split-11")
        assert rc == 0 and res["ok"], res
        assert fake2.sends == [("/wayfinder #7", True)], fake2.sends
        assert lp.exists()
        print("11 same-id retry with single-line command file: ok")

    # 12 --reset clear＋多行：重置不豁免形态闸，仍拒 split_required
    with tempfile.TemporaryDirectory() as td:
        fake = FakeHc(turn=5)
        rc, res, lp, _ = run_case("/wayfinder #7\n请绘图", td, fake, "split-12", reset="clear")
        assert rc == 2 and res["error"]["kind"] == "split_required", res
        assert res["reset"] == "clear" and res["error"]["reset_sent"] is False, res
        assert fake.calls == [] and not lp.exists()
        print("12 --reset clear + multiline: still refused (reset does not exempt)")

    # 13 新鲜路径（not_found＋launch/cwd/display_name 齐）＋多行：仍拒、零 hc 调用
    with tempfile.TemporaryDirectory() as td:
        fake = FakeHc(not_found=True)
        rc, res, lp, _ = run_case("/wayfinder #7\n请绘图", td, fake, "split-13",
                                  launch="claude", cwd="/proj", display_name="Bot_测试")
        assert rc == 2 and res["error"]["kind"] == "split_required", res
        assert fake.calls == [], fake.calls              # 连 get 都没发，创建/领养未触发
        assert not lp.exists()
        print("13 fresh path (not_found + launch params) + multiline: still refused, zero hc calls")

    # 14 emoji 体积（用户裁定 2）：纯函数整 350/351；端到端不是整 350——最短触发命令
    #    /wayfinder 占 10 码元，345×2+10=700 过、346×2+10=702 拒。
    assert dispatch.utf16_units("🎉" * 350) == 700
    assert dispatch.utf16_units("🎉" * 351) == 702
    with tempfile.TemporaryDirectory() as td:
        fake = FakeHc(turn=0)
        p = "/wayfinder" + "🎉" * 345                    # 10+690=700
        rc, res, lp, _ = run_case(p, td, fake, "split-14a")
        assert rc == 0 and res["ok"], res
        assert fake.sends == [(p, True)] and lp.exists()
    with tempfile.TemporaryDirectory() as td:
        fake = FakeHc(turn=0)
        rc, res, lp, _ = run_case("/wayfinder" + "🎉" * 346, td, fake, "split-14b")
        assert rc == 2 and res["error"]["kind"] == "split_required", res
        assert "702" in res["error"]["message"], res
        assert fake.calls == [] and not lp.exists()
    print("14 emoji sizing: 350=700 / 351=702 pure; e2e 345 pass, 346 refuse")

    print("ALL OK")


if __name__ == "__main__":
    main()