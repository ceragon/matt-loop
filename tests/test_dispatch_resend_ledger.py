#!/usr/bin/env python3
"""test_dispatch_resend_ledger.py — 补发期台账时序（G2，#113，ADR-0018 补记）。

跑法：python3 tests/test_dispatch_resend_ledger.py
缝：唯一业务缝 harness.run（CLI 契约）＋FakeHerdr；逐命令读盘用 S 片的
`snapshot.SnapshotWorld`（每条命令到达时先读一次磁盘台账），但不比对快照金样、
不动 tests/snapshots/。

钉住的次序：`do_send` 在「首发被拒且第一次复核为 done」与 `herdr.resend` 的
第二次复核之间落盘 phase=resending＋first_refusal={kind,returncode}（基线 L869
逐字一致）——第二次 get 命令到达时台账已含这两项，第一次 get 时还没有。
覆盖补发成功、第二次复核失败、补发仍失败三支。
"""
import tempfile
from pathlib import Path

import harness
import helpers
import snapshot
from helpers import EXT, PROMPT

ADDR = "t99"
FIRST_REFUSAL = {"kind": "status_mismatch", "returncode": 1}
RESEND_REFUSED_HINTS = ["目标可能已重新开始工作或停在弹框；本轮未再发送"]


class FlipOnGetWorld(snapshot.SnapshotWorld):
    """第 N 次 hc get 返回后改写目标状态（构造 resend 第二次复核非 idle/done）。"""

    def __init__(self, world, ledger_path, tmp_dir, *, flip_after_get=0,
                 status="working"):
        super().__init__(world, ledger_path, tmp_dir)
        self._flip_after_get = flip_after_get
        self._flip_status = status
        self._gets = 0

    def hc_json(self, hc_bin, args, timeout_s):
        ret = super().hc_json(hc_bin, args, timeout_s)
        if args[0] == "get":
            self._gets += 1
            if self._gets == self._flip_after_get:
                self._world.set_status(args[1], self._flip_status)
        return ret


def run_case(td, rid, world, *, flip_after_get=0):
    a = helpers.argv(td, rid)
    ledger_path = Path(a[a.index("--loop-dir") + 1]) / "runs" / (rid + ".json")
    world = FlipOnGetWorld(world, ledger_path, td, flip_after_get=flip_after_get)
    result = harness.run(a, world=world, extract=helpers.ext(EXT))
    return result, world


def _resend_check_index(trace):
    """首发 hc send 之后的第二次 hc get（resend 的复核命令）在 trace 里的下标。"""
    first_send = next(i for i, e in enumerate(trace) if e["command"] == "hc send")
    gets = [i for i, e in enumerate(trace)
            if e["command"] == "hc get" and i > first_send]
    assert len(gets) >= 2, trace
    return gets[1]


def _assert_between_rechecks(world):
    """两次复核之间已落账 resending＋first_refusal；第一次复核时还没有。"""
    idx = _resend_check_index(world.trace)
    entry = world.trace[idx]
    assert entry["phase"] == "resending", entry
    assert entry["changes"].get("first_refusal") == FIRST_REFUSAL, entry
    first_check = world.trace[idx - 1]
    assert first_check["command"] == "hc get", first_check
    assert first_check["phase"] == "sending", first_check
    assert "first_refusal" not in first_check["changes"], first_check
    return idx


def main():
    # 1 补发成功：第二次复核时已记账，随后裸补发；终态 first_refusal/resent 与基线一致
    w = helpers.world()
    w.set_status(ADDR, "done")
    with tempfile.TemporaryDirectory() as td:
        r, sw = run_case(td, "resend-ledger-ok", w)
    assert r.rc == 0, r.result
    idx = _assert_between_rechecks(sw)
    assert sw.trace[idx + 1]["command"] == "hc send", sw.trace[idx + 1]
    assert r.ledger["first_refusal"] == FIRST_REFUSAL, r.ledger
    assert r.ledger["resent"] is True, r.ledger
    assert w.sends == [(ADDR, PROMPT, False)], w.sends
    print("1 resend success: ledger has resending+first_refusal at second recheck ok")

    # 2 第二次复核失败：台账已在两次复核之间落账；外部 send_refused 文案/hints 逐字
    w = helpers.world()
    w.set_status(ADDR, "done")
    with tempfile.TemporaryDirectory() as td:
        r, sw = run_case(td, "resend-ledger-recheck", w, flip_after_get=2)
    assert r.rc == 1, r.result
    err = r.result["error"]
    assert err["kind"] == "send_refused", err
    assert err["message"] == "补发前复核状态是 working，不补发", err
    assert err["hints"] == RESEND_REFUSED_HINTS, err
    idx = _assert_between_rechecks(sw)
    assert all(e["command"] != "hc send" for e in sw.trace[idx + 1:]), sw.trace[idx + 1:]
    assert r.ledger["phase"] == "error:send_refused", r.ledger
    assert r.ledger["first_refusal"] == FIRST_REFUSAL, r.ledger
    assert w.sends == [], w.sends
    print("2 second recheck failure: ledger write point unchanged, send_refused verbatim ok")

    # 3 补发仍失败：第二次复核通过、裸补发失败；first_refusal 仍在台账
    w = helpers.world()
    w.set_status(ADDR, "done")
    w.fail("send", "error", kind="status_mismatch", nth=1)   # 首发被拒
    w.fail("send", "error", kind="blocked", nth=1)           # 裸补发失败
    with tempfile.TemporaryDirectory() as td:
        r, sw = run_case(td, "resend-ledger-sendfail", w)
    assert r.rc == 1, r.result
    err = r.result["error"]
    assert err["kind"] == "send_refused", err
    assert err["message"] == "done 补发仍失败：send refused（故障旋钮）", err
    assert err["hints"] == ["本轮不再发送；台账已记录两次尝试"], err
    idx = _assert_between_rechecks(sw)
    assert sw.trace[idx + 1]["command"] == "hc send", sw.trace[idx + 1]
    assert r.ledger["phase"] == "error:send_refused", r.ledger
    assert r.ledger["first_refusal"] == FIRST_REFUSAL, r.ledger
    assert w.sends == [], w.sends
    print("3 resend send failure: first_refusal kept, send_refused verbatim ok")

    print("ALL RESEND LEDGER TIMING TESTS PASSED")


if __name__ == "__main__":
    main()
