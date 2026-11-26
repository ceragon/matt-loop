#!/usr/bin/env python3
"""test_herdr.py — herdr 领域网关的 interface 测试（G1，#109，ADR-0018 决定 12）。

跑法：python3 tests/test_herdr.py
只经 `Herdr(deps)` 与 FakeHerdr 的命令级缝断言外部行为：网关说领域事实，
命令时序停在 FakeHerdr 的 command_log/gifts 观察面；不断言网关私有方法。
"""
import harness  # noqa: F401  （把 loop/scripts 放进 sys.path 并导出 dispatch）
from harness import dispatch
from fake_herdr import FakeHerdr
from errors import DispatchError
from herdr import (
    Blocked, Change, Herdr, HerdrFailure, Layout, NotFound, Placement,
    Receipt, Session, StatusMismatch,
)


def make_herdr(world, *, locate=None, hc_json=None, app_json=None, clock=None):
    clock = clock if clock is not None else harness.VirtualClock()
    deps = dispatch.Deps(
        hc_json=hc_json if hc_json is not None else world.hc_json,
        app_json=app_json if app_json is not None else world.app_json,
        monotonic=clock.monotonic, sleep=clock.sleep, env={},
        locate=locate if locate is not None else (
            lambda name, probe_dirs=None: "/fake/" + name),
    )
    return Herdr(deps), clock


def world_with_two_spaces():
    w = FakeHerdr()
    w.add_terminal(address="t99", name="claude-test", labels={"loop": "1"})
    sp1 = w.add_space(name="main")
    sp2 = w.add_space(name="side")
    return w, sp1, sp2


# ---------------------------------------------------------------- 读模型

def read_models():
    w = FakeHerdr()
    w.add_terminal(address="t99", name="claude-test", labels={"loop": "1"}, turn=7)
    h, _ = make_herdr(w)
    s = h.session("t99")
    assert isinstance(s, Session)
    assert s.address == "t99" and s.state == "live"
    assert s.live is True and s.finished is False
    assert s.status_state == "idle" and s.turn == 7
    assert s.foreground["name"] == "2.1.331"
    assert s.labels == {"loop": "1"} and s.raw["address"] == "t99"
    assert s.identity_ok("claude") is True
    assert s.identity_ok("grok") is False
    assert s.identity_ok(None) is True
    assert s.is_shell() is False
    print("1 Session: live terminal, status/turn/foreground/labels/raw, derived checks")

    w = FakeHerdr()
    w.add_terminal(address="t99", state="finished")
    h, _ = make_herdr(w)
    s = h.session("t99")
    assert s.live is False and s.finished is True
    assert s.foreground is None and s.status is None      # foreground 键缺失 + status:null
    print("2 Session: finished terminal, absent foreground and null status")

    w = FakeHerdr()
    w.add_terminal(address="t99", fg="shell", turn=0)
    h, _ = make_herdr(w)
    assert h.session("t99").is_shell() is True
    print("3 Session: idle shell detected")

    # Layout：pane:null 与条目隐藏两种形态都归为「无 placement」
    lay = Layout({"spaces": [
        {"id": "sp1", "name": "main", "tabs": [
            {"id": "tb1", "name": "tab", "terminals": [
                {"terminal": "t-nopane", "pane": None}]}]}]})
    assert lay.placement("t-nopane") is None
    hidden = Layout({"spaces": [
        {"id": "sp1", "name": "main", "tabs": [
            {"id": "tb1", "name": "tab", "terminals": [
                {"terminal": "t-other", "pane": "p-other"}]}]}]})
    assert hidden.placement("t-hidden") is None
    print("4 Layout: pane-null and hidden entry both have no placement")

    w = FakeHerdr()
    w.add_terminal(address="t50")
    sp = w.add_space(name="main")
    tb = w.add_tab(sp, name="标签页", terminals=["t50"])
    h, _ = make_herdr(w)
    p = h.layout().placement("t50")
    assert isinstance(p, Placement)
    assert p.address == "t50" and p.pane == "p-t50"
    assert p.space["id"] == sp and p.tab["id"] == tb
    assert p.space_id == sp and p.tab_id == tb
    assert p.raw["terminal"] == "t50"
    print("5 Layout.placement gives space/tab/pane with raw entry")


