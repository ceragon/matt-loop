#!/usr/bin/env python3
"""test_dispatch_reset.py — reset 前置流程测试（mock hc/pi，不打真实 agent）。

跑法：python3.12 tests/test_dispatch_reset.py
覆盖：
  1 向后兼容：不传 --reset / --reset none，只发主任务一条。
  2 reset=clear：先发 /clear、落定后再发主任务，共两条；台账/result 记 reset。
  3 前置拒发（非 done）：整轮停，主任务未发，reset_sent=False。
  4 前置已发、主任务拒发：部分状态，reset_sent=True。
  5 reset_settle：屏幕仍在变→未落定；屏幕静置→落定。
"""
import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "loop" / "scripts"))
import dispatch  # noqa: E402
import os  # noqa: E402
# 本文件 mock extract，走 pi 后端保持旧行为（ADR-0005 起默认 api 直连）。
os.environ["LOOP_EXTRACT_BACKEND"] = "pi"
# 测试隔离：学习默认开启（ADR-0012），既有套件不测学习、也不碰真实学习目录，统一关闭。
# 学习专项测试（test_dispatch_learning）自行显式开启并指 LOOP_LEARNING_DIR 到临时目录。
os.environ["LOOP_LEARNING"] = "off"


PROMPT = "请实现某个功能\n第二行说明"
ANCHOR = PROMPT.splitlines()[0][:24]
SCREEN = ANCHOR + "\n已完成该功能。"
EXT = {"needs_user": False, "questions": [],
       "conclusion": "", "blocked": "", "reply": SCREEN, "next_skill": ""}


class FakeHc:
    def __init__(self, state="idle", refuse_nth=None, refuse_kind="status_mismatch"):
        self.sends = []          # (text, if_idle)
        self.turn = 5
        self.state = state
        self.screen = SCREEN
        self.refuse_nth = refuse_nth   # 第 n 次 send 拒发（1 起）
        self.refuse_kind = refuse_kind
        self._n = 0

    def __call__(self, hc_bin, args, timeout_s):
        cmd = args[0]
        if cmd == "get":
            return {"state": "live", "turn": self.turn, "title": "claude-test",
                    "address": "t99", "name": "claude-test",
                    "labels": {"loop": "1"},
                    "status": {"state": self.state, "title": "claude-test"},
                    "foreground": {"name": "claude", "argv": ["claude"]}}
        if cmd == "send":
            self._n += 1
            if self.refuse_nth is not None and self._n == self.refuse_nth:
                raise dispatch.HcFailure(self.refuse_kind, "refused", [], 1,
                                         {"kind": self.refuse_kind})
            text = args[-1]
            self.sends.append((text, "--if" in args))
            baseline = self.turn      # hc send 返回处理前的 turn 作基线
            self.turn += 1            # 处理后 turn +1，等待判据 turn > baseline
            return {"turn": baseline}
        if cmd == "read":
            return {"text": self.screen, "complete": True, "how": "current", "why": ""}
        if cmd == "wait":
            return {"reason": "status", "status": self.state, "turn": self.turn}
        raise AssertionError("unexpected hc cmd: " + cmd)


