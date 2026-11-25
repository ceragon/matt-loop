#!/usr/bin/env python3
"""启动自举＋身份预检收敛测试（临时桩，验完可删）。

A: 解释器 <3.10 时 handle_old_python 经 uv 重 exec（不下载、防循环、探不到报错）。
B: --expect-title 已移除，parse_args 不再接受；身份预检只剩 --expect-proc。
"""
import contextlib
import importlib.util
import io
import json
import os
import sys
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent.parent / "loop" / "scripts"
spec = importlib.util.spec_from_file_location("dispatch", HERE / "dispatch.py")
dispatch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dispatch)

SCRIPT = str(Path(dispatch.__file__).resolve())


def call_old(uv_ret, guard, argv):
    """调 handle_old_python，mock execv 捕获命令；返回 (execv调用, stdout, exit码)。"""
    saved_guard = os.environ.get("LOOP_UV_REEXEC")
    saved_argv = sys.argv
    if guard is None:
        os.environ.pop("LOOP_UV_REEXEC", None)
    else:
        os.environ["LOOP_UV_REEXEC"] = guard
    sys.argv = argv
    calls = []
    out = io.StringIO()
    code = None

    def fake_execv(path, args):
        calls.append((path, list(args)))
        # 不真重 exec；返回让流程落到报错出口（真实 execv 成功则不返回）

    try:
        with mock.patch.object(dispatch, "find_tool", return_value=uv_ret), \
             mock.patch.object(dispatch.os, "execv", side_effect=fake_execv), \
             contextlib.redirect_stdout(out):
            dispatch.handle_old_python()
    except SystemExit as e:
        code = e.code
    finally:
        sys.argv = saved_argv
        if saved_guard is None:
            os.environ.pop("LOOP_UV_REEXEC", None)
        else:
            os.environ["LOOP_UV_REEXEC"] = saved_guard
    return calls, out.getvalue(), code


def main():
    # 1) uv 探到＋无 guard → execv 经 uv run --no-python-downloads 重跑，参数透传
    calls, out, code = call_old("/fake/uv", None,
                                ["dispatch.py", "--target", "t7", "--release-id", "r1"])
    assert len(calls) == 1, calls
    path, args = calls[0]
    assert path == "/fake/uv", path
    assert args[:4] == ["/fake/uv", "run", "--no-python-downloads", SCRIPT], args
    assert args[4:] == ["--target", "t7", "--release-id", "r1"], args
    print("1 uv 自举命令正确＋参数透传 OK")

    # 2) guard 已置 → 不再 execv，报 python_too_old 退出
    calls, out, code = call_old("/fake/uv", "1", ["dispatch.py"])
    assert not calls, calls
    assert code == 1, code
    assert json.loads(out)["error"]["kind"] == "python_too_old", out
    print("2 防循环 guard -> 不重 exec、报错 OK")

    # 3) uv 探不到 → 不 execv，报 python_too_old
    calls, out, code = call_old(None, None, ["dispatch.py"])
    assert not calls, calls
    assert code == 1 and json.loads(out)["error"]["kind"] == "python_too_old"
    print("3 uv 探不到 -> python_too_old OK")

    # 4) --expect-title 已从 parse_args 移除（传它报未知参数）
    base = ["--target", "t7", "--release-id", "r1", "--prompt-file", "p.txt", "--space", "main"]
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            dispatch.parse_args(base + ["--expect-title", "x"])
        raise AssertionError("--expect-title 应已被移除")
    except SystemExit as e:
        assert e.code == 2, e.code
    print("4 --expect-title 已移除 OK")

    # 5) --expect-proc 仍可用
    a = dispatch.parse_args(base + ["--expect-proc", "claude"])
    assert a.expect_proc == "claude"
    print("5 --expect-proc 保留 OK")

    print("全部通过")


if __name__ == "__main__":
    main()
