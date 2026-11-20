#!/usr/bin/env python3
"""--loop-dir 默认归置测试（临时桩，验完可删）。

默认应是 $TMPDIR/loop（tempfile.gettempdir()/loop），CWD 无关；
显式 --loop-dir 仍可覆盖。
"""
import importlib.util
import os
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("dispatch", HERE / "dispatch.py")
dispatch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dispatch)

BASE = ["--target", "t7", "--release-id", "r1", "--prompt-file", "p.txt"]


def main():
    expect = str(Path(tempfile.gettempdir()) / "loop")

    # 1) 默认 loop-dir = $TMPDIR/loop
    args = dispatch.parse_args(BASE)
    assert args.loop_dir == expect, (args.loop_dir, expect)
    print("1 默认 loop-dir = $TMPDIR/loop OK")

    # 2) 显式覆盖仍生效
    args = dispatch.parse_args(BASE + ["--loop-dir", "/custom/loop"])
    assert args.loop_dir == "/custom/loop", args.loop_dir
    print("2 显式 --loop-dir 覆盖 OK")

    # 3) 默认与 CWD 无关（换当前目录不变）
    cwd = os.getcwd()
    os.chdir(tempfile.gettempdir())
    try:
        a2 = dispatch.parse_args(BASE)
        assert a2.loop_dir == expect, (a2.loop_dir, expect)
    finally:
        os.chdir(cwd)
    print("3 默认与 CWD 无关 OK")

    print("全部通过")


if __name__ == "__main__":
    main()