class FakeApp:
    """herdr-app 桩（ADR-0009 第二桩，与 hc_json 同一缝背后；全套件共用这一个）：
    status/ls/do（terminal.keep、space.new、tab.close、tab.rename）。panes 集合决定 ls 里谁有 pane；
    spaces 空列表＝本机无 space。默认 t99 有 pane（既有测试的目标）。
    keep 成功把 terminal 挂进第一个 space 的新 tab；keep_no_pane 模拟后台失败
    （命令成功但 pane 不落地）。tab.close 结束该 tab 终端（联动 hc 桩置 finished）。"""

    def __init__(self, hc=None, panes=("t99",), spaces=None, closed=False,
                 keep_error=False, keep_no_pane=False, space_new_error=False,
                 tab_close_error=False, rename_error=False, rename_noop=False,
                 strip_tab_id=False, pane_delay=0, select_error=False,
                 select_noop=False, restore_error=False, restore_noop=False,
                 user_switch=False, land_elsewhere=False, select_flaky=False,
                 gift_finish_delay=0, gift_vanish=False):
        self.hc = hc
        self.calls = []
        self.panes = set(panes)
        if spaces is None:
            spaces = [{"id": "sp1", "name": "main",
                       "tabs": [{"id": "tb1", "terminals": [{"terminal": a} for a in sorted(self.panes)]}]
                               if self.panes else []}]
        self.spaces = spaces
        # 当前选中空间（#43）：status 报它，keep 落进它；space.select 改它。
        self.selected = spaces[0]["id"] if spaces else None
        self._select_n = 0
        # 空间定位失败/竞态旋钮（#43）：select_error＝space.select 命令失败；
        # select_noop＝首次 select 成功但选中不变（复核不符）；restore_error/restore_noop
        # ＝还原（第二次起）select 失败/不落地；user_switch＝keep 后用户切走选中（接管）。
        self.select_error = select_error
        self.select_noop = select_noop
        self.restore_error = restore_error
        self.restore_noop = restore_noop
        self.user_switch = user_switch
        # land_elsewhere＝keep 的 pane 落进非选中空间（模拟异步落地前选中被切走）；
        # select_flaky＝首次 select 已生效但紧接的一次 status 报旧选中（复核抖动）。
        self.land_elsewhere = land_elsewhere
        self.select_flaky = select_flaky
        self._flaky_status = 0
        self._stale_space = None
        self.closed = closed
        self.keep_error = keep_error
        self.keep_no_pane = keep_no_pane
        self.space_new_error = space_new_error
        self.tab_close_error = tab_close_error
        # 显示名层（ADR-0010）失败旋钮：rename_error＝tab.rename 命令失败；
        # rename_noop＝命令成功但名不落地（复核不符）；strip_tab_id＝ls 不暴露 tab id（定位不到）。
        self.rename_error = rename_error
        self.rename_noop = rename_noop
        self.strip_tab_id = strip_tab_id
        self.gifted = None       # space.new 赠送的 shell 终端地址
        # #46 Bug2 竞态旋钮：gift_finish_delay=N → 赠送终端 tab.close 后延迟 N 次
        # hc get 查询才 finished（tab.close 异步结束终端，rm live 会被拒）。
        self.gift_finish_delay = gift_finish_delay
        # gift_vanish＝tab.close 时赠送终端条目直接被收走（get/rm 均 not_found）
        self.gift_vanish = gift_vanish
        self._keep_n = 0
        # #40 竞态旋钮：pane_delay=N → keep 后第 N 次 ls 才把 pane 落地（模拟后台异步落地）。
        self.pane_delay = pane_delay
        self._pending_pane = None
        self._pane_countdown = 0

    def _ls(self):
        spaces = []
        for sp in self.spaces:
            tabs = []
            for tb in sp["tabs"]:
                tabs.append({"id": None if self.strip_tab_id else tb["id"],
                             "name": tb.get("name"),
                             "terminals": [{"terminal": t["terminal"],
                                            "pane": ("p-" + t["terminal"])
                                            if t["terminal"] in self.panes else None}
                                           for t in tb["terminals"]]})
            spaces.append({"id": sp["id"], "name": sp.get("name"),
                           "folder": sp.get("folder"), "tabs": tabs})
        return {"spaces": spaces, "recent": []}

    def __call__(self, app_bin, args, timeout_s):
        self.calls.append(list(args))
        if self.closed:
            raise dispatch.AppFailure("app_closed", "herdr-app 窗口没开", [], 1,
                                      {"error": "not running"})
        if args[0] == "status":
            if self._flaky_status > 0:
                self._flaky_status -= 1
                return {"space": self._stale_space}
            return {"space": self.selected}
        if args[0] == "ls":
            if self._pending_pane is not None:
                self._pane_countdown -= 1
                if self._pane_countdown <= 0:
                    self.panes.add(self._pending_pane)
                    self._pending_pane = None
            return self._ls()
        if args[0] == "do":
            name, payload = args[1], json.loads(args[2])
            if name == "terminal.keep":
                if self.keep_error:
                    raise dispatch.AppFailure("keep_error", "keep failed", [], 1,
                                              {"error": "keep failed"})
                addr = payload["terminal"]
                if not self.keep_no_pane:
                    if self.pane_delay > 0:
                        self._pending_pane = addr
                        self._pane_countdown = self.pane_delay
                    else:
                        self.panes.add(addr)
                self._keep_n += 1
                if self.spaces:
                    # keep 落进当前选中空间（#43 实证语义），不是恒第一个。
                    if self.land_elsewhere:
                        cur = next((sp for sp in self.spaces if sp["id"] != self.selected),
                                   self.spaces[0])
                    else:
                        cur = next((sp for sp in self.spaces if sp["id"] == self.selected),
                                   self.spaces[0])
                    cur["tabs"].append(
                        {"id": "tb-keep%d" % self._keep_n, "terminals": [{"terminal": addr}]})
                    if self.user_switch:
                        # 落地窗口内用户切走选中（还原竞态护栏的触发条件）。
                        other = next((sp for sp in self.spaces if sp["id"] != self.selected), None)
                        if other:
                            self.selected = other["id"]
                return {"pane": "p-kept", "terminal": addr}
            if name == "space.select":
                self._select_n += 1
                restore = self._select_n > 1
                if self.select_error or (restore and self.restore_error):
                    raise dispatch.AppFailure("select_error", "space.select failed", [], 1,
                                              {"error": "space.select failed"})
                sp = next((s for s in self.spaces if s["id"] == payload["id"]), None)
                if sp is None:
                    raise dispatch.AppFailure("select_error", "no space %s" % payload["id"],
                                              [], 1, {"error": "unknown space"})
                noop = self.restore_noop if restore else self.select_noop
                if not noop:
                    if self.select_flaky and not restore:
                        self._stale_space = self.selected
                        self._flaky_status = 1
                    self.selected = sp["id"]
                return {}
            if name == "space.new":
                if self.space_new_error:
                    raise dispatch.AppFailure("space_new_error", "space.new failed", [], 1,
                                              {"error": "space.new failed"})
                gifted = self.hc.add(fg="shell", turn=0) if self.hc else None
                self.gifted = gifted["address"] if gifted else "t-gift"
                sp = {"id": "sp-new", "name": payload.get("name"),
                      "folder": payload.get("folder"),
                      "tabs": [{"id": "tb-gift", "terminals": [{"terminal": self.gifted}]}]}
                self.spaces.append(sp)
                self.panes.add(self.gifted)
                if self.selected is None:
                    self.selected = sp["id"]   # 无选中空间时新空间被显示（真实 app 语义）
                return {"space": sp["id"], "tab": "tb-gift", "terminal": self.gifted}
            if name == "tab.close":
                if self.tab_close_error:
                    raise dispatch.AppFailure("tab_close_error", "tab.close failed", [], 1,
                                              {"error": "tab.close failed"})
                for sp in self.spaces:
                    for tb in list(sp["tabs"]):
                        if tb["id"] == payload["id"]:
                            for t in tb["terminals"]:
                                if self.hc and t["terminal"] in self.hc.terminals:
                                    if self.gift_vanish and t["terminal"] == self.gifted:
                                        self.hc.terminals.pop(t["terminal"])
                                    elif (self.gift_finish_delay
                                            and t["terminal"] == self.gifted):
                                        self.hc.terminals[t["terminal"]]["finish_countdown"] = \
                                            self.gift_finish_delay
                                    else:
                                        self.hc.terminals[t["terminal"]]["state"] = "finished"
                                self.panes.discard(t["terminal"])
                            sp["tabs"].remove(tb)
                return {}
            if name == "tab.rename":
                if self.rename_error:
                    raise dispatch.AppFailure("rename_error", "tab.rename failed", [], 1,
                                              {"error": "tab.rename failed"})
                for sp in self.spaces:
                    for tb in sp["tabs"]:
                        if tb["id"] == payload["id"] and not self.rename_noop:
                            tb["name"] = payload["name"]
                return {}
            raise AssertionError("unexpected do command: " + name)
        raise AssertionError("unexpected app cmd: " + repr(args))


