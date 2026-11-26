#!/usr/bin/env python3
"""test_deploy_smoke.py — 部署冒烟门（ADR-0021 决定 7）。

跑法：python3 tests/test_deploy_smoke.py
部署单元是 `loop/` 整目录（脚本须整目录同步）：dispatch.py 的兄弟模块必须随行。
本守卫把 loop/ 整目录拷到临时位置，在那里跑分发脚本 `--help`，必须成功——
漏拷兄弟模块在 CI 就红，不到副本上才炸。

自证：另拷一份、故意删掉一个兄弟模块（turn.py），该副本必须跑不通；
否则守卫本身坏了（假绿），测试失败。
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

LOOP_DIR = Path(__file__).resolve().parent.parent / "loop"
DISPATCH_REL = Path("scripts") / "dispatch.py"
# 自证用的兄弟模块：A 片新增，dispatch 直接 import。
SIBLING_TO_DROP = Path("scripts") / "turn.py"


def _copy_loop(dest):
    # 忽略 __pycache__/pyc：避免从陈旧字节码误导入，保证自证删源即真缺。
    shutil.copytree(LOOP_DIR, dest,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


def _run_help(loop_root):
    return subprocess.run(
        [sys.executable, str(loop_root / DISPATCH_REL), "--help"],
        capture_output=True, text=True, timeout=60,
    )


def main():
    with tempfile.TemporaryDirectory() as tmp:
        # 1 整目录副本：--help 必须成功（部署单元完整）。
        good = Path(tmp) / "good"
        _copy_loop(good)
        p = _run_help(good)
        assert p.returncode == 0, (
            "整目录副本跑 --help 失败（rc=%s）：\n%s" % (p.returncode, p.stderr))
        assert "usage:" in p.stdout.lower(), p.stdout
        print("1 whole-directory copy: dispatch --help ok")

        # 2 自证：漏拷一个兄弟模块的副本必红。
        broken = Path(tmp) / "broken"
        _copy_loop(broken)
        dropped = broken / SIBLING_TO_DROP
        dropped.unlink()
        p = _run_help(broken)
        assert p.returncode != 0, (
            "自证失败：删掉 %s 的副本仍跑通，冒烟门抓不住漏拷"
            % SIBLING_TO_DROP.name)
        assert dropped.stem in p.stderr.lower(), (
            "副本失败但不是因为缺 %s：\n%s" % (dropped.stem, p.stderr))
        print("2 self-proof: copy missing a sibling module fails")

    print("all deploy-smoke tests passed")


if __name__ == "__main__":
    main()
