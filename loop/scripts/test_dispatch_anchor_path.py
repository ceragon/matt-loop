#!/usr/bin/env python3
"""test_dispatch_anchor_path.py — 锚点降级＋工具路径探测测试（mock hc/pi，不打真实 agent）。

跑法：python3.12 loop/scripts/test_dispatch_anchor_path.py
覆盖：
  1 锚点在屏、无折叠 → ok，complete:true，anchor_found:true。
  2 锚点滚出屏、无折叠 → ok，complete:true，anchor_found:false（修复：不再误判）。
  3 有折叠标记 ▲ → error(incomplete_result)，complete:false（折叠仍硬阻断）。
  4 锚点缺失＋折叠 → error（折叠阻断）。
  5 find_tool：which 命中直接用；which 失效探到可执行文件；非可执行/空目录 → None。
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

from test_dispatch_reset import PROMPT, ANCHOR, FakeHc, FakeApp  # noqa: E402

REAL_WHICH = dispatch.shutil.which


def run_round(screen, rid):
    """reply 取整屏（保证逐字校验过），聚焦完整性门而非抽取质量。"""
    with tempfile.TemporaryDirectory() as td:
        pf = Path(td) / "prompt.md"
        pf.write_text(PROMPT, encoding="utf-8")
        ld = Path(td) / ".loop"
        argv = ["--target", "t99", "--release-id", rid,
                "--prompt-file", str(pf), "--loop-dir", str(ld),
                "--space", "main",
                "--wall-clock", "5", "--check-in", "1",
                "--settle-seconds", "0.01", "--settle-checks", "2"]
        fake = FakeHc()
        fake.screen = screen
        dispatch.hc_json = fake
        dispatch.app_json = FakeApp()
        dispatch.extract = lambda pi_bin, args, raw_path: {
            "needs_user": False, "questions": [],
            "conclusion": "", "blocked": "", "reply": screen}
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
    try:
        # 1 锚点在屏、无折叠
        rc, res, led = run_round(ANCHOR + "\n功能已完成。", "ap-anchor-present")
        assert rc == 0 and res["ok"] and res["complete"], res
        assert res["anchor_found"] is True and led["anchor_found"] is True
        assert res["complete_reason"] == []
        print("1 anchor present, no fold: ok, anchor_found=True ok")

        # 2 锚点滚出屏、无折叠 → 不再误判（修复核心）
        rc, res, led = run_round("功能已完成，验收通过。", "ap-anchor-gone")
        assert rc == 0 and res["ok"] and res["complete"], res
        assert res["anchor_found"] is False and led["anchor_found"] is False
        assert res["complete_reason"] == []
        print("2 anchor scrolled off, no fold: ok, anchor_found=False (no false error) ok")

        # 3 折叠标记仍硬阻断
        rc, res, led = run_round(ANCHOR + "\n回复▲折叠内容", "ap-fold")
        assert rc == 1 and not res["ok"], res
        assert res["error"]["kind"] == "incomplete_result", res
        assert "fold_markers" in res["error"]["message"]
        print("3 fold marker: still error(incomplete_result) ok")

        # 4 锚点缺失＋折叠 → 折叠阻断
        rc, res, led = run_round("回复▼折叠内容", "ap-fold-noanchor")
        assert rc == 1 and res["error"]["kind"] == "incomplete_result", res
        print("4 anchor gone + fold: fold still blocks ok")

        # 5 find_tool
        dispatch.shutil.which = lambda name: "/usr/bin/" + name
        assert dispatch.find_tool("pi") == "/usr/bin/pi"
        with tempfile.TemporaryDirectory() as td:
            tool = Path(td) / "pi"
            tool.write_text("#!/bin/sh\n", encoding="utf-8")
            tool.chmod(0o755)
            dispatch.shutil.which = lambda name: None
            assert dispatch.find_tool("pi", probe_dirs=[td]) == str(tool)
            tool.chmod(0o644)  # 非可执行 → 不认
            assert dispatch.find_tool("pi", probe_dirs=[td]) is None
            assert dispatch.find_tool("pi", probe_dirs=[]) is None
        print("5 find_tool: which-first, probe executable, skip non-exec/empty ok")
    finally:
        dispatch.shutil.which = REAL_WHICH

    print("all anchor/path tests passed")


if __name__ == "__main__":
    main()