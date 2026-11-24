#!/usr/bin/env python3
"""test_dispatch_space.py — 显式空间定位 测试（#43，ADR-0011，mock hc/herdr-app）。

跑法：python3 loop/scripts/test_dispatch_space.py
缝：分发脚本 CLI 契约（与 get_or_create/adopt 同一缝，NamedFakeHc/FakeApp 复用）。
覆盖：
  1 CLI 静态契约：--space 可解析；空串 bad_args（rc2、零发送、id 不消耗）。
  2 既有路径 pane 缺＋--space（名/id）：space.select→status 复核→keep→ls 复核
    →还原选中；pane 落进目标空间（非活动空间）；result space/space_selected/
    space_restore=restored。
  3 解析失败：找不到 space_not_found、重名 space_ambiguous（adopt_failed，
    零 select/keep、零发送、id 不消耗）。
  4 select 失败/复核不符：select_error，keep 未发、选中未动。
  5 keep 失败（keep_error/no_pane）也还原选中（finally 护栏）。
  6 还原竞态护栏：窗口内用户接管选中→跳过还原（user_switched），领养照常成功。
  7 还原失败降级：restore_error/restore_mismatch → space_restore_warning，
    不丢放行（pane 落地仍是硬闸）。
  8 原本就选中目标空间：不还原（not_needed）。
  9 pane 已在：--space 无操作（不 select、不挪 pane），result 回显 space。
  10 创建路径＋--space：落进目标空间；无 space＋--space → space_not_found
    （不 space.new、保留已建终端、id 消耗）。
  11 不传 --space 维持现状：全程零 space.select。
  12 落地复核所在空间：pane 落进非目标空间 → space_target_warning
    （landed_elsewhere）告警不丢放行，不静默重演 #43。
  13 select 已生效但复核不符：先走还原护栏再 select_error（焦点不滞留）。
  14 复活路径＋--space：补领养走空间定位后才发启动命令。
  15 命中空间无 id：space_not_found，不静默退化为不定位。
"""
import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dispatch  # noqa: E402
import os  # noqa: E402
os.environ["LOOP_EXTRACT_BACKEND"] = "pi"

from test_dispatch_get_or_create import (  # noqa: E402
    PROMPT, LAUNCH, NAME, NamedFakeHc, FakeApp, run_case,
)


def existing_terminal(fake, address="t50"):
    return fake.add(name=NAME, fg="agent", turn=5, address=address,
                    labels={"loop": "1", "loop-agent": "claude"})


def two_spaces(panes=()):
    """两个空间：sp1（main，活动）＋sp2（proj，目标）；panes 决定谁已有 pane。"""
    return [{"id": "sp1", "name": "main", "folder": "/home",
             "tabs": [{"id": "tb1", "terminals": [{"terminal": a} for a in sorted(panes)]}]},
            {"id": "sp2", "name": "proj", "folder": "/proj", "tabs": []}]


