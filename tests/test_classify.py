#!/usr/bin/env python3
"""test_classify.py — 五路分流纯函数 classify(session|None, request)（P1，ADR-0020 决定 4）。

跑法：python3 tests/test_classify.py
缝：dispatch.classify 纯函数直调（无 I/O、无全局替换、不碰 ctx）。
request 是鸭子类型：本函数只读 expect_proc 与 rebuild 两个字段；P2 的冻结
Request 可直接接上。表驱动覆盖五路分流、标签/身份/shell 判定、
occupied→rebuild 升格、--rebuild 误传、非 live/finished → not_idle。
"""
from types import SimpleNamespace

import harness
from harness import dispatch
from herdr import Session


def sess(**over):
    """造一个 live claude agent 的 Session（默认既有路径的全部判据均满足）。"""
    raw = {"address": "t1", "state": "live", "turn": 5,
           "labels": {"loop": "1", "loop-agent": "claude"},
           "foreground": {"name": "2.1.331", "argv": ["claude"]},
           "status": {"app": "claude-code", "state": "idle"}}
    raw.update(over)
    return Session.from_raw(raw)


SHELL_FG = {"name": "zsh", "argv": ["/bin/zsh", "-l"]}
WRONG_FG = {"name": "grok", "argv": ["grok"]}
WRONG_STATUS = {"app": "grok", "state": "idle"}


def req(**over):
    kw = {"expect_proc": "claude", "rebuild": False}
    kw.update(over)
    return SimpleNamespace(**kw)


MISUSE = {
    "kind": "bad_args",
    "message": "--rebuild 只在 session_occupied 占位分支生效，其余分流误传",
    "hints": ["去掉 --rebuild；强制重建仅用于用户显式授权 kill 占位者后同名重建"],
    "returncode": 2,
}
NOT_IDLE = {"kind": "not_idle", "message": "目标终端已结束，不发送",
            "hints": [], "returncode": 1}

# (用例名, session, request, 期望 Route 名 或 期望错误 dict)
CASES = [
    ("目标不存在 → 创建", None, req(), "create"),
    ("同名终端已结束 → 创建（旧条目按 t 号留审计）",
     sess(state="finished"), req(), "create"),
    ("live 空闲 shell＋标签符 → 复活",
     sess(foreground=SHELL_FG, status=None), req(), "revive"),
    ("live 前台 agent 身份符＋标签符 → 既有", sess(), req(), "existing"),
    ("--expect-proc 为空时只认标签 → 既有",
     sess(), req(expect_proc=None), "existing"),
    ("无 loop 标签 → 占位", sess(labels={}), req(), "occupied"),
    ("loop 标签与 --expect-proc 不符 → 占位",
     sess(labels={"loop": "1", "loop-agent": "grok"}), req(), "occupied"),
    ("前台身份与 --expect-proc 不符 → 占位",
     sess(foreground=WRONG_FG, status=WRONG_STATUS), req(), "occupied"),
    ("前台 shell 但 status 非 null（异常态）→ 占位",
     sess(foreground=SHELL_FG, status={"app": "x", "state": "idle"}),
     req(), "occupied"),
    ("occupied＋--rebuild → 强制重建升格",
     sess(labels={}), req(rebuild=True), "rebuild"),
    ("occupied＋--rebuild 但占位者 working → 仍是 rebuild（busy 由 gate_route 报）",
     sess(labels={}, status={"app": "grok", "state": "working"}),
     req(rebuild=True), "rebuild"),
    ("--rebuild 误传创建分流 → bad_args", None, req(rebuild=True), MISUSE),
    ("--rebuild 误传既有分流 → bad_args", sess(), req(rebuild=True), MISUSE),
    ("--rebuild 误传复活分流 → bad_args",
     sess(foreground=SHELL_FG, status=None), req(rebuild=True), MISUSE),
    ("live 但状态既非 live 也非 finished → not_idle",
     sess(state="ended"), req(), NOT_IDLE),
]


def _check_error(exc, want):
    assert isinstance(exc, dispatch.DispatchError), type(exc)
    assert exc.kind == want["kind"], exc.kind
    assert exc.message == want["message"], exc.message
    assert exc.hints == want["hints"], exc.hints
    assert exc.returncode == want["returncode"], exc.returncode


def main():
    for i, (label, session, request, want) in enumerate(CASES, 1):
        if isinstance(want, str):
            got = dispatch.classify(session, request)
            assert isinstance(got, dispatch.Route), got
            assert got is dispatch.Route[want], (label, got)
        else:
            try:
                dispatch.classify(session, request)
            except dispatch.DispatchError as e:
                _check_error(e, want)
            else:
                raise AssertionError(f"{label}: 未抛错")
        print(f"{i} {label} ok")

    # fresh 派生：create/revive/rebuild 为新鲜会话，existing/occupied 不是
    assert {r for r in dispatch.Route if r.is_fresh} == {
        dispatch.Route.create, dispatch.Route.revive, dispatch.Route.rebuild}
    assert not dispatch.Route.existing.is_fresh
    assert not dispatch.Route.occupied.is_fresh
    print("fresh derivation (create/revive/rebuild) ok")
    print("ALL CLASSIFY TESTS PASSED")


if __name__ == "__main__":
    main()
