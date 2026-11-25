#!/usr/bin/env python3
"""test_dispatch_display_name.py — 显示名层测试（ADR-0010，mock hc/herdr-app）。

跑法：python3 tests/test_dispatch_display_name.py
缝：分发脚本 CLI 契约（与 get_or_create/adopt 同一缝，FakeHc/FakeApp 复用）。
覆盖（规格 #41 Testing Decisions）：
  T1 创建缺 --display-name（launch/cwd 齐）→ display_name_required、exit 2、
     零发送、无台账；补参同 id 重试成功。
  T2 复活/重建缺参 → 同 T1。
  T3 校验边界：空/65 字/含换行/含控制字符 → bad_args、exit 2、零发送；
     64 字中文含空格通过；任何分流都校验。
  T4 创建成功 → 调用序 领养→tab.rename→ls 复核→主发送；result display_name；
     台账 naming phase。
  T5 rename 命令失败 → ok:true、主任务照发、display_name_warning.reason=rename_error。
  T6 复核名不符 → warning rename_mismatch（带实际名）、ok:true。
  T7 复用轮不传 → 零 tab.rename 调用、result 无 display_name。
  T8 复用轮传 → tab.rename 恰一次（覆盖，无对比逻辑）。
  T9 定位不到 tab → warning no_tab、主流程继续。
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "loop" / "scripts"))
import dispatch  # noqa: E402
import os  # noqa: E402
os.environ["LOOP_EXTRACT_BACKEND"] = "pi"

from test_dispatch_get_or_create import (  # noqa: E402
    PROMPT, SCREEN, EXT, LAUNCH, NAME, DISPLAY, NamedFakeHc, FakeApp, run_case,
)


def main():
    # T1 创建缺 --display-name（launch/cwd 齐）：display_name_required rc2、
    #    零发送、无台账（id 不消耗）；补参同 id 重试成功
    fake = NamedFakeHc()
    app = FakeApp(hc=fake)
    td = tempfile.mkdtemp()
    rc, res, led, _ = run_case(fake, app, "dn-create-req", launch=LAUNCH, cwd="/proj",
                               expect_proc="claude", display_name=None, td=td)
    assert rc == 2 and res["error"]["kind"] == "display_name_required", res
    assert fake.runs == [] and fake.sends == [] and led is None, "零发送、id 不消耗"
    rc, res, led, _ = run_case(fake, app, "dn-create-req", launch=LAUNCH, cwd="/proj",
                               expect_proc="claude", display_name=DISPLAY, td=td)
    assert rc == 0 and res["ok"], res
    assert res["display_name"] == DISPLAY, res
    print("T1 create display_name_required: rc2 id unconsumed, same-id retry ok")

    # T2 复活/重建缺 --display-name：同 T1（rc2、零发送/零 kill、id 不消耗、补参同 id 重试）
    for scenario in ("revive", "rebuild"):
        fake = NamedFakeHc()
        if scenario == "revive":
            fake.add(name=NAME, fg="shell", turn=7,
                     labels={"loop": "1", "loop-agent": "claude"}, address="t50")
            app = FakeApp(hc=fake, panes=("t50",))
            kw = {}
        else:
            fake.add(name=NAME, fg="agent", app="grok", st="idle", turn=3,
                     labels={}, address="t60")
            app = FakeApp(hc=fake, panes=("t60",))
            kw = {"rebuild": True}
        td = tempfile.mkdtemp()
        rc, res, led, _ = run_case(fake, app, "dn-%s-req" % scenario, launch=LAUNCH,
                                   cwd="/proj", expect_proc="claude", display_name=None,
                                   td=td, **kw)
        assert rc == 2 and res["error"]["kind"] == "display_name_required", (scenario, res)
        assert fake.sends == [] and fake.runs == [] and fake.kills == [] and led is None, scenario
        rc, res, led, _ = run_case(fake, app, "dn-%s-req" % scenario, launch=LAUNCH,
                                   cwd="/proj", expect_proc="claude", display_name=DISPLAY,
                                   td=td, **kw)
        assert rc == 0 and res["ok"] and res["display_name"] == DISPLAY, (scenario, res)
    print("T2 revive/rebuild display_name_required: rc2 id unconsumed, same-id retry ok")

    # T3 机械校验边界：空/65 字/含换行/含控制字符 → bad_args rc2、零发送、id 不消耗；
    #    任何分流都校验（创建＋既有两条路径都试）。64 字中文含空格通过；不校验式样。
    bad_names = ["", "x" * 65, "a\nb", "a\rb", "a\x1bb", "a\tb", "a\x00b"]
    for bad in bad_names:
        for path in ("create", "existing"):
            fake = NamedFakeHc()
            if path == "existing":
                fake.add(name=NAME, fg="agent", turn=5,
                         labels={"loop": "1", "loop-agent": "claude"}, address="t50")
                app = FakeApp(hc=fake, panes=("t50",))
                kw = {"expect_proc": "claude"}
            else:
                app = FakeApp(hc=fake)
                kw = {"launch": LAUNCH, "cwd": "/proj", "expect_proc": "claude"}
            rid = "dn-bad-%s-%d" % (path, bad_names.index(bad))
            rc, res, led, td = run_case(fake, app, rid, display_name=bad, **kw)
            assert rc == 2 and res["error"]["kind"] == "bad_args", (path, repr(bad), res)
            assert fake.sends == [] and fake.runs == [] and led is None, (path, repr(bad))
    # 64 字中文含空格通过；无 bot 前缀的式样也通过（不校验式样，US10）
    for good in ["测" * 63 + " ", "随便什么名字"]:
        fake = NamedFakeHc()
        app = FakeApp(hc=fake)
        rc, res, led, td = run_case(fake, app, "dn-good-%d" % len(good),
                                    launch=LAUNCH, cwd="/proj", expect_proc="claude",
                                    display_name=good)
        assert rc == 0 and res["ok"] and res["display_name"] == good, (repr(good), res)
    print("T3 validation boundaries: empty/65/newline/control -> bad_args rc2 any branch; 64-CJK+space ok")

    # T4 创建成功：调用序 领养→tab.rename→ls 复核→主发送；result display_name；台账 naming 痕迹。
    #    用共享 journal 跨 hc/app 两桩断言时序（rename 在主任务发送之前）。
    fake = NamedFakeHc()
    app = FakeApp(hc=fake)
    journal = []

    def jhc(hc_bin, args, t):
        journal.append(("hc", args[0]))
        return fake(hc_bin, args, t)

    def japp(app_bin, args, t):
        journal.append(("app", args[0], args[1] if args[0] == "do" else None))
        return app(app_bin, args, t)

    rc, res, led, td = run_case(jhc, japp, "dn-create-ok", launch=LAUNCH, cwd="/proj",
                                expect_proc="claude", display_name=DISPLAY)
    assert rc == 0 and res["ok"], res
    assert res["display_name"] == DISPLAY and "display_name_warning" not in res, res
    assert led["display_name"] == DISPLAY and led.get("naming_done_at"), led
    # tab.rename 载荷 {id, name}，name 是放行原文
    renames = [c for c in app.calls if c[0] == "do" and c[1] == "tab.rename"]
    assert len(renames) == 1, app.calls
    assert json.loads(renames[0][2])["name"] == DISPLAY, renames
    # 时序：领养 keep → tab.rename → ls 复核 → 主任务发送（journal 跨桩）
    keep_i = journal.index(("app", "do", "terminal.keep"))
    rename_i = journal.index(("app", "do", "tab.rename"))
    last_send_i = len(journal) - 1 - journal[::-1].index(("hc", "send"))
    assert keep_i < rename_i < last_send_i, journal
    # rename 之后有 ls 复核（命令成功不算数）
    assert journal[rename_i + 1] == ("app", "ls", None), journal
    print("T4 create success: adopt->tab.rename->ls recheck->main send order, result+ledger ok")

    # T5 rename 命令失败 → ok:true、主任务照发、warning.reason=rename_error（不丢放行）
    fake = NamedFakeHc()
    app = FakeApp(hc=fake, rename_error=True)
    rc, res, led, td = run_case(fake, app, "dn-rename-err", launch=LAUNCH, cwd="/proj",
                                expect_proc="claude", display_name=DISPLAY)
    assert rc == 0 and res["ok"], res
    assert res["display_name"] == DISPLAY, res
    assert res["display_name_warning"]["reason"] == "rename_error", res
    assert led["display_name_warning"]["reason"] == "rename_error", led
    new_addr = res["target_address"]
    assert fake.sends[-1] == (new_addr, PROMPT, True), "主任务照发"
    print("T5 rename command failure: ok, main sent, warning rename_error ok")

    # T6 复核名不符 → warning rename_mismatch（带实际名）、ok:true。既有路径预置旧名，
    #    rename_noop 命令成功但名不落地 → 复核读到旧名。
    fake = NamedFakeHc()
    fake.add(name=NAME, fg="agent", turn=5,
             labels={"loop": "1", "loop-agent": "claude"}, address="t50")
    app = FakeApp(hc=fake, panes=("t50",), rename_noop=True,
                  spaces=[{"id": "sp1", "name": "main",
                           "tabs": [{"id": "tb1", "name": "旧名",
                                     "terminals": [{"terminal": "t50"}]}]}])
    rc, res, led, td = run_case(fake, app, "dn-mismatch", expect_proc="claude",
                                display_name=DISPLAY)
    assert rc == 0 and res["ok"], res
    w = res["display_name_warning"]
    assert w["reason"] == "rename_mismatch" and w["actual"] == "旧名", w
    assert fake.sends[-1] == ("t50", PROMPT, True), "主任务照发"
    print("T6 recheck mismatch: ok, warning rename_mismatch with actual name ok")

    # T9 定位不到 tab → warning no_tab、主流程继续（strip_tab_id：ls 不暴露 tab id）
    fake = NamedFakeHc()
    app = FakeApp(hc=fake, strip_tab_id=True)
    rc, res, led, td = run_case(fake, app, "dn-no-tab", launch=LAUNCH, cwd="/proj",
                                expect_proc="claude", display_name=DISPLAY)
    assert rc == 0 and res["ok"], res
    assert res["display_name_warning"]["reason"] == "no_tab", res
    assert res["display_name"] == DISPLAY, res
    new_addr = res["target_address"]
    assert fake.sends[-1] == (new_addr, PROMPT, True), "主流程继续"
    print("T9 tab not locatable: ok, warning no_tab, main flow continues ok")

    # T7 复用轮不传 → 零 tab.rename、result 无 display_name（向后兼容，US28）
    fake = NamedFakeHc()
    fake.add(name=NAME, fg="agent", turn=5,
             labels={"loop": "1", "loop-agent": "claude"}, address="t50")
    app = FakeApp(hc=fake, panes=("t50",))
    rc, res, led, td = run_case(fake, app, "dn-reuse-none", expect_proc="claude",
                                display_name=None)
    assert rc == 0 and res["ok"], res
    assert "display_name" not in res and "display_name_warning" not in res, res
    assert not any(c[0] == "do" and c[1] == "tab.rename" for c in app.calls), app.calls
    assert led["display_name"] is None, led
    print("T7 reuse without display-name: zero tab.rename, no display_name field ok")

    # T8 复用轮传 → tab.rename 恰一次（覆盖，无对比逻辑）。预置旧名→覆盖；预置同名→仍 rename（不跳过）。
    for preset in ("旧任务名", DISPLAY):
        fake = NamedFakeHc()
        fake.add(name=NAME, fg="agent", turn=5,
                 labels={"loop": "1", "loop-agent": "claude"}, address="t50")
        app = FakeApp(hc=fake, panes=("t50",),
                      spaces=[{"id": "sp1", "name": "main",
                               "tabs": [{"id": "tb1", "name": preset,
                                         "terminals": [{"terminal": "t50"}]}]}])
        rc, res, led, td = run_case(fake, app, "dn-reuse-set-%s" % ("same" if preset == DISPLAY else "diff"),
                                    expect_proc="claude", display_name=DISPLAY)
        assert rc == 0 and res["ok"], res
        renames = [c for c in app.calls if c[0] == "do" and c[1] == "tab.rename"]
        assert len(renames) == 1, (preset, app.calls)
        assert json.loads(renames[0][2])["name"] == DISPLAY, renames
        assert res["display_name"] == DISPLAY and "display_name_warning" not in res, res
    print("T8 reuse with display-name: exactly one tab.rename (overwrite, no compare/skip) ok")

    # T11 两参同缺（launch＋display_name）：launch_required 先报（预检顺序，Decision 3）
    fake = NamedFakeHc()
    app = FakeApp(hc=fake)
    rc, res, led, td = run_case(fake, app, "dn-both-missing", display_name=None)
    assert rc == 2 and res["error"]["kind"] == "launch_required", res
    assert fake.runs == [] and led is None
    print("T11 both launch+display missing: launch_required reported first ok")

    # T12 error 也回显 display_name＋warning（US17）：既有路径 rename 告警后主任务发送失败
    class MainRefuseHc(NamedFakeHc):
        def __call__(self, hc_bin, args, timeout_s):
            if args[0] == "send" and args[-1] == PROMPT:
                raise dispatch.HcFailure("send_error", "main refused", [], 1, {})
            return super().__call__(hc_bin, args, timeout_s)

    fake = MainRefuseHc()
    fake.add(name=NAME, fg="agent", turn=5,
             labels={"loop": "1", "loop-agent": "claude"}, address="t50")
    app = FakeApp(hc=fake, panes=("t50",), rename_error=True)
    rc, res, led, td = run_case(fake, app, "dn-err-echo", expect_proc="claude",
                                display_name=DISPLAY)
    assert rc == 1 and res["error"]["kind"] == "hc_failed", res
    assert res["display_name"] == DISPLAY, res
    assert res["display_name_warning"]["reason"] == "rename_error", res
    print("T12 error result echoes display_name + warning (rename attempted) ok")

    # T13 未尝试 rename 不回显（Decision 8）：创建启动失败（naming 未跑），传了 display_name
    #     但 result 无 display_name 字段（尝试过 rename 才带）。
    fake = NamedFakeHc()
    fake.launch_send_error = True
    app = FakeApp(hc=fake)
    rc, res, led, td = run_case(fake, app, "dn-no-attempt", launch=LAUNCH, cwd="/proj",
                                expect_proc="claude", display_name=DISPLAY)
    assert rc == 1 and res["error"]["kind"] == "launch_failed", res
    assert "display_name" not in res, "naming 未跑不回显"
    assert led["display_name"] == DISPLAY, "台账建账仍记传入值"
    print("T13 rename not attempted (launch failed): no display_name in result, ledger keeps it ok")

    print("ALL DISPLAY-NAME TESTS PASSED")


if __name__ == "__main__":
    main()