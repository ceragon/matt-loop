#!/usr/bin/env python3
"""test_dispatch_display_name.py — 显示名层测试（ADR-0010，FakeHerdr 假世界）。

跑法：python3 tests/test_dispatch_display_name.py
缝：唯一业务缝 harness.run（CLI 契约，T5b/ADR-0021）；场景用 FakeHerdr 世界方法与旋钮构造，
断言只认 stdout 一行 JSON、退出码、台账/result 文件、假世界 sends/hc_calls/app_calls/command_log 日志。
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

T5b（ADR-0021，#99）：完整轮用例迁 FakeHerdr＋harness.run（唯一 CLI 缝）。跨 hc/app 时序
断言经假世界 command_log 日志（原跨桩 journal 的假世界侧出处；改名避开 ADR-0022 台账
Journal 域词）；rename 失败/不落地经 fail("tab.rename") error/noop；tab id 不暴露经
hide_tab_ids()；主任务拒发经 fail("send", nth=1)。
R（#104）：共享常量迁 helpers 单一出处。
"""
import json
import tempfile
from pathlib import Path

import harness
from fake_herdr import FakeHerdr
from helpers import PROMPT, LAUNCH, NAME, DISPLAY


def run_round(w, rid, *, target=NAME, launch=None, cwd=None, rebuild=False,
              expect_proc=None, reset=None, model=None, prompt=PROMPT,
              pre_ledger=False, wall_clock="5", td=None, display_name="auto", space="main"):
    """装配 argv＋经唯一 harness.run 跑一轮（不替换任何全局）。返回 (rc, result, ledger, td)。
    td 可传入复用（同 id 重试）。display_name="auto"：带 --launch 时自动补 DISPLAY。"""
    if td is None:
        td = tempfile.mkdtemp()
    pf = Path(td) / "prompt.md"
    pf.write_text(prompt, encoding="utf-8")
    ld = Path(td) / ".loop"
    if pre_ledger:
        (ld / "runs").mkdir(parents=True, exist_ok=True)
        (ld / "runs" / (rid + ".json")).write_text("{}", encoding="utf-8")
    argv = ["--target", target, "--release-id", rid,
            "--prompt-file", str(pf), "--loop-dir", str(ld),
            "--wall-clock", wall_clock, "--check-in", "1",
            "--settle-seconds", "0.01", "--settle-checks", "2",
            "--adopt-timeout", "0.05", "--adopt-poll", "0.001"]
    if launch is not None:
        argv += ["--launch", launch]
    if cwd is not None:
        argv += ["--cwd", cwd]
    if display_name == "auto":
        display_name = DISPLAY if launch is not None else None
    if display_name is not None:
        argv += ["--display-name", display_name]
    if rebuild:
        argv += ["--rebuild"]
    if expect_proc is not None:
        argv += ["--expect-proc", expect_proc]
    if reset is not None:
        argv += ["--reset", reset]
    if model is not None:
        argv += ["--target-model", model]
    if space is not None:
        argv += ["--space", space]
    r = harness.run(argv, world=w)
    if r.ledger is not None:
        assert r.result_path.read_text(encoding="utf-8").strip() == r.stdout.strip(), \
            "result 文件与 stdout 不一致"
    return r.rc, r.result, r.ledger, td


def _existing(w, address="t50", **kw):
    """既有 idle agent（loop 标签符）；kw 透传 add_terminal（fg/status 场景）。"""
    w.add_terminal(address=address, name=NAME, turn=5,
                   labels={"loop": "1", "loop-agent": "claude"}, **kw)


def _main_space(w, panes=()):
    """main 空间；panes 决定哪些终端已有 pane（空＝无标签页条目）。"""
    sp = w.add_space(name="main")
    if panes:
        w.add_tab(sp, terminals=list(panes))
    return sp


def _runs(w):
    return [c for c in w.hc_calls if c[0] == "run"]


def _kills(w):
    return [c[1] for c in w.hc_calls if c[0] == "kill"]


