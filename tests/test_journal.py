#!/usr/bin/env python3
"""test_journal.py — 台账文件 Journal 表驱动单测（④journal，#113，ADR-0022）。

跑法：python3 tests/test_journal.py
缝：loop/scripts/journal.py 的五个方法直调，只看文件行为（不碰管线、不碰 FakeHerdr）：
  1 open：建 runs 目录、写首版、只调一次（「消耗放行 id」的唯一动作）；
  2 phase：每次调用一次原子落盘、合并字段、name=None 只补字段不改 phase；
  3 fact：每次调用一次原子落盘、键值可经 get 读回；
  4 open 前零写入；phase 在 open 前抛编程错误（RuntimeError，不是 DispatchError）；
  5 fail：写 error:<kind> 终态＋failed_at（墙钟由注入 now 取）、内部吞 OSError；
  6 phase 不吞 OSError（中途写失败原样冒出，归 run 顶层映射 io_failed）；
  7 get 哨兵区分缺键与 False 值（complete 判据是键存在）。
原子落盘次数经 wrap 计数观察（mock.patch.object 退出无条件恢复，棘轮合法）；
OSError 用「把父目录换成普通文件」自然注入，不虚拟化写函数。
"""
import json
import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

import harness  # noqa: F401  （把 loop/scripts 放进 sys.path）
import journal
from errors import DispatchError

NOW = "2026-01-02T03:04:05+00:00"


def journal_at(td, name="run-1"):
    return journal.Journal(Path(td) / ".loop" / "runs" / (name + ".json"),
                           now=lambda: NOW)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


@contextmanager
def counting_writes():
    """观察原子落盘次数：每次调用都委派真实写函数，文件行为不变。"""
    with mock.patch.object(journal, "write_json_atomic",
                           wraps=journal.write_json_atomic) as spy:
        yield spy


def break_writes(path):
    """把写路径的父目录换成普通文件：下一次原子写抛 NotADirectoryError（OSError）。"""
    path = Path(path)
    path.unlink()
    path.parent.rmdir()
    path.parent.write_text("not a directory", encoding="utf-8")


def _expect_runtime_error(fn, what):
    try:
        fn()
    except RuntimeError as e:
        assert not isinstance(e, DispatchError), (what, e)
        return
    raise AssertionError("%s：必须抛编程错误 RuntimeError" % what)


def test_open_creates_runs_dir_and_writes_initial_snapshot():
    # open ＝放行 id 消耗的唯一动作：建 runs 目录、首版落盘；此前零写入。
    with tempfile.TemporaryDirectory() as td:
        j = journal_at(td)
        assert not j.path.parent.exists(), "构造不建目录"
        initial = {"release_id": "run-1", "phase": "sending",
                   "target_address": None, "workspace": None}
        with counting_writes() as w:
            j.open(initial)
        assert w.call_count == 1, "open 一次原子落盘"
        assert j.opened is True
        assert j.path.parent.is_dir(), "runs 目录由 open 建"
        assert read_json(j.path) == initial, "首版内容逐字"
        _expect_runtime_error(lambda: j.open(initial), "open 只能调用一次")
    print("1 open: runs dir + initial snapshot + once-only OK")


def test_phase_is_one_atomic_write_per_call_and_merges_fields():
    # name 非 None 改 phase；name=None（9 个只补字段写点）不改 phase；字段合并。
    cases = [
        ("sent_waiting", {"sent_at": "T1", "send": {"turn": 5}},
         "sent_waiting"),
        (None, {"reset_skipped": "fresh_launch"}, "sending"),
        ("read", {}, "read"),
    ]
    for name, fields, expect_phase in cases:
        with tempfile.TemporaryDirectory() as td:
            j = journal_at(td)
            j.open({"release_id": "run-1", "phase": "sending"})
            with counting_writes() as w:
                j.phase(name, **fields)
            assert w.call_count == 1, (name, "每次 phase 一次原子落盘")
            data = read_json(j.path)
            assert data["phase"] == expect_phase, (name, data)
            for key, value in fields.items():
                assert data[key] == value, (name, key, data)
    print("2 phase: one atomic write per call, fields merged, name=None keeps phase OK")


