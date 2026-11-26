#!/usr/bin/env python3
"""test_dispatch_anchor_path.py — 锚点降级＋工具路径探测测试（FakeHerdr 假世界，不打真实 agent）。

跑法：python3 tests/test_dispatch_anchor_path.py
覆盖：
  1 锚点在屏、无折叠 → ok，complete:true，anchor_found:true。
  2 锚点滚出屏、无折叠 → ok，complete:true，anchor_found:false（修复：不再误判）。
  3 折叠命中 → ok，complete:false（标记不阻断，ADR-0019）。
  4 锚点缺失＋折叠 → 标记与 anchor_found:false 并存。
  5 find_tool：which 命中直接用；which 失效探到可执行文件；非可执行/空目录 → None。

T5a（ADR-0021，#99）：完整轮用例（1–4）迁 FakeHerdr＋harness.run（唯一 CLI 缝）；
find_tool 探测（5）不吃 Deps，保留直调，替换手法改对宿主模块 shutil 的作用域
mock.patch.object（退出无条件恢复，ADR-0016 决定 7 残留洞纪律）。
R（#104）：共享常量与 world/argv 构造器迁 helpers 单一出处。
"""
import shutil
import tempfile
from pathlib import Path
from unittest import mock

import harness
from harness import dispatch
from helpers import ANCHOR, world, argv

def run_round(screen, rid):
    """reply 取回读全文（无旧轮时本轮段＝全文，保证逐字校验过），聚焦完整性门而非抽取质量。"""
    w = world(screen)
    with tempfile.TemporaryDirectory() as td:
        r = harness.run(argv(td, rid), world=w,
                        extract=lambda pi_bin, args, turn_path: {
                            "needs_user": False, "questions": [],
                            "conclusion": "", "blocked": "", "reply": screen})
        return r.rc, r.result, r.ledger


def main():
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

    # 3 折叠命中 → 标记不阻断（ADR-0019）
    rc, res, led = run_round(ANCHOR + "\n回复\n… +36 lines", "ap-fold")
    assert rc == 0 and res["ok"], res
    assert res["complete"] is False and res["complete_reason"] == ["fold_markers"], res
    assert led["complete"] is False
    print("3 fold marker: complete=false flag, no block ok")

    # 4 锚点缺失＋折叠 → 标记与 anchor_found:false 并存
    rc, res, led = run_round("回复\n… +36 lines", "ap-fold-noanchor")
    assert rc == 0 and res["ok"] and res["complete"] is False, res
    assert res["anchor_found"] is False
    print("4 anchor gone + fold: flag without block ok")

    # 5 find_tool（单元探针：对宿主模块 shutil 的作用域替换，退出无条件恢复）
    with mock.patch.object(shutil, "which", side_effect=lambda name: "/usr/bin/" + name):
        assert dispatch.find_tool("pi") == "/usr/bin/pi"
    with tempfile.TemporaryDirectory() as td:
        tool = Path(td) / "pi"
        tool.write_text("#!/bin/sh\n", encoding="utf-8")
        tool.chmod(0o755)
        with mock.patch.object(shutil, "which", return_value=None):
            assert dispatch.find_tool("pi", probe_dirs=[td]) == str(tool)
            tool.chmod(0o644)  # 非可执行 → 不认
            assert dispatch.find_tool("pi", probe_dirs=[td]) is None
            assert dispatch.find_tool("pi", probe_dirs=[]) is None
    print("5 find_tool: which-first, probe executable, skip non-exec/empty ok")

    print("all anchor/path tests passed")


if __name__ == "__main__":
    main()