# ---------------------------------------------------------------- 预检与失败模型

def preflight_and_failures():
    w = FakeHerdr()
    missing = make_herdr(w, locate=lambda name, probe_dirs=None: None)[0]
    for call, tool in ((missing.require_hc, "hc"), (missing.require_app, "herdr-app")):
        try:
            call()
        except DispatchError as e:
            assert e.kind == "missing_tool", (e.kind, tool)
            assert not isinstance(e, HerdrFailure)
        else:
            raise AssertionError("missing_tool 未抛：%s" % tool)
    print("6 require_hc/require_app raise missing_tool outside HerdrFailure")

    h, _ = make_herdr(w)
    assert h.require_hc() and h.require_app()
    print("7 require_hc/require_app return located path")

    w = FakeHerdr()
    w.add_terminal(address="t99")
    h, _ = make_herdr(w)
    try:
        h.session("nope")
    except NotFound as e:
        assert e.kind == "not_found" and e.returncode == 3
        assert e.native_kind == "not_found" and e.op == "session" and e.source == "hc"
    else:
        raise AssertionError("session 找不到须抛 NotFound，不返回 None")
    print("8 session(target) not found -> NotFound with native kind/returncode")

    w = FakeHerdr()
    w.add_terminal(address="t99")
    w.set_status("t99", "working")
    h, _ = make_herdr(w)
    try:
        h.send("t99", "hello", guard="idle")
    except StatusMismatch as e:
        assert e.kind == "status_mismatch" and e.stage == "refused_not_done"
        assert w.sends == []
    else:
        raise AssertionError("--if idle 被拒且非 done 须抛 StatusMismatch")
    print("9 StatusMismatch on --if idle refusal without done")

    w = FakeHerdr()
    w.add_terminal(address="t99")
    w.fail("send", "error", kind="blocked", message="blocked by user")
    h, _ = make_herdr(w)
    try:
        h.send("t99", "hello", guard="idle")
    except Blocked as e:
        assert e.kind == "blocked" and e.native_kind == "blocked"
        assert e.hints == ["blocked by user"] or e.message.endswith("blocked by user")
    else:
        raise AssertionError("blocked 须抛 Blocked")
    print("10 Blocked subclass, native kind passthrough")

    w = FakeHerdr()
    w.add_terminal(address="t99")
    w.fail("get", "error", kind="weird_native")
    h, _ = make_herdr(w)
    try:
        h.session("t99")
    except HerdrFailure as e:
        assert e.kind == "weird_native" and e.native_kind == "weird_native"
        assert type(e) is HerdrFailure
    else:
        raise AssertionError("原生 kind 须穿透")
    print("11 unknown native kind passes through as HerdrFailure, not external kind")

    # 进程层失败：tool_timeout/hc_output/app_output 也归 HerdrFailure，kind/退出码/hints 逐字
    def raise_timeout(hc_bin, args, timeout_s):
        raise DispatchError("tool_timeout", "挂起", ["等太久"], returncode=17)

    h, _ = make_herdr(FakeHerdr(), hc_json=raise_timeout)
    try:
        h.session("t99")
    except HerdrFailure as e:
        assert e.kind == "tool_timeout" and e.returncode == 17
        assert e.hints == ["等太久"] and e.source == "hc" and e.op == "session"
        assert e.native_kind is None      # 进程层失败没有 hc 原生 kind
    else:
        raise AssertionError("进程层失败须归 HerdrFailure")

    def raise_app_output(app_bin, args, timeout_s):
        raise DispatchError("app_output", "不是 JSON", ["stdout 摘录"])

    h, _ = make_herdr(FakeHerdr(), app_json=raise_app_output)
    try:
        h.layout()
    except HerdrFailure as e:
        assert e.kind == "app_output" and e.hints == ["stdout 摘录"]
        assert e.source == "app" and e.op == "layout"
    else:
        raise AssertionError("app_output 须归 HerdrFailure")
    print("12 process-layer failures stay HerdrFailure with verbatim kind/rc/hints")

    w = FakeHerdr()
    w.add_space(name="main")
    w.app_closed()                       # app 未开：错误信封沿用研究票假设形状
    h, _ = make_herdr(w)
    try:
        h.layout()
    except HerdrFailure as e:
        assert e.kind == "app_failed" and e.source == "app"
        assert e.detail == {"error": "not running"}
    else:
        raise AssertionError("app 未开须抛 HerdrFailure")
    print("12b app-not-running envelope -> HerdrFailure with detail kept")