def test_fact_is_one_atomic_write_per_call_and_get_roundtrips():
    with tempfile.TemporaryDirectory() as td:
        j = journal_at(td)
        j.open({"release_id": "run-1", "phase": "adopting"})
        with counting_writes() as w:
            j.fact("pane_adopted", True)
            j.fact("space_landed", "sp2")
        assert w.call_count == 2, "每次 fact 一次原子落盘"
        assert j.get("pane_adopted") is True
        assert j.get("space_landed") == "sp2"
        assert j.get("absent") is None
        assert j.get("absent", "dflt") == "dflt"
        data = read_json(j.path)
        assert data["pane_adopted"] is True and data["space_landed"] == "sp2"
    print("3 fact: one atomic write per call, get roundtrip OK")


def test_zero_writes_before_open_and_phase_is_programming_error():
    # 预检期：fact 与 fail 都不写盘；phase 是编程错误（不是 DispatchError）。
    with tempfile.TemporaryDirectory() as td:
        j = journal_at(td)
        with counting_writes() as w:
            j.fact("target_address", "t1")
            j.fail("bad_args", "预检失败")
        assert w.call_count == 0, "open 前零写入"
        assert not j.path.parent.exists(), "open 前不建目录"
        assert j.opened is False
        assert j.get("target_address", "dflt") == "dflt", "未开时 get 恒返 default"
        _expect_runtime_error(lambda: j.phase("adopting"), "open 前 phase")
    print("4 before open: zero writes, phase raises RuntimeError OK")


def test_fail_writes_terminal_ledger_and_swallows_oserror():
    with tempfile.TemporaryDirectory() as td:
        j = journal_at(td)
        j.open({"release_id": "run-1", "phase": "sending", "pane_adopted": True})
        with counting_writes() as w:
            j.fail("launch_failed", "启动失败")
        assert w.call_count == 1, "fail 写一次终态"
        data = read_json(j.path)
        assert data["phase"] == "error:launch_failed"
        assert data["error"] == {"kind": "launch_failed", "message": "启动失败"}
        assert data["failed_at"] == NOW, "failed_at 由注入墙钟盖"
        assert data["pane_adopted"] is True, "终态保留既有字段"

        break_writes(j.path)
        j.fail("io_failed", "写不进去也不外抛")   # 不抛＝吞 OSError
    print("5 fail: terminal ledger + failed_at, swallows OSError OK")


def test_phase_does_not_swallow_oserror():
    with tempfile.TemporaryDirectory() as td:
        j = journal_at(td)
        j.open({"release_id": "run-1", "phase": "sending"})
        break_writes(j.path)
        try:
            j.phase("read")
        except OSError:
            pass
        else:
            raise AssertionError("phase 不得吞 OSError（中途写失败归 run 顶层 io_failed）")
    print("6 phase: OSError propagates OK")


def test_get_sentinel_distinguishes_missing_key_from_false_value():
    missing = object()
    with tempfile.TemporaryDirectory() as td:
        j = journal_at(td)
        j.open({"release_id": "run-1", "phase": "read",
                "complete": False, "complete_reason": []})
        assert j.get("complete", missing) is False, "False 是合法值，不是缺键"
        assert j.get("complete_reason", missing) == []
        assert j.get("never_written", missing) is missing, "缺键返回哨兵"
    with tempfile.TemporaryDirectory() as td:
        j = journal_at(td)
        assert j.get("complete", missing) is missing, "未开恒返 default"
    print("7 get: sentinel distinguishes missing key from False OK")


def main():
    test_open_creates_runs_dir_and_writes_initial_snapshot()
    test_phase_is_one_atomic_write_per_call_and_merges_fields()
    test_fact_is_one_atomic_write_per_call_and_get_roundtrips()
    test_zero_writes_before_open_and_phase_is_programming_error()
    test_fail_writes_terminal_ledger_and_swallows_oserror()
    test_phase_does_not_swallow_oserror()
    test_get_sentinel_distinguishes_missing_key_from_false_value()
    print("ALL JOURNAL TESTS PASSED")


if __name__ == "__main__":
    main()
