#!/usr/bin/env python3
"""test_dispatch_adopt.py — 领养／恒可见不变量测试（ADR-0009，mock hc/herdr-app）。

跑法：python3 loop/scripts/test_dispatch_adopt.py
缝：分发脚本 CLI 契约（与 get_or_create 同一缝，FakeHc/FakeApp 复用）。
覆盖：
  1 既有路径 pane 在：只读 status＋ls，不 keep；result workspace=adopted。
  2 既有路径 pane 缺：预检补领养（keep＋ls 复核）成功；主任务照发。
  3 既有路径 app 没开：adopt_failed(app_closed)，零发送、无台账（id 不消耗）；
    开 app 后同 id 重试成功。
  4 既有路径 keep 失败／复核无 pane：adopt_failed(keep_error/no_pane)，id 不消耗。
  5 创建路径领养失败三原因：保留已建终端（launched:true/target_address）、
    workspace=failed＋adopt_reason、台账停 error:adopt_failed（id 消耗）；
    开 app 后新 id 重试走既有路径补领养修复。
  6 零空间统一走 --space 路径（#43 定稿）：space.new{name:目录名,folder,focus:false}
    →keep→复核→tab.close confirmed:true＋hc rm 赠送 shell（绑 space_created）；
    成功与中途失败部分状态（空间已建/pane 已落地如实报）。
  7 预检顺序：身份核对（分流）先于核 pane；核 pane 先于 idle 核对。
  8 app 探测绝不用裸 herdr-app（所有调用带子命令）。
  9 复活也过预检 pane 闸：app 没开停轮（id 不消耗、同 id 重试）；pane 缺补领养后才启动。
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dispatch  # noqa: E402
import os  # noqa: E402
os.environ["LOOP_EXTRACT_BACKEND"] = "pi"

from test_dispatch_get_or_create import (  # noqa: E402
    PROMPT, SCREEN, EXT, LAUNCH, NAME, NamedFakeHc, FakeApp, run_case,
)


def existing_terminal(fake, address="t50", labels=None):
    return fake.add(name=NAME, fg="agent", turn=5, address=address,
                    labels={"loop": "1", "loop-agent": "claude"} if labels is None else labels)


def main():
    # 1 既有路径 pane 在：只读探测，不 keep
    fake = NamedFakeHc()
    existing_terminal(fake)
    app = FakeApp(hc=fake, panes=("t50",))
    rc, res, led, td = run_case(fake, app, "adopt-have-1", expect_proc="claude")
    assert rc == 0 and res["ok"], res
    assert [c[0] for c in app.calls] == ["status", "ls"], app.calls
    assert res["workspace"] == "adopted" and res["target_address"] == "t50"
    assert fake.sends == [("t50", PROMPT, True)], fake.sends
    print("1 existing path pane present: read-only status+ls, no keep, workspace adopted ok")

    # 2 既有路径 pane 缺：预检补领养成功（keep＋复核），主任务照发
    fake = NamedFakeHc()
    existing_terminal(fake)
    app = FakeApp(hc=fake, panes=())
    rc, res, led, td = run_case(fake, app, "adopt-fix-1", expect_proc="claude")
    assert rc == 0 and res["ok"], res
    assert [c[0] for c in app.calls] == ["status", "ls", "do", "ls"], app.calls
    assert app.calls[2][1] == "terminal.keep"
    assert json.loads(app.calls[2][2]) == {"terminal": "t50"}
    assert res["workspace"] == "adopted"
    assert fake.sends == [("t50", PROMPT, True)], fake.sends
    print("2 existing path pane missing: precheck re-adopt (keep+recheck), main sent ok")

    # 3 既有路径 app 没开：adopt_failed(app_closed)，零发送、id 不消耗；同 id 重试修复
    fake = NamedFakeHc()
    existing_terminal(fake)
    app = FakeApp(hc=fake, panes=("t50",), closed=True)
    td = tempfile.mkdtemp()
    rc, res, led, _ = run_case(fake, app, "adopt-closed-1", expect_proc="claude", td=td)
    assert rc == 1 and res["error"]["kind"] == "adopt_failed", res
    assert res["adopt_reason"] == "app_closed" and res["workspace"] == "failed", res
    assert fake.sends == [] and led is None, "预检失败零发送、id 不消耗"
    app2 = FakeApp(hc=fake, panes=("t50",))
    rc, res, led, _ = run_case(fake, app2, "adopt-closed-1", expect_proc="claude", td=td)
    assert rc == 0 and res["ok"], res
    print("3 existing path app closed: adopt_failed app_closed, id unconsumed, same-id retry ok")

    # 4 既有路径 keep 失败／复核无 pane：adopt_failed，id 不消耗
    for knob, reason in (("keep_error", "keep_error"), ("keep_no_pane", "no_pane")):
        fake = NamedFakeHc()
        existing_terminal(fake)
        app = FakeApp(hc=fake, panes=(), **{knob: True})
        rc, res, led, td = run_case(fake, app, "adopt-%s-1" % reason, expect_proc="claude")
        assert rc == 1 and res["error"]["kind"] == "adopt_failed", (knob, res)
        assert res["adopt_reason"] == reason and res["workspace"] == "failed", res
        assert fake.sends == [] and led is None, knob
    print("4 existing path keep_error/no_pane: adopt_failed, id unconsumed ok")

    # 5 创建路径领养失败三原因：保留已建终端、id 消耗；新 id 重试走既有路径修复
    for knob, reason in (("closed", "app_closed"), ("keep_error", "keep_error"),
                         ("keep_no_pane", "no_pane")):
        fake = NamedFakeHc()
        app = FakeApp(hc=fake, **{knob: True})
        rc, res, led, td = run_case(fake, app, "adopt-create-%s" % reason,
                                    launch=LAUNCH, cwd="/proj", expect_proc="claude")
        assert rc == 1 and res["error"]["kind"] == "adopt_failed", (knob, res)
        assert res["adopt_reason"] == reason and res["workspace"] == "failed", res
        assert res["launched"] is True and res["launch_sent"] is True, res
        new_addr = res["target_address"]
        assert new_addr and fake.terminals[new_addr]["state"] == "live", "保留已建终端"
        assert led is not None and led["phase"] == "error:adopt_failed", "id 消耗、台账停领养失败"
        assert led["launched"] is True and led["workspace"] == "failed"
        # 主任务零发送（启动命令已发、占前置槽）
        assert fake.sends == [(new_addr, LAUNCH, False)], fake.sends
        # 开 app 后新放行 id 重试：既有路径补领养修复，主任务照发
        app2 = FakeApp(hc=fake, panes=())
        rc2, res2, led2, _ = run_case(fake, app2, "adopt-create-%s-fix" % reason,
                                      expect_proc="claude", td=td)
        assert rc2 == 0 and res2["ok"], (knob, res2)
        assert res2["workspace"] == "adopted"
        assert fake.sends[-1] == (new_addr, PROMPT, True), fake.sends
    print("5 create-path adopt failures: terminal kept, id consumed, new-id retry repairs ok")

    # 6 零空间统一走 --space 路径（#43 定稿）：space.new(name=目录名)→keep→复核→tab.close＋hc rm
    path = tempfile.mkdtemp()
    fake = NamedFakeHc()
    app = FakeApp(hc=fake, spaces=[])
    rc, res, led, td = run_case(fake, app, "adopt-nospace-1",
                                launch=LAUNCH, cwd="/proj", expect_proc="claude", space=path)
    assert rc == 0 and res["ok"], res
    kinds = [(c[0], c[1] if c[0] == "do" else None) for c in app.calls]
    # 建后 status 复核 shown（零空间新空间即显示，目标==shown 不 select）；
    # 清理舞后接显示名落地（ADR-0010，harness 自动注入 DISPLAY）：ls→tab.rename→ls
    assert kinds == [("status", None), ("ls", None), ("do", "space.new"), ("status", None),
                     ("do", "terminal.keep"), ("ls", None), ("do", "tab.close"),
                     ("ls", None), ("do", "tab.rename"), ("ls", None)], kinds
    sp_new = json.loads(app.calls[2][2])
    assert sp_new == {"name": Path(path).name, "folder": path, "focus": False}, sp_new
    tab_close = json.loads(app.calls[6][2])
    assert tab_close == {"id": "tb-gift", "confirmed": True}, tab_close
    assert fake.rms == [app.gifted], fake.rms
    assert res["workspace"] == "adopted"
    print("6 zero-space via --space path: space.new(dirname,focus:false)->keep->tab.close+rm ok")

    # 6b 建空间中途失败：space.new 失败（空间未建，adopt_failed）／tab.close 失败
    #    （pane 已落地、空间已建 → #46 Bug2 降级 gift_cleanup_warning，不丢放行）
    path = tempfile.mkdtemp()
    fake = NamedFakeHc()
    app = FakeApp(hc=fake, spaces=[], space_new_error=True)
    rc, res, led, td = run_case(fake, app, "adopt-nospace-sperr",
                                launch=LAUNCH, cwd="/proj", expect_proc="claude", space=path)
    assert rc == 1 and res["error"]["kind"] == "adopt_failed", res
    assert res["adopt_reason"] == "keep_error" and res["workspace"] == "failed"
    assert "space_created" not in res, "空间未建如实报"
    assert res["launched"] is True
    path = tempfile.mkdtemp()
    fake = NamedFakeHc()
    app = FakeApp(hc=fake, spaces=[], tab_close_error=True)
    rc, res, led, td = run_case(fake, app, "adopt-nospace-taberr",
                                launch=LAUNCH, cwd="/proj", expect_proc="claude", space=path)
    assert rc == 0 and res["ok"], res
    assert res["workspace"] == "adopted", "真终端已可见，整轮不判 failed（#46 Bug2）"
    assert res["space_created"] is True and res["pane_adopted"] is True, res
    assert res["gift_cleanup_warning"]["reason"] == "tab_close_error", res
    assert fake.rms == [], "tab.close 失败不应 rm"
    assert fake.sends[-1] == (res["target_address"], PROMPT, True), "主任务照发"
    print("6b space-create mid-failure: space.new error fails; tab.close error degrades to warning ok")

    # 6c 既有路径补领养遇零空间：同一套舞，预检失败仍 id 不消耗
    path = tempfile.mkdtemp()
    fake = NamedFakeHc()
    existing_terminal(fake)
    app = FakeApp(hc=fake, spaces=[], keep_error=True)
    rc, res, led, td = run_case(fake, app, "adopt-nospace-existing", expect_proc="claude",
                                space=path)
    assert rc == 1 and res["error"]["kind"] == "adopt_failed", res
    assert res["space_created"] is True and led is None
    assert fake.sends == []
    print("6c existing-path zero-space re-adopt failure: space created reported, id unconsumed ok")

    # 7 预检顺序：身份核对（分流）先于核 pane（标签符但前台身份不符＋app 没开 →
    #   session_occupied，不探 app）；核 pane 先于 idle 核对（working＋app 没开 → adopt_failed）
    fake = NamedFakeHc()
    d = existing_terminal(fake)
    d["foreground"] = {"name": "grok", "argv": ["grok"]}
    d["status"] = {"app": "grok", "state": "idle"}
    app = FakeApp(hc=fake, closed=True)
    rc, res, led, td = run_case(fake, app, "adopt-order-id", expect_proc="claude")
    assert rc == 1 and res["error"]["kind"] == "session_occupied", res
    assert app.calls == [], "身份不符不应探 app"
    fake = NamedFakeHc()
    d = existing_terminal(fake)
    d["status"]["state"] = "working"
    app = FakeApp(hc=fake, closed=True)
    rc, res, led, td = run_case(fake, app, "adopt-order-idle", expect_proc="claude")
    assert rc == 1 and res["error"]["kind"] == "adopt_failed", res
    print("7 precheck order: identity before pane, pane before idle ok")

    # 8 app 探测绝不用裸 herdr-app：所有调用第一个元素是子命令
    fake = NamedFakeHc()
    existing_terminal(fake)
    app = FakeApp(hc=fake, panes=())
    rc, res, led, td = run_case(fake, app, "adopt-probe-1", expect_proc="claude")
    assert rc == 0 and all(c and c[0] in ("status", "ls", "do") for c in app.calls)
    assert app.calls[0] == ["status"], "探针是 herdr-app status，不是裸 herdr-app"
    print("8 app probe uses status subcommand, never bare herdr-app ok")

    # 9 复活也过预检 pane 闸（US26：app 没开所有轮次停摆，无隐性例外）：
    #   app 没开→adopt_failed、零发送、id 不消耗、同 id 重试；pane 缺→补领养后才启动
    fake = NamedFakeHc()
    fake.add(name=NAME, fg="shell", turn=7,
             labels={"loop": "1", "loop-agent": "claude"}, address="t50")
    app = FakeApp(hc=fake, panes=("t50",), closed=True)
    td = tempfile.mkdtemp()
    rc, res, led, _ = run_case(fake, app, "adopt-revive-closed", launch=LAUNCH, cwd="/proj",
                               expect_proc="claude", td=td)
    assert rc == 1 and res["error"]["kind"] == "adopt_failed", res
    assert res["adopt_reason"] == "app_closed" and fake.sends == [] and led is None
    app2 = FakeApp(hc=fake, panes=("t50",))
    rc, res, led, _ = run_case(fake, app2, "adopt-revive-closed", launch=LAUNCH, cwd="/proj",
                               expect_proc="claude", td=td)
    assert rc == 0 and res["ok"], res
    fake2 = NamedFakeHc()
    fake2.add(name=NAME, fg="shell", turn=7,
              labels={"loop": "1", "loop-agent": "claude"}, address="t50")
    app3 = FakeApp(hc=fake2, panes=())
    rc, res, led, td = run_case(fake2, app3, "adopt-revive-fix", launch=LAUNCH, cwd="/proj",
                                expect_proc="claude")
    assert rc == 0 and res["ok"], res
    # 预检补领养 [status,ls,do,ls] ＋启动后显示名落地 [ls,tab.rename,ls]（ADR-0010）
    assert [c[0] for c in app3.calls] == ["status", "ls", "do", "ls", "ls", "do", "ls"], app3.calls
    assert fake2.sends == [("t50", LAUNCH, False), ("t50", PROMPT, True)], fake2.sends
    assert res["workspace"] == "adopted"
    print("9 revive pane gate: app closed stops round (id unconsumed), missing pane repaired ok")

    print("ALL ADOPT TESTS PASSED")


if __name__ == "__main__":
    main()