def session_operations():
    w = FakeHerdr()
    h, _ = make_herdr(w)
    s = h.create_session("claude-test", "/proj")
    assert s.address and s.live and s.turn == 0
    s2 = h.label_session("claude-test", ["loop=1", "loop-agent=claude"])
    assert s2.labels == {"loop": "1", "loop-agent": "claude"}
    w.screen(s.address, "屏幕内容")
    assert h.read("claude-test", 10) == "屏幕内容"
    change = h.wait_change("claude-test", 1)
    assert isinstance(change, Change)
    assert change.reason == "status" and change.terminal == s.address
    assert h.kill("claude-test").finished is True
    print("13 create_session/label_session/read/wait_change/kill return facts")

    # read 缺 text 键不得静默返回 None：按输出层失败抛 HerdrFailure（hc_output）
    def read_without_text(hc_bin, args, timeout_s):
        return {"complete": True}

    h, _ = make_herdr(FakeHerdr(), hc_json=read_without_text)
    try:
        h.read("t99", 10)
    except HerdrFailure as e:
        assert e.kind == "hc_output" and e.stage == "missing_text"
        assert e.source == "hc" and e.op == "read"
        assert e.partial_facts["raw"] == {"complete": True}
    else:
        raise AssertionError("read 缺 text 键须抛 HerdrFailure")
    print("13b read: missing text key -> HerdrFailure, not silent None")

    w = FakeHerdr()
    sp0 = w.add_space(name="main")
    h, _ = make_herdr(w)
    sp = h.create_space("proj", "/proj")
    assert sp and sp != sp0
    assert any(s.get("id") == sp for s in h.layout().raw["spaces"])
    print("14 create_space returns the new space id")

    def missing_space(app_bin, args, timeout_s):
        return {"unexpected": True}

    h, _ = make_herdr(w, app_json=missing_space)
    try:
        h.create_space("x", "/x")
    except HerdrFailure as e:
        assert e.kind == "space_output" and e.stage == "missing_space"
        assert e.source == "app" and e.partial_facts["raw"] == {"unexpected": True}
    else:
        raise AssertionError("space.new 缺 space 键须抛 HerdrFailure")
    print("15 create_space missing 'space' key -> HerdrFailure")


def send_resend():
    w = FakeHerdr()
    w.add_terminal(address="t99")
    h, _ = make_herdr(w)
    r = h.send("t99", "first", guard="idle")
    assert isinstance(r, Receipt)
    assert r.resent is False and r.first_refusal is None
    assert w.sends == [("t99", "first", True)]
    print("16 send: idle guard accepted, no resend")

    w.set_status("t99", "done")
    r = h.send("t99", "second", guard="idle")
    assert r.resent is True
    assert r.first_refusal["kind"] == "status_mismatch"
    assert w.sends[-1] == ("t99", "second", False)
    assert len([s for s in w.sends if s[1] == "second"]) == 1
    print("17 send: refused but done -> exactly one bare resend, receipt.resent")

    w.set_status("t99", "working")
    before = len(w.sends)
    r = h.send("t99", "command", guard=None)
    assert r.resent is False and r.first_refusal is None
    assert w.sends[-1] == ("t99", "command", False)
    assert len(w.sends) == before + 1
    print("18 send: guard=None sends bare without resend logic")