def main():
    # T1 创建缺 --display-name（launch/cwd 齐）：display_name_required rc2、
    #    零发送、无台账（id 不消耗）；补参同 id 重试成功
    w = FakeHerdr()
    w.add_space(name="main")
    w.will_launch(LAUNCH, "agent")
    td = tempfile.mkdtemp()
    rc, res, led, _ = run_round(w, "dn-create-req", launch=LAUNCH, cwd="/proj",
                                expect_proc="claude", display_name=None, td=td)
    assert rc == 2 and res["error"]["kind"] == "display_name_required", res
    assert _runs(w) == [] and w.sends == [] and led is None, "零发送、id 不消耗"
    rc, res, led, _ = run_round(w, "dn-create-req", launch=LAUNCH, cwd="/proj",
                                expect_proc="claude", display_name=DISPLAY, td=td)
    assert rc == 0 and res["ok"], res
    assert res["display_name"] == DISPLAY, res
    print("T1 create display_name_required: rc2 id unconsumed, same-id retry ok")

    # T2 复活/重建缺 --display-name：同 T1（rc2、零发送/零 kill、id 不消耗、补参同 id 重试）
    for scenario in ("revive", "rebuild"):
        w = FakeHerdr()
        if scenario == "revive":
            w.add_terminal(address="t50", name=NAME, fg="shell", turn=7,
                           labels={"loop": "1", "loop-agent": "claude"})
            _main_space(w, panes=("t50",))
            kw = {}
        else:
            w.add_terminal(address="t60", name=NAME, fg="wrong", status="idle",
                           turn=3, labels={})
            _main_space(w, panes=("t60",))
            kw = {"rebuild": True}
        w.will_launch(LAUNCH, "agent")
        td = tempfile.mkdtemp()
        rc, res, led, _ = run_round(w, "dn-%s-req" % scenario, launch=LAUNCH,
                                    cwd="/proj", expect_proc="claude", display_name=None,
                                    td=td, **kw)
        assert rc == 2 and res["error"]["kind"] == "display_name_required", (scenario, res)
        assert w.sends == [] and _runs(w) == [] and _kills(w) == [] and led is None, scenario
        rc, res, led, _ = run_round(w, "dn-%s-req" % scenario, launch=LAUNCH,
                                    cwd="/proj", expect_proc="claude", display_name=DISPLAY,
                                    td=td, **kw)
        assert rc == 0 and res["ok"] and res["display_name"] == DISPLAY, (scenario, res)
    print("T2 revive/rebuild display_name_required: rc2 id unconsumed, same-id retry ok")

    # T3 机械校验边界：空/65 字/含换行/含控制字符 → bad_args rc2、零发送、id 不消耗；
    #    任何分流都校验（创建＋既有两条路径都试）。64 字中文含空格通过；不校验式样。
    bad_names = ["", "x" * 65, "a\nb", "a\rb", "a\x1bb", "a\tb", "a\x00b"]
    for bad in bad_names:
        for path in ("create", "existing"):
            w = FakeHerdr()
            if path == "existing":
                _existing(w)
                _main_space(w, panes=("t50",))
                kw = {"expect_proc": "claude"}
            else:
                w.add_space(name="main")
                w.will_launch(LAUNCH, "agent")
                kw = {"launch": LAUNCH, "cwd": "/proj", "expect_proc": "claude"}
            rid = "dn-bad-%s-%d" % (path, bad_names.index(bad))
            rc, res, led, td = run_round(w, rid, display_name=bad, **kw)
            assert rc == 2 and res["error"]["kind"] == "bad_args", (path, repr(bad), res)
            assert w.sends == [] and _runs(w) == [] and led is None, (path, repr(bad))
    # 64 字中文含空格通过；无 bot 前缀的式样也通过（不校验式样，US10）
    for good in ["测" * 63 + " ", "随便什么名字"]:
        w = FakeHerdr()
        w.add_space(name="main")
        w.will_launch(LAUNCH, "agent")
        rc, res, led, td = run_round(w, "dn-good-%d" % len(good),
                                     launch=LAUNCH, cwd="/proj", expect_proc="claude",
                                     display_name=good)
        assert rc == 0 and res["ok"] and res["display_name"] == good, (repr(good), res)
    print("T3 validation boundaries: empty/65/newline/control -> bad_args rc2 any branch; 64-CJK+space ok")

    # T4 创建成功：调用序 领养→tab.rename→ls 复核→主发送；result display_name；台账 naming 痕迹。
    #    用假世界跨侧 command_log 断言时序（rename 在主任务发送之前）。
    w = FakeHerdr()
    w.add_space(name="main")
    w.will_launch(LAUNCH, "agent")
    rc, res, led, td = run_round(w, "dn-create-ok", launch=LAUNCH, cwd="/proj",
                                 expect_proc="claude", display_name=DISPLAY)
    assert rc == 0 and res["ok"], res
    assert res["display_name"] == DISPLAY and "display_name_warning" not in res, res
    assert led["display_name"] == DISPLAY and led.get("naming_done_at"), led
    # tab.rename 载荷 {id, name}，name 是放行原文
    renames = [c for c in w.app_calls if c[0] == "do" and c[1] == "tab.rename"]
    assert len(renames) == 1, w.app_calls
    assert json.loads(renames[0][2])["name"] == DISPLAY, renames
    # 时序：领养 keep → tab.rename → ls 复核 → 主任务发送（command_log 跨 hc/app 两侧）
    command_log = w.command_log
    keep_i = command_log.index(("app", "do", "terminal.keep"))
    rename_i = command_log.index(("app", "do", "tab.rename"))
    last_send_i = len(command_log) - 1 - command_log[::-1].index(("hc", "send"))
    assert keep_i < rename_i < last_send_i, command_log
    # rename 之后有 ls 复核（命令成功不算数）
    assert command_log[rename_i + 1] == ("app", "ls", None), command_log
    print("T4 create success: adopt->tab.rename->ls recheck->main send order, result+ledger ok")

    # T5 rename 命令失败 → ok:true、主任务照发、warning.reason=rename_error（不丢放行）
    w = FakeHerdr()
    w.add_space(name="main")
    w.will_launch(LAUNCH, "agent")
    w.fail("tab.rename", "error")
    rc, res, led, td = run_round(w, "dn-rename-err", launch=LAUNCH, cwd="/proj",
                                 expect_proc="claude", display_name=DISPLAY)
    assert rc == 0 and res["ok"], res
    assert res["display_name"] == DISPLAY, res
    assert res["display_name_warning"]["reason"] == "rename_error", res
    assert led["display_name_warning"]["reason"] == "rename_error", led
    new_addr = res["target_address"]
    assert w.sends[-1] == (new_addr, PROMPT, True), "主任务照发"
    print("T5 rename command failure: ok, main sent, warning rename_error ok")

    # T6 复核名不符 → warning rename_mismatch（带实际名）、ok:true。既有路径预置旧名，
    #    rename noop 命令成功但名不落地 → 复核读到旧名。
    w = FakeHerdr()
    _existing(w)
    sp = w.add_space(id="sp1", name="main")
    w.add_tab(sp, id="tb1", name="旧名", terminals=["t50"])
    w.fail("tab.rename", "noop")
    rc, res, led, td = run_round(w, "dn-mismatch", expect_proc="claude",
                                 display_name=DISPLAY)
    assert rc == 0 and res["ok"], res
    warn = res["display_name_warning"]
    assert warn["reason"] == "rename_mismatch" and warn["actual"] == "旧名", warn
    assert w.sends[-1] == ("t50", PROMPT, True), "主任务照发"
    print("T6 recheck mismatch: ok, warning rename_mismatch with actual name ok")

    # T9 定位不到 tab → warning no_tab、主流程继续（hide_tab_ids：ls 不暴露 tab id）
    w = FakeHerdr()
    w.add_space(name="main")
    w.will_launch(LAUNCH, "agent")
    w.hide_tab_ids()
    rc, res, led, td = run_round(w, "dn-no-tab", launch=LAUNCH, cwd="/proj",
                                 expect_proc="claude", display_name=DISPLAY)
    assert rc == 0 and res["ok"], res
    assert res["display_name_warning"]["reason"] == "no_tab", res
    assert res["display_name"] == DISPLAY, res
    new_addr = res["target_address"]
    assert w.sends[-1] == (new_addr, PROMPT, True), "主流程继续"
    print("T9 tab not locatable: ok, warning no_tab, main flow continues ok")

    # T7 复用轮不传 → 零 tab.rename、result 无 display_name（向后兼容，US28）
    w = FakeHerdr()
    _existing(w)
    _main_space(w, panes=("t50",))
    rc, res, led, td = run_round(w, "dn-reuse-none", expect_proc="claude",
                                 display_name=None)
    assert rc == 0 and res["ok"], res
    assert "display_name" not in res and "display_name_warning" not in res, res
    assert not any(c[0] == "do" and c[1] == "tab.rename" for c in w.app_calls), w.app_calls
    assert led["display_name"] is None, led
    print("T7 reuse without display-name: zero tab.rename, no display_name field ok")

    # T8 复用轮传 → tab.rename 恰一次（覆盖，无对比逻辑）。预置旧名→覆盖；预置同名→仍 rename（不跳过）。
    for preset in ("旧任务名", DISPLAY):
        w = FakeHerdr()
        _existing(w)
        sp = w.add_space(id="sp1", name="main")
        w.add_tab(sp, id="tb1", name=preset, terminals=["t50"])
        rc, res, led, td = run_round(w, "dn-reuse-set-%s" % ("same" if preset == DISPLAY else "diff"),
                                     expect_proc="claude", display_name=DISPLAY)
        assert rc == 0 and res["ok"], res
        renames = [c for c in w.app_calls if c[0] == "do" and c[1] == "tab.rename"]
        assert len(renames) == 1, (preset, w.app_calls)
        assert json.loads(renames[0][2])["name"] == DISPLAY, renames
        assert res["display_name"] == DISPLAY and "display_name_warning" not in res, res
    print("T8 reuse with display-name: exactly one tab.rename (overwrite, no compare/skip) ok")

    # T11 两参同缺（launch＋display_name）：launch_required 先报（预检顺序，Decision 3）
    w = FakeHerdr()
    w.add_space(name="main")
    rc, res, led, td = run_round(w, "dn-both-missing", display_name=None)
    assert rc == 2 and res["error"]["kind"] == "launch_required", res
    assert _runs(w) == [] and led is None
    print("T11 both launch+display missing: launch_required reported first ok")

    # T12 error 也回显 display_name＋warning（US17）：既有路径 rename 告警后主任务发送失败
    w = FakeHerdr()
    _existing(w)
    _main_space(w, panes=("t50",))
    w.fail("tab.rename", "error")
    w.fail("send", "error", kind="send_error", nth=1)   # 既有路径唯一发送＝主任务
    rc, res, led, td = run_round(w, "dn-err-echo", expect_proc="claude",
                                 display_name=DISPLAY)
    assert rc == 1 and res["error"]["kind"] == "hc_failed", res
    assert res["display_name"] == DISPLAY, res
    assert res["display_name_warning"]["reason"] == "rename_error", res
    print("T12 error result echoes display_name + warning (rename attempted) ok")

    # T13 未尝试 rename 不回显（Decision 8）：创建启动失败（naming 未跑），传了 display_name
    #     但 result 无 display_name 字段（尝试过 rename 才带）。
    w = FakeHerdr()
    w.add_space(name="main")
    w.fail("send", "error", nth=1)   # 首次发送＝启动命令
    rc, res, led, td = run_round(w, "dn-no-attempt", launch=LAUNCH, cwd="/proj",
                                 expect_proc="claude", display_name=DISPLAY)
    assert rc == 1 and res["error"]["kind"] == "launch_failed", res
    assert "display_name" not in res, "naming 未跑不回显"
    assert led["display_name"] == DISPLAY, "台账建账仍记传入值"
    print("T13 rename not attempted (launch failed): no display_name in result, ledger keeps it ok")

    print("ALL DISPLAY-NAME TESTS PASSED")


if __name__ == "__main__":
    main()