def run_case(reset_arg, fake, rid):
    with tempfile.TemporaryDirectory() as td:
        pf = Path(td) / "prompt.md"
        pf.write_text(PROMPT, encoding="utf-8")
        ld = Path(td) / ".loop"
        argv = ["--target", "t99", "--release-id", rid,
                "--prompt-file", str(pf), "--loop-dir", str(ld),
                "--space", "main",
                "--wall-clock", "5", "--check-in", "1",
                "--settle-seconds", "0.01", "--settle-checks", "2"]
        if reset_arg is not None:
            argv += ["--reset", reset_arg]
        dispatch.hc_json = fake
        dispatch.app_json = FakeApp()
        dispatch.extract = lambda pi_bin, args, raw_path: dict(EXT)
        dispatch.check_structure_and_tools = lambda: None
        dispatch.shutil.which = lambda name: "/fake/" + name
        dispatch.time.sleep = lambda s: None
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = dispatch.main(argv)
        result = json.loads(buf.getvalue().strip())
        ledger = json.loads((ld / "runs" / (rid + ".json")).read_text(encoding="utf-8"))
        return rc, result, ledger


def main():
    # 1 向后兼容：不传 --reset
    fake = FakeHc()
    rc, res, led = run_case(None, fake, "reset-compat-1")
    assert rc == 0 and res["ok"], res
    assert fake.sends == [(PROMPT, True)], fake.sends
    assert res["reset"] == "none" and led["reset"] == "none"
    assert "reset_send" not in led
    print("1 backward-compat (no --reset): single main send ok")

    # 1b --reset none 显式
    fake = FakeHc()
    rc, res, led = run_case("none", fake, "reset-compat-2")
    assert rc == 0 and fake.sends == [(PROMPT, True)] and res["reset"] == "none"
    print("1b --reset none: single main send ok")

    # 2 reset=clear：两条发送，前置在前
    fake = FakeHc()
    rc, res, led = run_case("clear", fake, "reset-clear-1")
    assert rc == 0 and res["ok"], res
    assert fake.sends == [("/clear", True), (PROMPT, True)], fake.sends
    assert res["reset"] == "clear" and led["reset"] == "clear"
    assert led.get("reset_send") and led.get("reset_done_at"), led
    assert led["phase"] == "done"
    print("2 reset=clear: /clear then main, ledger/result record reset ok")

    # 2b reset=new / compact 也发对应字面命令
    for r in ("new", "compact"):
        fake = FakeHc()
        rc, res, led = run_case(r, fake, "reset-lit-" + r)
        assert rc == 0 and fake.sends[0] == ("/" + r, True), (r, fake.sends)
        assert res["reset"] == r
    print("2b reset=new/compact: literal /new /compact ok")

    # 3 前置拒发（复核非 done）：整轮停，主任务未发
    fake = FakeHc(state="idle", refuse_nth=1)
    rc, res, led = run_case("clear", fake, "reset-refuse-pre")
    assert rc == 1 and not res["ok"], res
    assert res["error"]["kind"] == "send_refused", res
    assert res["reset"] == "clear" and res["error"]["reset_sent"] is False
    assert fake.sends == [], fake.sends
    assert led["phase"].startswith("error:")
    print("3 pre-step refused: round aborts, main not sent, reset_sent=False ok")

    # 4 前置已发、主任务拒发：部分状态 reset_sent=True
    fake = FakeHc(state="idle", refuse_nth=2)
    rc, res, led = run_case("clear", fake, "reset-refuse-main")
    assert rc == 1 and not res["ok"], res
    assert res["error"]["kind"] == "send_refused", res
    assert res["reset"] == "clear" and res["error"]["reset_sent"] is True
    assert fake.sends == [("/clear", True)], fake.sends
    assert led.get("reset_done_at"), led   # 前置确已落定
    print("4 pre-step sent, main refused: partial state reset_sent=True ok")

    # 5 reset_settle：屏幕变化→未落定；静置→落定
    class Args:
        settle_checks = 2
        settle_seconds = 0
        target = "t99"
    screens = iter(["a", "b", "c"])     # 一直在变
    dispatch.hc_json = lambda hc_bin, args, ts: (
        {"state": "live", "status": {"state": "idle"}, "turn": 5}
        if args[0] == "get" else {"text": next(screens)})
    dispatch.time.sleep = lambda s: None
    assert dispatch.reset_settle("/fake/hc", Args()) is False
    dispatch.hc_json = lambda hc_bin, args, ts: (
        {"state": "live", "status": {"state": "idle"}, "turn": 5}
        if args[0] == "get" else {"text": "stable"})
    assert dispatch.reset_settle("/fake/hc", Args()) is True
    print("5 reset_settle: changing screen not settled, stable screen settled ok")

    print("all reset tests passed")


if __name__ == "__main__":
    main()