def keep_polling():
    w = FakeHerdr()
    w.add_terminal(address="t50")
    w.add_space(name="main")
    w.delay("terminal.keep", "land_after", after=2)
    h, clock = make_herdr(w)
    p = h.keep("t50", within=10, poll=2)
    assert isinstance(p, Placement) and p.pane == "p-t50"
    assert clock.t >= 2          # 延迟旋钮下必须继续轮询
    print("19 keep: polls until pane lands after the delay")

    w = FakeHerdr()
    w.add_terminal(address="t50")
    w.add_space(name="main")
    w.fail("terminal.keep", "error")
    h, _ = make_herdr(w)
    try:
        h.keep("t50")
    except HerdrFailure as e:
        assert e.stage == "keep_command" and e.kind == "app_failed"
    else:
        raise AssertionError("keep 命令失败须抛")
    print("20 keep: command failure -> stage keep_command")

    w = FakeHerdr()
    w.add_terminal(address="t50")
    w.add_space(name="main")
    w.fail("ls", "error")
    h, _ = make_herdr(w)
    try:
        h.keep("t50")
    except HerdrFailure as e:
        assert e.stage == "keep_poll" and e.kind == "app_failed"
    else:
        raise AssertionError("轮询读失败须抛")
    print("21 keep: poll read failure -> stage keep_poll")

    w = FakeHerdr()
    w.add_terminal(address="t50")
    w.add_space(name="main")
    w.fail("terminal.keep", "noop")
    h, clock = make_herdr(w)
    try:
        h.keep("t50", within=4, poll=2)
    except HerdrFailure as e:
        assert e.kind == "no_placement" and e.stage == "no_pane"
        assert clock.t >= 4
    else:
        raise AssertionError("窗口耗尽须抛 HerdrFailure（stage no_pane）")
    print("22 keep: window exhausted -> stage no_pane, internal kind, virtual clock")


def focus_restoration():
    def world3():
        w, sp1, sp2 = world_with_two_spaces()
        sp3 = w.add_space(name="third")
        return w, sp1, sp2, sp3

    w, sp1, sp2, _ = world3()
    h, _ = make_herdr(w)
    with h.focus(sp2) as f:
        assert h.selected_space() == sp2
    assert f.restore == "restored" and h.selected_space() == sp1
    print("23 focus: restored to original selection")

    w, sp1, sp2, _ = world3()
    h, _ = make_herdr(w)
    with h.focus(sp1) as f:
        pass
    assert f.restore == "not_needed"
    assert ("app", "do", "space.select") not in w.command_log
    print("24 focus: already selected -> not_needed without select")

    w, sp1, sp2, sp3 = world3()
    h, _ = make_herdr(w)
    with h.focus(sp2) as f:
        w.user_switches_space()          # 用户接管：切去另一个空间
        assert h.selected_space() != sp2
    assert f.restore == "user_switched"
    assert h.selected_space() != sp2     # 不抢回
    print("25 focus: user switched during focus -> user_switched, not grabbed back")

    w, sp1, sp2, _ = world3()
    h, _ = make_herdr(w)
    w.fail("status", "error", nth=3)     # 第 3 次 status 是退出还原的首次读
    with h.focus(sp2) as f:
        pass
    assert f.restore == "failed"
    assert f.warning["reason"] == "restore_error"
    print("26 focus: restore read failure -> failed + restore_error warning")

    w, sp1, sp2, _ = world3()
    h, _ = make_herdr(w)
    w.delay("space.select", "land_after", after=2, nth=2)  # 只让还原时的 select 复核抖动
    with h.focus(sp2) as f:
        pass
    assert f.restore == "failed"
    assert f.warning["reason"] == "restore_mismatch"
    print("27 focus: restore verification mismatch -> failed + restore_mismatch")

    w, sp1, sp2, _ = world3()
    h, _ = make_herdr(w)
    try:
        with h.focus(sp2) as f:
            raise RuntimeError("业务异常")
    except RuntimeError:
        pass
    assert f.restore == "restored" and h.selected_space() == sp1
    print("28 focus: exception exit still restores")

    w, sp1, sp2, _ = world3()
    w.fail("space.select", "noop")
    h, _ = make_herdr(w)
    try:
        with h.focus(sp2):
            raise AssertionError("进入不符不应继续")
    except StatusMismatch as e:
        assert e.stage == "focus_select_mismatch"
    assert h.selected_space() == sp1
    print("29 focus: enter verification mismatch restores first, then raises")

    # select 已成功、随后复核读失败：进入路径也必须先还原再抛（__exit__ 不会运行）
    w, sp1, sp2, _ = world3()
    h, _ = make_herdr(w)
    w.fail("status", "error", nth=2)     # 第 2 次 status 是进入复核读
    try:
        with h.focus(sp2):
            raise AssertionError("进入复核读失败不应继续")
    except HerdrFailure as e:
        assert e.stage == "space_verify"
    assert h.selected_space() == sp1     # 已还原离开目标空间
    assert ("app", "do", "space.select") in w.command_log   # 还原命令已发
    print("29b focus: enter verify read failure restores before raising")


