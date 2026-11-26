#!/usr/bin/env python3
"""test_dispatch_adopt.py — 领养／恒可见不变量测试（ADR-0009，FakeHerdr 假世界）。

跑法：python3 tests/test_dispatch_adopt.py
缝：唯一业务缝 harness.run（CLI 契约，T5b/ADR-0021）；场景用 FakeHerdr 世界方法与旋钮构造，
断言只认 stdout 一行 JSON、退出码、台账/result 文件、假世界 sends/hc_calls/app_calls/gifts 日志。
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

T5b（ADR-0021，#99）：完整轮用例迁 FakeHerdr＋harness.run（唯一 CLI 缝）。app 没开/重新打开
经 app_closed()/app_opened() 事件；keep 失败/无 pane 经 fail("terminal.keep") error/noop
（nth=1 使失败后重试成功）；赠送 shell/tab/空间 id 经假世界 gifts 日志取得（不硬编码自动 id）。
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


def _rms(w):
    return [c[1] for c in w.hc_calls if c[0] == "rm"]


def main():
    # 1 既有路径 pane 在：只读探测，不 keep
    w = FakeHerdr()
    _existing(w)
    _main_space(w, panes=("t50",))
    rc, res, led, td = run_round(w, "adopt-have-1", expect_proc="claude")
    assert rc == 0 and res["ok"], res
    assert [c[0] for c in w.app_calls] == ["status", "ls"], w.app_calls
    assert res["workspace"] == "adopted" and res["target_address"] == "t50"
    assert w.sends == [("t50", PROMPT, True)], w.sends
    print("1 existing path pane present: read-only status+ls, no keep, workspace adopted ok")

    # 2 既有路径 pane 缺：预检补领养成功（keep＋复核），主任务照发
    w = FakeHerdr()
    _existing(w)
    _main_space(w)
    rc, res, led, td = run_round(w, "adopt-fix-1", expect_proc="claude")
    assert rc == 0 and res["ok"], res
    assert [c[0] for c in w.app_calls] == ["status", "ls", "do", "ls"], w.app_calls
    assert w.app_calls[2][1] == "terminal.keep"
    assert json.loads(w.app_calls[2][2]) == {"terminal": "t50"}
    assert res["workspace"] == "adopted"
    assert w.sends == [("t50", PROMPT, True)], w.sends
    print("2 existing path pane missing: precheck re-adopt (keep+recheck), main sent ok")

    # 3 既有路径 app 没开：adopt_failed(app_closed)，零发送、id 不消耗；同 id 重试修复
    w = FakeHerdr()
    _existing(w)
    _main_space(w, panes=("t50",))
    w.app_closed()
    td = tempfile.mkdtemp()
    rc, res, led, _ = run_round(w, "adopt-closed-1", expect_proc="claude", td=td)
    assert rc == 1 and res["error"]["kind"] == "adopt_failed", res
    assert res["adopt_reason"] == "app_closed" and res["workspace"] == "failed", res
    assert w.sends == [] and led is None, "预检失败零发送、id 不消耗"
    w.app_opened()
    rc, res, led, _ = run_round(w, "adopt-closed-1", expect_proc="claude", td=td)
    assert rc == 0 and res["ok"], res
    print("3 existing path app closed: adopt_failed app_closed, id unconsumed, same-id retry ok")

    # 4 既有路径 keep 失败／复核无 pane：adopt_failed，id 不消耗
    for mode, reason in (("error", "keep_error"), ("noop", "no_pane")):
        w = FakeHerdr()
        _existing(w)
        _main_space(w)
        w.fail("terminal.keep", mode)
        rc, res, led, td = run_round(w, "adopt-%s-1" % reason, expect_proc="claude")
        assert rc == 1 and res["error"]["kind"] == "adopt_failed", (mode, res)
        assert res["adopt_reason"] == reason and res["workspace"] == "failed", res
        assert w.sends == [] and led is None, mode
    print("4 existing path keep_error/no_pane: adopt_failed, id unconsumed ok")

    # 5 创建路径领养失败三原因：保留已建终端、id 消耗；新 id 重试走既有路径修复
    for setup, reason in (("closed", "app_closed"), ("error", "keep_error"),
                          ("noop", "no_pane")):
        w = FakeHerdr()
        _main_space(w)
        w.will_launch(LAUNCH, "agent")
        if setup == "closed":
            w.app_closed()
        else:
            w.fail("terminal.keep", setup, nth=1)   # 只首次 keep 失败，重试成功
        rc, res, led, td = run_round(w, "adopt-create-%s" % reason,
                                     launch=LAUNCH, cwd="/proj", expect_proc="claude")
        assert rc == 1 and res["error"]["kind"] == "adopt_failed", (setup, res)
        assert res["adopt_reason"] == reason and res["workspace"] == "failed", res
        assert res["launched"] is True and res["launch_sent"] is True, res
        new_addr = res["target_address"]
        assert new_addr and w.hc_json(None, ["get", new_addr], 30)["state"] == "live", \
            "保留已建终端"
        assert led is not None and led["phase"] == "error:adopt_failed", "id 消耗、台账停领养失败"
        assert led["launched"] is True and led["workspace"] == "failed"
        # 主任务零发送（启动命令已发、占前置槽）
        assert w.sends == [(new_addr, LAUNCH, False)], w.sends
        # 开 app 后新放行 id 重试：既有路径补领养修复，主任务照发
        if setup == "closed":
            w.app_opened()
        rc2, res2, led2, _ = run_round(w, "adopt-create-%s-fix" % reason,
                                       expect_proc="claude", td=td)
        assert rc2 == 0 and res2["ok"], (setup, res2)
        assert res2["workspace"] == "adopted"
        assert w.sends[-1] == (new_addr, PROMPT, True), w.sends
    print("5 create-path adopt failures: terminal kept, id consumed, new-id retry repairs ok")

    # 6 零空间统一走 --space 路径（#43 定稿）：space.new(name=目录名)→keep→复核→tab.close＋hc rm
    path = tempfile.mkdtemp()
    w = FakeHerdr()
    w.will_launch(LAUNCH, "agent")
    rc, res, led, td = run_round(w, "adopt-nospace-1",
                                 launch=LAUNCH, cwd="/proj", expect_proc="claude", space=path)
    assert rc == 0 and res["ok"], res
    kinds = [(c[0], c[1] if c[0] == "do" else None) for c in w.app_calls]
    # 建后 status 复核 shown（零空间新空间即显示，目标==shown 不 select）；
    # 清理舞后接显示名落地（ADR-0010，run_round 自动注入 DISPLAY）：ls→tab.rename→ls
    assert kinds == [("status", None), ("ls", None), ("do", "space.new"), ("status", None),
                     ("do", "terminal.keep"), ("ls", None), ("do", "tab.close"),
                     ("ls", None), ("do", "tab.rename"), ("ls", None)], kinds
    sp_new = json.loads(w.app_calls[2][2])
    assert sp_new == {"name": Path(path).name, "folder": path, "focus": False}, sp_new
    sp_new_id, gift_tab, gifted = w.gifts[0]
    tab_close = json.loads(w.app_calls[6][2])
    assert tab_close == {"id": gift_tab, "confirmed": True}, tab_close
    assert _rms(w) == [gifted], _rms(w)
    assert res["workspace"] == "adopted"
    print("6 zero-space via --space path: space.new(dirname,focus:false)->keep->tab.close+rm ok")

    # 6b 建空间中途失败：space.new 失败（空间未建，adopt_failed）／tab.close 失败
    #    （pane 已落地、空间已建 → #46 Bug2 降级 gift_cleanup_warning，不丢放行）
    path = tempfile.mkdtemp()
    w = FakeHerdr()
    w.will_launch(LAUNCH, "agent")
    w.fail("space.new", "error")
    rc, res, led, td = run_round(w, "adopt-nospace-sperr",
                                 launch=LAUNCH, cwd="/proj", expect_proc="claude", space=path)
    assert rc == 1 and res["error"]["kind"] == "adopt_failed", res
    assert res["adopt_reason"] == "keep_error" and res["workspace"] == "failed"
    assert "space_created" not in res, "空间未建如实报"
    assert res["launched"] is True
    path = tempfile.mkdtemp()
    w = FakeHerdr()
    w.will_launch(LAUNCH, "agent")
    w.fail("tab.close", "error")
    rc, res, led, td = run_round(w, "adopt-nospace-taberr",
                                 launch=LAUNCH, cwd="/proj", expect_proc="claude", space=path)
    assert rc == 0 and res["ok"], res
    assert res["workspace"] == "adopted", "真终端已可见，整轮不判 failed（#46 Bug2）"
    assert res["space_created"] is True and res["pane_adopted"] is True, res
    assert res["gift_cleanup_warning"]["reason"] == "tab_close_error", res
    assert _rms(w) == [], "tab.close 失败不应 rm"
    assert w.sends[-1] == (res["target_address"], PROMPT, True), "主任务照发"
    print("6b space-create mid-failure: space.new error fails; tab.close error degrades to warning ok")

    # 6c 既有路径补领养遇零空间：同一套舞，预检失败仍 id 不消耗
    path = tempfile.mkdtemp()
    w = FakeHerdr()
    _existing(w)
    w.fail("terminal.keep", "error")
    rc, res, led, td = run_round(w, "adopt-nospace-existing", expect_proc="claude",
                                 space=path)
    assert rc == 1 and res["error"]["kind"] == "adopt_failed", res
    assert res["space_created"] is True and led is None
    assert w.sends == []
    print("6c existing-path zero-space re-adopt failure: space created reported, id unconsumed ok")

    # 7 预检顺序：身份核对（分流）先于核 pane（标签符但前台身份不符＋app 没开 →
    #   session_occupied，不探 app）；核 pane 先于 idle 核对（working＋app 没开 → adopt_failed）
    w = FakeHerdr()
    _existing(w, fg="wrong", status="idle")
    w.app_closed()
    rc, res, led, td = run_round(w, "adopt-order-id", expect_proc="claude")
    assert rc == 1 and res["error"]["kind"] == "session_occupied", res
    assert w.app_calls == [], "身份不符不应探 app"
    w = FakeHerdr()
    _existing(w, status="working")
    w.app_closed()
    rc, res, led, td = run_round(w, "adopt-order-idle", expect_proc="claude")
    assert rc == 1 and res["error"]["kind"] == "adopt_failed", res
    print("7 precheck order: identity before pane, pane before idle ok")

    # 8 app 探测绝不用裸 herdr-app：所有调用第一个元素是子命令
    w = FakeHerdr()
    _existing(w)
    _main_space(w)
    rc, res, led, td = run_round(w, "adopt-probe-1", expect_proc="claude")
    assert rc == 0 and all(c and c[0] in ("status", "ls", "do") for c in w.app_calls)
    assert w.app_calls[0] == ["status"], "探针是 herdr-app status，不是裸 herdr-app"
    print("8 app probe uses status subcommand, never bare herdr-app ok")

    # 9 复活也过预检 pane 闸（US26：app 没开所有轮次停摆，无隐性例外）：
    #   app 没开→adopt_failed、零发送、id 不消耗、同 id 重试；pane 缺→补领养后才启动
    w = FakeHerdr()
    w.add_terminal(address="t50", name=NAME, fg="shell", turn=7,
                   labels={"loop": "1", "loop-agent": "claude"})
    _main_space(w, panes=("t50",))
    w.will_launch(LAUNCH, "agent")
    w.app_closed()
    td = tempfile.mkdtemp()
    rc, res, led, _ = run_round(w, "adopt-revive-closed", launch=LAUNCH, cwd="/proj",
                                expect_proc="claude", td=td)
    assert rc == 1 and res["error"]["kind"] == "adopt_failed", res
    assert res["adopt_reason"] == "app_closed" and w.sends == [] and led is None
    w.app_opened()
    rc, res, led, _ = run_round(w, "adopt-revive-closed", launch=LAUNCH, cwd="/proj",
                                expect_proc="claude", td=td)
    assert rc == 0 and res["ok"], res
    w2 = FakeHerdr()
    w2.add_terminal(address="t50", name=NAME, fg="shell", turn=7,
                    labels={"loop": "1", "loop-agent": "claude"})
    w2.add_space(name="main")
    w2.will_launch(LAUNCH, "agent")
    rc, res, led, td = run_round(w2, "adopt-revive-fix", launch=LAUNCH, cwd="/proj",
                                 expect_proc="claude")
    assert rc == 0 and res["ok"], res
    # 预检补领养 [status,ls,do,ls] ＋启动后显示名落地 [ls,tab.rename,ls]（ADR-0010）
    assert [c[0] for c in w2.app_calls] == ["status", "ls", "do", "ls", "ls", "do", "ls"], w2.app_calls
    assert w2.sends == [("t50", LAUNCH, False), ("t50", PROMPT, True)], w2.sends
    assert res["workspace"] == "adopted"
    print("9 revive pane gate: app closed stops round (id unconsumed), missing pane repaired ok")

    print("ALL ADOPT TESTS PASSED")


if __name__ == "__main__":
    main()
