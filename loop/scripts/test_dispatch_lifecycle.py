#!/usr/bin/env python3
"""test_dispatch_lifecycle.py — 会话生命周期三 bug 测试（#46，ADR-0009/0011 修订，mock hc/herdr-app）。

跑法：python3 loop/scripts/test_dispatch_lifecycle.py
缝：分发脚本 CLI 契约（与 get_or_create/adopt/space 同一缝，NamedFakeHc/FakeApp 复用）。
覆盖：
  1 Bug1 命名会话独立于调用方：创建/重建的 hc run 必带 --no-parent（调用方背景终端退出不连带杀）。
  2 Bug3 创建路径领养先于等 agent 就绪：卡弹框（dialog）→ 会话已领养可见
    （workspace=adopted＋pane_adopted）＋error=dialog_blocked；主任务零发送、id 消耗；
    用户处理后新 id 走既有路径重试。
  3 Bug2 赠送 shell 清理竞态：3a 延迟 finished（gift_finish_delay）→ 轮询后 rm 成功；
    3b 超时仍 live → gift_cleanup_warning(finish_timeout) 但整轮仍 adopted、主任务照发；
    3c rm 命令失败 → gift_cleanup_warning(rm_error) 同样不丢放行；
    3d 赠送终端条目已被收走（not_found）→ 视同清理完成，不 rm、无告警。
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dispatch  # noqa: E402,F401
import os  # noqa: E402
os.environ["LOOP_EXTRACT_BACKEND"] = "pi"

from test_dispatch_get_or_create import (  # noqa: E402
    PROMPT, LAUNCH, NAME, NamedFakeHc, FakeApp, run_case,
)


def main():
    # 1 Bug1：创建必带 --no-parent（命名会话无父链、跨轮持久，不随调用方终端死）
    fake = NamedFakeHc()
    app = FakeApp(hc=fake)
    rc, res, led, td = run_case(fake, app, "life-noparent-1",
                                launch=LAUNCH, cwd="/proj", expect_proc="claude")
    assert rc == 0 and res["ok"], res
    assert len(fake.runs) == 1 and "--no-parent" in fake.runs[0], fake.runs
    # 重建路径同形状
    fake = NamedFakeHc()
    fake.add(name=NAME, fg="agent", app="grok", st="idle", turn=3,
             labels={}, address="t60")
    app = FakeApp(hc=fake)
    rc, res, led, td = run_case(fake, app, "life-noparent-rebuild",
                                launch=LAUNCH, cwd="/proj", expect_proc="claude",
                                rebuild=True)
    assert rc == 0 and res["ok"], res
    assert len(fake.runs) == 1 and "--no-parent" in fake.runs[0], fake.runs
    print("1 Bug1 no-parent: create/rebuild hc run carries --no-parent ok")

    # 2 Bug3：卡弹框 → 领养先于等就绪执行，会话可见（恒可见不变量），整轮停、不发主任务
    fake = NamedFakeHc()
    fake.launch_effect = "dialog"
    app = FakeApp(hc=fake)
    rc, res, led, td = run_case(fake, app, "life-dialog-1",
                                launch=LAUNCH, cwd="/proj", expect_proc="claude")
    assert rc == 1 and res["error"]["kind"] == "dialog_blocked", res
    assert res["workspace"] == "adopted", "卡弹框也须领养可见，不随就绪失败丢可见性"
    assert res["launched"] is True and res["launch_sent"] is True, res
    assert res["pane_adopted"] is True, "pane 已经 keep 落地，如实报"
    new_addr = res["target_address"]
    assert any(c[:2] == ["do", "terminal.keep"] for c in app.calls), "领养须已经执行"
    assert new_addr in app.panes, "pane 已落地，用户看得见卡住的会话"
    assert fake.sends == [(new_addr, LAUNCH, False)], "主任务零发送"
    assert led["phase"] == "error:dialog_blocked" and led["workspace"] == "adopted"
    # 放行 id 消耗（部分状态已发生）：同 id 重试拒；用户处理弹框后新 id 走既有路径
    rc, res2, _, _ = run_case(fake, app, "life-dialog-1",
                              launch=LAUNCH, cwd="/proj", expect_proc="claude", td=td)
    assert rc == 1 and res2["error"]["kind"] == "duplicate_release", res2
    fake.terminals[new_addr]["status"]["state"] = "idle"   # 用户到屏幕前处理了弹框
    app2 = FakeApp(hc=fake, panes=(new_addr,))
    rc, res3, led3, _ = run_case(fake, app2, "life-dialog-fix",
                                 expect_proc="claude", td=td)
    assert rc == 0 and res3["ok"], res3
    assert res3["workspace"] == "adopted" and fake.sends[-1] == (new_addr, PROMPT, True)
    print("2 Bug3 dialog-blocked: adopt before agent-ready, visible + dialog_blocked, new-id retry ok")

    # 3a Bug2：赠送终端延迟 finished（gift_finish_delay=3）→ 轮询等 finished 再 rm，
    #    清理成功、无告警（旧行为单次 rm live → 退出码 1 → 误报 adopt_failed）
    path = tempfile.mkdtemp()
    fake = NamedFakeHc()
    fake.add(name=NAME, fg="agent", turn=5, address="t50",
             labels={"loop": "1", "loop-agent": "claude"})
    app = FakeApp(hc=fake, panes=(), spaces=[], gift_finish_delay=3)
    rc, res, led, td = run_case(fake, app, "life-gift-delay",
                                expect_proc="claude", space=path)
    assert rc == 0 and res["ok"], res
    assert res["workspace"] == "adopted" and res["pane_adopted"] is True, res
    assert "gift_cleanup_warning" not in res, res
    assert fake.rms == [app.gifted], fake.rms
    assert fake.gets.count(app.gifted) >= 3, "须轮询等 finished，不是单次 rm 撞 live"
    assert fake.sends == [("t50", PROMPT, True)], "清理成功，主任务照发"
    print("3a Bug2 gift delayed finish: poll until finished then rm, no warning ok")

    # 3b Bug2（创建路径）：轮询窗口耗尽仍 live → 降级 gift_cleanup_warning(finish_timeout)，
    #    不丢已成功的领养——整轮仍 adopted、主任务照发、台账留痕
    path = tempfile.mkdtemp()
    fake = NamedFakeHc()
    app = FakeApp(hc=fake, spaces=[], gift_finish_delay=1000000)
    rc, res, led, td = run_case(fake, app, "life-gift-timeout",
                                launch=LAUNCH, cwd="/proj", expect_proc="claude", space=path)
    assert rc == 0 and res["ok"], res
    assert res["workspace"] == "adopted" and res["pane_adopted"] is True, res
    assert res["gift_cleanup_warning"]["reason"] == "finish_timeout", res
    assert led["gift_cleanup_warning"]["reason"] == "finish_timeout", "台账留痕"
    new_addr = res["target_address"]
    assert fake.sends == [(new_addr, LAUNCH, False), (new_addr, PROMPT, True)], \
        "赠送清理失败不丢放行"
    print("3b Bug2 gift finish timeout: gift_cleanup_warning, round still adopted ok")

    # 3c Bug2：终端已 finished 但 rm 命令失败 → 降级 rm_error，同样不丢放行
    path = tempfile.mkdtemp()
    fake = NamedFakeHc()
    fake.add(name=NAME, fg="agent", turn=5, address="t50",
             labels={"loop": "1", "loop-agent": "claude"})
    fake.rm_error = True
    app = FakeApp(hc=fake, panes=(), spaces=[])
    rc, res, led, td = run_case(fake, app, "life-gift-rmerr",
                                expect_proc="claude", space=path)
    assert rc == 0 and res["ok"], res
    assert res["workspace"] == "adopted", res
    assert res["gift_cleanup_warning"]["reason"] == "rm_error", res
    assert fake.sends == [("t50", PROMPT, True)]
    print("3c Bug2 gift rm failure: gift_cleanup_warning(rm_error), release kept ok")

    # 3d Bug2：赠送终端条目在清理前已被收走（get not_found）→ 视同清理完成，不 rm、无告警
    path = tempfile.mkdtemp()
    fake = NamedFakeHc()
    fake.add(name=NAME, fg="agent", turn=5, address="t50",
             labels={"loop": "1", "loop-agent": "claude"})
    app = FakeApp(hc=fake, panes=(), spaces=[], gift_vanish=True)
    rc, res, led, td = run_case(fake, app, "life-gift-vanish",
                                expect_proc="claude", space=path)
    assert rc == 0 and res["ok"], res
    assert res["workspace"] == "adopted", res
    assert "gift_cleanup_warning" not in res, "条目已不在不是失败"
    assert fake.rms == [], "无需 rm"
    assert fake.sends == [("t50", PROMPT, True)]
    print("3d Bug2 gift entry already gone: treated as cleaned, no rm, no warning ok")

    print("ALL LIFECYCLE TESTS PASSED")


if __name__ == "__main__":
    main()