def gift_cleanup():
    def gifted_world():
        w = FakeHerdr()
        w.add_terminal(address="t50", name="claude-test")
        gift = w.add_terminal(address="t-gift", fg="shell", turn=0)
        sp = w.add_space(name="main")
        w.add_tab(sp, terminals=[gift])
        keep_tab = w.add_tab(sp, terminals=["t50"])
        return w, sp, gift, keep_tab

    w, sp, gift, keep_tab = gifted_world()
    h, _ = make_herdr(w)
    warnings = h.discard_shell_tabs(sp, "t50")
    assert warnings == []
    tabs = h.layout().raw["spaces"][0]["tabs"]
    assert [t["id"] for t in tabs] == [keep_tab]
    order = w.command_log
    assert order.index(("app", "do", "tab.close")) < order.index(("hc", "get"))
    assert order.index(("hc", "get")) < order.index(("hc", "rm"))
    print("30 discard_shell_tabs: tab.close -> wait finished -> rm ordering")

    w, sp, gift, keep_tab = gifted_world()
    w.fail("tab.close", "error")
    h, _ = make_herdr(w)
    warnings = h.discard_shell_tabs(sp, "t50")
    assert [x["reason"] for x in warnings] == ["tab_close_error"]
    assert ("hc", "get") not in w.command_log
    print("31 discard_shell_tabs: tab_close_error warning, no polling")

    w, sp, gift, keep_tab = gifted_world()
    w.delay("tab.close", "land_after", after=5)
    h, clock = make_herdr(w)
    warnings = h.discard_shell_tabs(sp, "t50", within=4, poll=2)
    assert [x["reason"] for x in warnings] == ["finish_timeout"]
    assert clock.t >= 4
    print("32 discard_shell_tabs: finish_timeout warning (rm live refused)")

    w, sp, gift, keep_tab = gifted_world()
    w.delay("tab.close", "land_after", after=99)
    w.fail("get", "error", kind="unavailable")
    h, clock = make_herdr(w)
    warnings = h.discard_shell_tabs(sp, "t50", within=4, poll=2)
    assert [x["reason"] for x in warnings] == ["finish_poll_error"]
    assert clock.t >= 4
    print("33 discard_shell_tabs: finish_poll_error warning")

    w, sp, gift, keep_tab = gifted_world()
    w.fail("rm", "error", kind="unavailable")
    h, _ = make_herdr(w)
    warnings = h.discard_shell_tabs(sp, "t50")
    assert [x["reason"] for x in warnings] == ["rm_error"]
    print("34 discard_shell_tabs: rm_error warning")

    # 瞬时读错误后恢复：最后一次轮询是成功读，窗口耗尽应记 timeout 而非 poll_error
    w, sp, gift, keep_tab = gifted_world()
    w.fail("tab.close", "noop")          # 终端保持 live
    w.fail("get", "error", kind="unavailable", nth=1)
    h, _ = make_herdr(w)
    warnings = h.discard_shell_tabs(sp, "t50", within=4, poll=2)
    assert [x["reason"] for x in warnings] == ["finish_timeout"]
    print("35 discard_shell_tabs: transient poll error recovered -> timeout")


def main():
    read_models()
    preflight_and_failures()
    session_operations()
    send_resend()
    keep_polling()
    focus_restoration()
    gift_cleanup()
    print("all herdr tests passed")


if __name__ == "__main__":
    main()
