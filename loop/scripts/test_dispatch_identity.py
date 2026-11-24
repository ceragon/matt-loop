#!/usr/bin/env python3
"""check_identity 的 --expect-proc 匹配测试（临时桩，验完可删）。

claude 等的 foreground.name 可能是版本号（如 2.1.325），预检只认
foreground.argv[0] 与 status.app（ADR-0009）；foreground.name 不作判据。
"""
import argparse
import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("dispatch", HERE / "dispatch.py")
dispatch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dispatch)


def args(expect_proc=None):
    return argparse.Namespace(expect_proc=expect_proc)


def claude_terminal():
    # claude 靶子：name 是版本号，argv[0]=claude，status.app=claude-code
    return {
        "title": "示例任务标题",
        "foreground": {"name": "2.1.325", "argv": ["claude", "--dangerously-skip-permissions"]},
        "status": {"app": "claude-code", "state": "working"},
    }


def expect_mismatch(terminal, a):
    try:
        dispatch.check_identity(terminal, a)
    except dispatch.DispatchError as e:
        assert e.kind == "identity_mismatch", e.kind
        return
    raise AssertionError("应抛 identity_mismatch")


def main():
    # 1) name 是版本号，但 argv[0]=claude → expect-proc claude 应过
    dispatch.check_identity(claude_terminal(), args(expect_proc="claude"))
    print("1 name=版本号但 argv[0]=claude -> 过 OK")

    # 2) status.app 兜底：name/argv 都不含，app 含 → 过
    t = {"foreground": {"name": "9.9.9", "argv": []}, "status": {"app": "claude-code"}}
    dispatch.check_identity(t, args(expect_proc="claude"))
    print("2 仅 status.app 含 claude -> 过 OK")

    # 3) 身份不符 → identity_mismatch
    t = {"foreground": {"name": "grok", "argv": ["grok"]}, "status": {"app": "grok"}}
    expect_mismatch(t, args(expect_proc="claude"))
    print("3 身份不符 -> identity_mismatch OK")

    # 4) 标题不再作判据：标题任意、proc 符即过
    t = claude_terminal()
    t["title"] = "Claude Code"  # 默认标题，与票号无关
    dispatch.check_identity(t, args(expect_proc="claude"))
    print("4 标题任意但 proc 符 -> 过（标题预检已移除）OK")

    # 5) foreground.name 不作判据（ADR-0009）：name 含预期子串但 argv[0]/app 不含 → 不符
    t = {"foreground": {"name": "claude-fake", "argv": ["grok"]}, "status": {"app": "grok"}}
    expect_mismatch(t, args(expect_proc="claude"))
    print("5 name 含子串但 argv[0]/app 不含 -> identity_mismatch（name 不作判据）OK")

    print("全部通过")


if __name__ == "__main__":
    main()
