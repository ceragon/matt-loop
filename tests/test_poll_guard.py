#!/usr/bin/env python3
"""test_poll_guard.py — 等待循环的两个小件（P4，ADR-0020 决定 8）。

跑法：python3 tests/test_poll_guard.py
缝：`pipeline.poll(deps, step, *, deadline, interval, on_timeout)` 与
`pipeline.guard(session, *, exited, error)`。断言只认对外行为：
  - poll：走 Deps 虚拟时钟；step 返回 None 表示继续，返回非 None 即返回值；
    到 deadline 抛调用方传入的 on_timeout，不自己造 kind/文案。
  - guard：终端已结束 → 传入的 exited（优先于状态判读）；blocked → 既有
    dialog_blocked（经 dialog_error）；error → 传入的 error；kind/文案由调用
    阶段提供，helper 不自造。

六个就绪判据（turn 过基线加落定、前台不再是 shell、屏幕静置、shell 就绪）留在
六个具名函数里，由 S 快照与既有 main 级覆盖守；本文件只钉这两个小件。
"""
import types

import harness  # noqa: F401  （把 loop/scripts 放进 sys.path）
import pipeline
from errors import DispatchError
from herdr import Session


def fake_deps(clock):
    """只给 poll 需要的虚拟时钟两面。"""
    return types.SimpleNamespace(monotonic=clock.monotonic, sleep=clock.sleep)


def raises(fn, exc):
    try:
        fn()
    except exc as e:
        return e
    raise AssertionError("期望抛出 %r" % (exc,))


def main():
    # 1 step 立即落定：直接返回其值，零睡眠
    clock = harness.VirtualClock()
    calls = []

    def settled():
        calls.append(clock.t)
        return "g"

    got = pipeline.poll(fake_deps(clock), settled, deadline=10, interval=2,
                        on_timeout=DispatchError("timeout", "poll 超时", []))
    assert got == "g" and calls == [0] and clock.t == 0, (got, calls, clock.t)
    print("1 poll: settled step returns its value, no sleep ok")

    # 2 step 返回 None 继续轮询；间隔推进虚拟时钟
    clock = harness.VirtualClock()
    calls, results = [], [None, None, "g"]

    def advancing():
        calls.append(clock.t)
        return results.pop(0)

    got = pipeline.poll(fake_deps(clock), advancing, deadline=10, interval=2,
                        on_timeout=DispatchError("timeout", "poll 超时", []))
    assert got == "g" and calls == [0, 2, 4] and clock.t == 4, (got, calls, clock.t)
    print("2 poll: None means pending, interval advances virtual clock ok")

    # 3 到 deadline 抛调用方传入的 on_timeout，step 不再调用
    clock = harness.VirtualClock()
    seen = []

    def stuck():
        seen.append(clock.t)
        return None

    timeout = DispatchError("timeout", "poll 超时", [])
    e = raises(lambda: pipeline.poll(fake_deps(clock), stuck, deadline=5, interval=2,
                                     on_timeout=timeout), DispatchError)
    assert e is timeout and seen == [0, 2, 4], (e, seen)
    print("3 poll: deadline raises the supplied on_timeout ok")

    # 4 deadline 已过：立即抛，step 零调用
    clock = harness.VirtualClock()
    clock.t = 9

    def never():
        raise AssertionError("deadline 已过，step 不应被调用")

    e = raises(lambda: pipeline.poll(fake_deps(clock), never, deadline=9, interval=1,
                                     on_timeout=DispatchError("timeout", "poll 超时", [])),
               DispatchError)
    assert e.kind == "timeout", e
    print("4 poll: expired deadline raises before stepping ok")

    # ---- guard ----

    def session(state="live", status_state=None, **status):
        raw = {"state": state}
        if status_state is not None:
            raw["status"] = dict(status, state=status_state)
        return Session.from_raw(raw)

    exited = DispatchError("target_exited", "本阶段退出文案", [])
    error = DispatchError("target_error", "本阶段 error 文案", [])

    # 5 终端已结束 → 传入的 exited（优先于 status 判读）
    e = raises(lambda: pipeline.guard(session("finished", "blocked"),
                                      exited=exited, error=error), DispatchError)
    assert e is exited, e
    print("5 guard: ended terminal raises the supplied exited, before status ok")

    # 6 blocked → 既有 dialog_blocked（文案由 dialog_error 生成）
    e = raises(lambda: pipeline.guard(session("live", "blocked", kind="perm", msg="请确认"),
                                      exited=exited, error=error), DispatchError)
    assert e.kind == "dialog_blocked", e
    assert e.message == "目标会话停在弹框等人决定（kind=perm）：请确认", e.message
    print("6 guard: blocked becomes dialog_blocked via dialog_error ok")

    # 7 error → 传入的 error
    e = raises(lambda: pipeline.guard(session("live", "error"),
                                      exited=exited, error=error), DispatchError)
    assert e is error, e
    print("7 guard: error status raises the supplied error ok")

    # 8 其它状态（idle/done/working，或状态缺失）不抛
    for g in (session("live", "idle"), session("live", "done"),
              session("live", "working"), session("live")):
        pipeline.guard(g, exited=exited, error=error)
    print("8 guard: idle/done/working/missing status passes ok")

    print("all poll/guard tests passed")


if __name__ == "__main__":
    main()