def main():
    # 1 CLI 静态契约：--space 可解析；空串 bad_args
    base = ["--target", NAME, "--release-id", "r1", "--prompt-file", "p.txt"]
    a = dispatch.parse_args(base + ["--space", "proj"])
    assert a.space == "proj"
    a = dispatch.parse_args(base)
    assert a.space is None
    fake = NamedFakeHc()
    existing_terminal(fake)
    app = FakeApp(hc=fake, panes=("t50",))
    rc, res, led, td = run_case(fake, app, "space-empty-1", expect_proc="claude", space="")
    assert rc == 2 and res["error"]["kind"] == "bad_args", res
    assert res["space"] == "", "请求值回显（传了就带，error 也带）"
    assert fake.sends == [] and led is None, "空串拒发、id 不消耗"
    print("1 CLI static contract: --space parses, empty rejected bad_args rc2 ok")

    # 2 既有路径 pane 缺＋--space：select→复核→keep→ls 复核→还原；pane 落进目标空间
    for space_arg in ("proj", "sp2"):   # 名与 id 两种传法
        fake = NamedFakeHc()
        existing_terminal(fake)
        app = FakeApp(hc=fake, panes=(), spaces=two_spaces())
        rc, res, led, td = run_case(fake, app, "space-fix-%s" % space_arg,
                                    expect_proc="claude", space=space_arg)
        assert rc == 0 and res["ok"], res
        kinds = [(c[0], c[1] if c[0] == "do" else None) for c in app.calls]
        assert kinds == [("status", None), ("ls", None),
                         ("do", "space.select"), ("status", None),
                         ("do", "terminal.keep"), ("ls", None),
                         ("status", None), ("do", "space.select"), ("status", None)], kinds
        assert json.loads(app.calls[2][2]) == {"id": "sp2"}, app.calls[2]
        assert json.loads(app.calls[4][2]) == {"terminal": "t50"}, app.calls[4]
        assert json.loads(app.calls[7][2]) == {"id": "sp1"}, "还原到原选中空间"
        # pane 真落进目标空间 sp2（不是活动空间 sp1）
        in_sp2 = any(t["terminal"] == "t50" for tb in app.spaces[1]["tabs"]
                     for t in tb["terminals"])
        in_sp1 = any(t["terminal"] == "t50" for tb in app.spaces[0]["tabs"]
                     for t in tb["terminals"])
        assert in_sp2 and not in_sp1, "keep 应落进选中的目标空间"
        assert app.selected == "sp1", "选中还原"
        assert res["space"] == space_arg and res["space_selected"] == "sp2", res
        assert res["space_restore"] == "restored" and res["workspace"] == "adopted", res
        assert res["space_landed"] == "sp2" and "space_target_warning" not in res, res
        assert fake.sends == [("t50", PROMPT, True)], fake.sends
    print("2 existing path --space by name/id: select->keep->restore, pane in target space ok")

    # 3 解析失败：找不到 space_not_found、重名 space_ambiguous；零 select/keep、id 不消耗
    fake = NamedFakeHc()
    existing_terminal(fake)
    app = FakeApp(hc=fake, panes=(), spaces=two_spaces())
    rc, res, led, td = run_case(fake, app, "space-notfound-1", expect_proc="claude",
                                space="no-such-space")
    assert rc == 1 and res["error"]["kind"] == "adopt_failed", res
    assert res["adopt_reason"] == "space_not_found" and res["workspace"] == "failed", res
    assert res["space"] == "no-such-space", "请求值回显"
    assert [c[0] for c in app.calls] == ["status", "ls"], "零 select/keep"
    assert fake.sends == [] and led is None, "零发送、id 不消耗"
    # 重名：两个空间同名 proj
    spaces = two_spaces()
    spaces[1]["name"] = "main"
    fake = NamedFakeHc()
    existing_terminal(fake)
    app = FakeApp(hc=fake, panes=(), spaces=spaces)
    rc, res, led, td = run_case(fake, app, "space-ambig-1", expect_proc="claude", space="main")
    assert rc == 1 and res["error"]["kind"] == "adopt_failed", res
    assert res["adopt_reason"] == "space_ambiguous", res
    assert [c[0] for c in app.calls] == ["status", "ls"] and fake.sends == [] and led is None
    print("3 resolution failures: space_not_found/space_ambiguous, zero writes, id unconsumed ok")

    # 4 select 失败/复核不符：select_error，keep 未发、选中未动、id 不消耗
    for knob in ("select_error", "select_noop"):
        fake = NamedFakeHc()
        existing_terminal(fake)
        app = FakeApp(hc=fake, panes=(), spaces=two_spaces(), **{knob: True})
        rc, res, led, td = run_case(fake, app, "space-%s-1" % knob, expect_proc="claude",
                                    space="proj")
        assert rc == 1 and res["error"]["kind"] == "adopt_failed", (knob, res)
        assert res["adopt_reason"] == "select_error", (knob, res)
        assert not any(c[0] == "do" and c[1] == "terminal.keep" for c in app.calls), knob
        assert app.selected == "sp1", "选中未动"
        assert fake.sends == [] and led is None, knob
    print("4 select error/noop: select_error, keep not sent, selection untouched ok")

    # 5 keep 失败（keep_error/no_pane）也还原选中（finally 护栏）：adopt_failed 但视图不劫持
    for knob, reason in (("keep_error", "keep_error"), ("keep_no_pane", "no_pane")):
        fake = NamedFakeHc()
        existing_terminal(fake)
        app = FakeApp(hc=fake, panes=(), spaces=two_spaces(), **{knob: True})
        rc, res, led, td = run_case(fake, app, "space-%s-restore" % reason,
                                    expect_proc="claude", space="proj")
        assert rc == 1 and res["error"]["kind"] == "adopt_failed", (knob, res)
        assert res["adopt_reason"] == reason, (knob, res)
        assert res["space_restore"] == "restored", (knob, res)
        assert app.selected == "sp1", "失败路径也还原选中"
        assert fake.sends == [] and led is None, knob
    print("5 keep failure paths restore selection (finally guardrail) ok")

    # 6 还原竞态护栏：落地窗口内用户切走选中→跳过还原（user_switched），领养照常成功
    fake = NamedFakeHc()
    existing_terminal(fake)
    app = FakeApp(hc=fake, panes=(), spaces=two_spaces(), user_switch=True)
    rc, res, led, td = run_case(fake, app, "space-userswitch-1", expect_proc="claude",
                                space="proj")
    assert rc == 0 and res["ok"], res
    assert res["space_restore"] == "user_switched", res
    assert "space_restore_warning" not in res, "用户接管不是告警"
    assert app.selected == "sp1", "不抢回用户的视图"
    assert not any(c[0] == "do" and c[1] == "space.select"
                   and json.loads(c[2]) == {"id": "sp1"} for c in app.calls), "不发还原 select"
    assert res["workspace"] == "adopted" and fake.sends == [("t50", PROMPT, True)]
    print("6 restore race guardrail: user took over -> skip restore, adopt succeeds ok")

    # 7 还原失败降级：restore_error/restore_noop → space_restore_warning，不丢放行
    for knob, reason in (("restore_error", "restore_error"), ("restore_noop", "restore_mismatch")):
        fake = NamedFakeHc()
        existing_terminal(fake)
        app = FakeApp(hc=fake, panes=(), spaces=two_spaces(), **{knob: True})
        rc, res, led, td = run_case(fake, app, "space-%s-1" % knob, expect_proc="claude",
                                    space="proj")
        assert rc == 0 and res["ok"], (knob, res)
        assert res["space_restore"] == "failed", (knob, res)
        assert res["space_restore_warning"]["reason"] == reason, (knob, res)
        assert res["workspace"] == "adopted", "pane 落地仍是硬闸，还原失败不丢放行"
        assert fake.sends == [("t50", PROMPT, True)], knob
    print("7 restore failure degrades to warning, release not lost ok")

    # 8 原本就选中目标空间：不还原（not_needed），零还原 select
    fake = NamedFakeHc()
    existing_terminal(fake)
    spaces = two_spaces()
    app = FakeApp(hc=fake, panes=(), spaces=spaces)
    app.selected = "sp2"   # 用户当前就在目标空间
    rc, res, led, td = run_case(fake, app, "space-notneeded-1", expect_proc="claude",
                                space="proj")
    assert rc == 0 and res["ok"], res
    assert res["space_restore"] == "not_needed", res
    assert app.selected == "sp2"
    selects = [c for c in app.calls if c[0] == "do" and c[1] == "space.select"]
    assert len(selects) == 1 and json.loads(selects[0][2]) == {"id": "sp2"}, selects
    print("8 already on target space: no restore select (not_needed) ok")

    # 9 pane 已在：--space 无操作（不 select、不挪 pane），result 只回显请求值
    fake = NamedFakeHc()
    existing_terminal(fake)
    app = FakeApp(hc=fake, panes=("t50",), spaces=two_spaces(panes=("t50",)))
    rc, res, led, td = run_case(fake, app, "space-noop-1", expect_proc="claude", space="proj")
    assert rc == 0 and res["ok"], res
    assert [c[0] for c in app.calls] == ["status", "ls"], "pane 已在只读探测"
    assert res["space"] == "proj" and "space_selected" not in res and "space_restore" not in res
    assert app.selected == "sp1", "选中不动"
    assert fake.sends == [("t50", PROMPT, True)]
    print("9 pane present: --space no-op, request echoed ok")

    # 10 创建路径＋--space：落进目标空间、还原选中；无 space＋--space → space_not_found
    fake = NamedFakeHc()
    app = FakeApp(hc=fake, spaces=two_spaces())
    rc, res, led, td = run_case(fake, app, "space-create-1", launch=LAUNCH, cwd="/proj",
                                expect_proc="claude", space="proj")
    assert rc == 0 and res["ok"], res
    addr = res["target_address"]
    in_sp2 = any(t["terminal"] == addr for tb in app.spaces[1]["tabs"] for t in tb["terminals"])
    assert in_sp2, "创建路径 keep 也落进目标空间"
    assert app.selected == "sp1" and res["space_restore"] == "restored", res
    assert res["space"] == "proj" and res["space_selected"] == "sp2"
    assert led["space"] == "proj" and led["space_selected"] == "sp2", "台账留痕"
    assert led["space_restore"] == "restored"
    # 无 space＋--space：不 space.new（不自动建）、保留已建终端、id 消耗
    fake = NamedFakeHc()
    app = FakeApp(hc=fake, spaces=[])
    rc, res, led, td = run_case(fake, app, "space-create-nospace", launch=LAUNCH, cwd="/proj",
                                expect_proc="claude", space="proj")
    assert rc == 1 and res["error"]["kind"] == "adopt_failed", res
    assert res["adopt_reason"] == "space_not_found", res
    assert not any(c[0] == "do" and c[1] == "space.new" for c in app.calls), "不自动建空间"
    assert res["launched"] is True and res["launch_sent"] is True, res
    assert fake.terminals[res["target_address"]]["state"] == "live", "保留已建终端"
    assert led["phase"] == "error:adopt_failed", "id 消耗"
    print("10 create path --space: lands in target; no-space -> space_not_found, terminal kept ok")

    # 11 不传 --space 维持现状：全程零 space.select，keep 落进当前选中空间
    fake = NamedFakeHc()
    existing_terminal(fake)
    app = FakeApp(hc=fake, panes=(), spaces=two_spaces())
    rc, res, led, td = run_case(fake, app, "space-compat-1", expect_proc="claude")
    assert rc == 0 and res["ok"], res
    assert not any(c[0] == "do" and c[1] == "space.select" for c in app.calls), "零 select"
    assert "space" not in res and "space_restore" not in res
    in_sp1 = any(t["terminal"] == "t50" for tb in app.spaces[0]["tabs"] for t in tb["terminals"])
    assert in_sp1, "不传时落点随当前选中空间（现状）"
    print("11 no --space: status quo, zero space.select ok")

    # 12 落地复核所在空间：用户切走＋pane 异步落进用户空间 → 告警不丢放行（不静默重演 #43）
    fake = NamedFakeHc()
    existing_terminal(fake)
    app = FakeApp(hc=fake, panes=(), spaces=two_spaces(), user_switch=True, land_elsewhere=True)
    rc, res, led, td = run_case(fake, app, "space-drift-1", expect_proc="claude", space="proj")
    assert rc == 0 and res["ok"], res
    assert res["space_landed"] == "sp1", res
    assert res["space_target_warning"]["reason"] == "landed_elsewhere", res
    assert res["space_target_warning"]["actual"] == "sp1", res
    assert res["space_restore"] == "user_switched" and res["workspace"] == "adopted", res
    assert fake.sends == [("t50", PROMPT, True)], "pane 已落地，恒可见满足，不丢放行"
    print("12 landing recheck: pane landed elsewhere -> space_target_warning, release kept ok")

    # 13 select 已生效但复核不符（status 抖动/用户秒切）：先走还原护栏再 select_error
    fake = NamedFakeHc()
    existing_terminal(fake)
    app = FakeApp(hc=fake, panes=(), spaces=two_spaces(), select_flaky=True)
    rc, res, led, td = run_case(fake, app, "space-flaky-1", expect_proc="claude", space="proj")
    assert rc == 1 and res["error"]["kind"] == "adopt_failed", res
    assert res["adopt_reason"] == "select_error", res
    assert not any(c[0] == "do" and c[1] == "terminal.keep" for c in app.calls), "keep 未发"
    assert res["space_restore"] == "restored", "select 已生效，护栏把焦点还原"
    assert app.selected == "sp1", "焦点不被留在目标空间"
    assert fake.sends == [] and led is None
    print("13 select took effect but verify mismatched: restore guardrail then select_error ok")

    # 14 复活路径＋--space：pane 缺补领养走 targeting，之后才发启动命令
    fake = NamedFakeHc()
    fake.add(name=NAME, fg="shell", turn=7, address="t50",
             labels={"loop": "1", "loop-agent": "claude"})
    app = FakeApp(hc=fake, panes=(), spaces=two_spaces())
    rc, res, led, td = run_case(fake, app, "space-revive-1", launch=LAUNCH, cwd="/proj",
                                expect_proc="claude", space="proj")
    assert rc == 0 and res["ok"], res
    assert res["space_selected"] == "sp2" and res["space_restore"] == "restored", res
    in_sp2 = any(t["terminal"] == "t50" for tb in app.spaces[1]["tabs"] for t in tb["terminals"])
    assert in_sp2, "复活补领养也落进目标空间"
    assert fake.sends == [("t50", LAUNCH, False), ("t50", PROMPT, True)], fake.sends
    print("14 revive path --space: re-adopt targets space before launch ok")

    # 15 命中空间无 id：不静默退化为不 targeting，报 space_not_found
    fake = NamedFakeHc()
    existing_terminal(fake)
    app = FakeApp(hc=fake, panes=(), spaces=[{"id": None, "name": "proj", "tabs": []}])
    rc, res, led, td = run_case(fake, app, "space-noid-1", expect_proc="claude", space="proj")
    assert rc == 1 and res["error"]["kind"] == "adopt_failed", res
    assert res["adopt_reason"] == "space_not_found", res
    assert not any(c[0] == "do" and c[1] == "terminal.keep" for c in app.calls)
    print("15 matched space without id: space_not_found, no silent fallback ok")

    print("ALL SPACE TESTS PASSED")


if __name__ == "__main__":
    main()