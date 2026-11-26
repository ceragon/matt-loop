#!/usr/bin/env python3
"""test_dispatch_gift_ledger.py — 赠送清理告警的台账时序（G3，#113，ADR-0018 补记）。

跑法：python3 tests/test_dispatch_gift_ledger.py
缝：唯一业务缝 harness.run（CLI 契约）＋FakeHerdr；逐命令读盘用 S 片的
`snapshot.SnapshotWorld`（每条命令到达时先读一次磁盘台账），但不比对快照金样、
不动 tests/snapshots/。

钉住的次序：基线 gift_warning 每条告警立即写 ctx＋台账＋原子落盘，与清理命令交错。
G3 的 `discard_shell_tabs` 是生成器，管线在 for 循环体里逐条落盘——多 tab 清理时，
第一条 tab.close 失败并写入 gift_cleanup_warning 后、第二条 tab.close 命令到达时
台账已含该告警（整批延后就会在这里露馅）。
"""
import tempfile
from pathlib import Path

import harness
import helpers
import snapshot
from fake_herdr import FakeHerdr
from helpers import DISPLAY, EXT, LAUNCH, NAME, SCREEN

FRESH = ["--launch", LAUNCH, "--cwd", "/proj",
         "--display-name", DISPLAY, "--expect-proc", "claude"]


class AddGiftTabWorld(snapshot.SnapshotWorld):
    """space.new 成功后给新空间追加一个赠送 tab，构造多 tab 清理的交错。"""

    def __init__(self, world, ledger_path, tmp_dir, *, extra_gift):
        super().__init__(world, ledger_path, tmp_dir, screen_after_run=SCREEN)
        self._extra_gift = extra_gift

    def app_json(self, app_bin, args, timeout_s):
        ret = super().app_json(app_bin, args, timeout_s)
        if args[0] == "do" and args[1] == "space.new":
            sp = ret.get("space") if isinstance(ret, dict) else None
            if sp:
                self._world.add_tab(sp, terminals=[self._extra_gift])
        return ret


def run_case(td, rid):
    space_dir = Path(td) / "newspace"
    space_dir.mkdir()
    w = FakeHerdr()
    w.add_space(name="main")
    w.will_launch(LAUNCH, "agent")
    extra_gift = w.add_terminal(fg="shell", turn=0)
    w.fail("tab.close", "error")          # 两个赠送 tab 的关闭都失败
    a = snapshot.argv(td, rid, target=NAME, space=str(space_dir), extra=FRESH)
    ledger_path = (Path(a[a.index("--loop-dir") + 1]) / "runs" / (rid + ".json"))
    world = AddGiftTabWorld(w, ledger_path, td, extra_gift=extra_gift)
    return harness.run(a, world=world, extract=helpers.ext(EXT)), world


def main():
    with tempfile.TemporaryDirectory() as td:
        r, sw = run_case(td, "gift-ledger-1")
    assert r.rc == 0 and r.result["ok"], r.result

    closes = [i for i, e in enumerate(sw.trace)
              if e["command"] == "app do tab.close"]
    assert len(closes) >= 2, sw.trace
    first = sw.trace[closes[0]]
    second = sw.trace[closes[1]]
    assert "gift_cleanup_warning" not in (first["changes"] or {}), first
    assert "gift_cleanup_warning" in (second["changes"] or {}), second
    assert second["changes"]["gift_cleanup_warning"]["reason"] == "tab_close_error"

    # 终态仍带降级告警、主轮成功（清理失败不丢放行）
    assert r.ledger["gift_cleanup_warning"]["reason"] == "tab_close_error"
    assert r.ledger["workspace"] == "adopted"
    print("1 multi-tab gift cleanup: first warning on disk before second tab.close ok")
    print("ALL GIFT LEDGER TIMING TESTS PASSED")


if __name__ == "__main__":
    main()
