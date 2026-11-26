#!/usr/bin/env python3
"""test_dispatch_lifecycle.py — 会话生命周期三 bug 测试（#46，ADR-0009/0011 修订，FakeHerdr 假世界）。

跑法：python3 tests/test_dispatch_lifecycle.py
缝：唯一业务缝 harness.run（CLI 契约，T5b/ADR-0021）；场景用 FakeHerdr 世界方法与旋钮构造，
断言只认 stdout 一行 JSON、退出码、台账/result 文件、假世界 sends/hc_calls/app_calls/gifts 日志。
覆盖：
  1 Bug1 命名会话独立于调用方：创建/重建的 hc run 必带 --no-parent（调用方背景终端退出不连带杀）。
  2 Bug3 创建路径领养先于等 agent 就绪：卡弹框（dialog）→ 会话已领养可见
    （workspace=adopted＋pane_adopted）＋error=dialog_blocked；主任务零发送、id 消耗；
    用户处理后新 id 走既有路径重试。
  3 Bug2 赠送 shell 清理竞态：3a 延迟 finished（tab.close land_after）→ 轮询后 rm 成功；
    3b 超时仍 live → gift_cleanup_warning(finish_timeout) 但整轮仍 adopted、主任务照发；
    3c rm 命令失败 → gift_cleanup_warning(rm_error) 同样不丢放行；
    3d 赠送终端条目已被收走（not_found）→ 视同清理完成，不 rm、无告警。

T5b（ADR-0021，#99）：完整轮用例迁 FakeHerdr＋harness.run（唯一 CLI 缝）。赠送 shell 地址
经假世界 gifts 日志取得（不硬编码自动 id）；延迟 finished 经 delay("tab.close", "land_after")、
条目被收走经 vanish、rm 失败经 fail("rm")；卡弹框经 will_launch(…, "dialog")。
常量与助手本文件自带，不 import 供应方文件（去重债归 R）。
"""
import tempfile
from pathlib import Path

import harness
from fake_herdr import FakeHerdr

PROMPT = "请实现某个功能\n第二行说明"
LAUNCH = "claude --dangerously-skip-permissions"
NAME = "claude-matt-loop"
DISPLAY = "LoopEng_测试任务"


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


def _runs(w):
    return [c for c in w.hc_calls if c[0] == "run"]


def _rms(w):
    return [c[1] for c in w.hc_calls if c[0] == "rm"]


def _gets(w, addr):
    return sum(1 for c in w.hc_calls if c[0] == "get" and c[1] == addr)


def _pane_landed(w, addr):
    """跑后经命令层入口只读复核 pane 已落地（T4 先例）。"""
    ls = w.app_json(None, ["ls"], 30)
    return any(t["terminal"] == addr and t["pane"]
               for sp in ls["spaces"] for tb in sp["tabs"] for t in tb["terminals"])


def main():
    # 1 Bug1：创建必带 --no-parent（命名会话无父链、跨轮持久，不随调用方终端死）
    w = FakeHerdr()
    w.add_space(name="main")
    w.will_launch(LAUNCH, "agent")
    rc, res, led, td = run_round(w, "life-noparent-1",
                                 launch=LAUNCH, cwd="/proj", expect_proc="claude")
    assert rc == 0 and res["ok"], res
    runs = _runs(w)
    assert len(runs) == 1 and "--no-parent" in runs[0], runs
    # 重建路径同形状
    w = FakeHerdr()
    w.add_terminal(address="t60", name=NAME, fg="wrong", status="idle", turn=3, labels={})
    w.add_space(name="main")
    w.will_launch(LAUNCH, "agent")
    rc, res, led, td = run_round(w, "life-noparent-rebuild",
                                 launch=LAUNCH, cwd="/proj", expect_proc="claude",
                                 rebuild=True)
    assert rc == 0 and res["ok"], res
    runs = _runs(w)
    assert len(runs) == 1 and "--no-parent" in runs[0], runs
    print("1 Bug1 no-parent: create/rebuild hc run carries --no-parent ok")

    # 2 Bug3：卡弹框 → 领养先于等就绪执行，会话可见（恒可见不变量），整轮停、不发主任务
    w = FakeHerdr()
    w.add_space(name="main")
    w.will_launch(LAUNCH, "dialog")
    rc, res, led, td = run_round(w, "life-dialog-1",
                                 launch=LAUNCH, cwd="/proj", expect_proc="claude")
    assert rc == 1 and res["error"]["kind"] == "dialog_blocked", res
    assert res["workspace"] == "adopted", "卡弹框也须领养可见，不随就绪失败丢可见性"
    assert res["launched"] is True and res["launch_sent"] is True, res
    assert res["pane_adopted"] is True, "pane 已经 keep 落地，如实报"
    new_addr = res["target_address"]
    assert any(c[:2] == ["do", "terminal.keep"] for c in w.app_calls), "领养须已经执行"
    assert _pane_landed(w, new_addr), "pane 已落地，用户看得见卡住的会话"
    assert w.sends == [(new_addr, LAUNCH, False)], "主任务零发送"
    assert led["phase"] == "error:dialog_blocked" and led["workspace"] == "adopted"
    # 放行 id 消耗（部分状态已发生）：同 id 重试拒；用户处理弹框后新 id 走既有路径
    rc, res2, _, _ = run_round(w, "life-dialog-1",
                               launch=LAUNCH, cwd="/proj", expect_proc="claude", td=td)
    assert rc == 1 and res2["error"]["kind"] == "duplicate_release", res2
    w.set_status(new_addr, "idle")   # 用户到屏幕前处理了弹框
    rc, res3, led3, _ = run_round(w, "life-dialog-fix", expect_proc="claude", td=td)
    assert rc == 0 and res3["ok"], res3
    assert res3["workspace"] == "adopted" and w.sends[-1] == (new_addr, PROMPT, True)
    print("2 Bug3 dialog-blocked: adopt before agent-ready, visible + dialog_blocked, new-id retry ok")

    # 3a Bug2：赠送终端延迟 finished（land_after=3）→ 轮询等 finished 再 rm，
    #    清理成功、无告警（旧行为单次 rm live → 退出码 1 → 误报 adopt_failed）
    path = tempfile.mkdtemp()
    w = FakeHerdr()
    w.add_terminal(address="t50", name=NAME, turn=5,
                   labels={"loop": "1", "loop-agent": "claude"})
    w.delay("tab.close", "land_after", after=3)
    rc, res, led, td = run_round(w, "life-gift-delay",
                                 expect_proc="claude", space=path)
    assert rc == 0 and res["ok"], res
    assert res["workspace"] == "adopted" and res["pane_adopted"] is True, res
    assert "gift_cleanup_warning" not in res, res
    gifted = w.gifts[0][2]
    assert _rms(w) == [gifted], _rms(w)
    assert _gets(w, gifted) >= 3, "须轮询等 finished，不是单次 rm 撞 live"
    assert w.sends == [("t50", PROMPT, True)], "清理成功，主任务照发"
    print("3a Bug2 gift delayed finish: poll until finished then rm, no warning ok")

    # 3b Bug2（创建路径）：轮询窗口耗尽仍 live → 降级 gift_cleanup_warning(finish_timeout)，
    #    不丢已成功的领养——整轮仍 adopted、主任务照发、台账留痕
    path = tempfile.mkdtemp()
    w = FakeHerdr()
    w.will_launch(LAUNCH, "agent")
    w.delay("tab.close", "land_after", after=1000000)
    rc, res, led, td = run_round(w, "life-gift-timeout",
                                 launch=LAUNCH, cwd="/proj", expect_proc="claude", space=path)
    assert rc == 0 and res["ok"], res
    assert res["workspace"] == "adopted" and res["pane_adopted"] is True, res
    assert res["gift_cleanup_warning"]["reason"] == "finish_timeout", res
    assert led["gift_cleanup_warning"]["reason"] == "finish_timeout", "台账留痕"
    new_addr = res["target_address"]
    assert w.sends == [(new_addr, LAUNCH, False), (new_addr, PROMPT, True)], \
        "赠送清理失败不丢放行"
    print("3b Bug2 gift finish timeout: gift_cleanup_warning, round still adopted ok")

    # 3c Bug2：终端已 finished 但 rm 命令失败 → 降级 rm_error，同样不丢放行
    path = tempfile.mkdtemp()
    w = FakeHerdr()
    w.add_terminal(address="t50", name=NAME, turn=5,
                   labels={"loop": "1", "loop-agent": "claude"})
    w.fail("rm", "error")
    rc, res, led, td = run_round(w, "life-gift-rmerr",
                                 expect_proc="claude", space=path)
    assert rc == 0 and res["ok"], res
    assert res["workspace"] == "adopted", res
    assert res["gift_cleanup_warning"]["reason"] == "rm_error", res
    assert w.sends == [("t50", PROMPT, True)]
    print("3c Bug2 gift rm failure: gift_cleanup_warning(rm_error), release kept ok")

    # 3d Bug2：赠送终端条目在清理前已被收走（get not_found）→ 视同清理完成，不 rm、无告警
    path = tempfile.mkdtemp()
    w = FakeHerdr()
    w.add_terminal(address="t50", name=NAME, turn=5,
                   labels={"loop": "1", "loop-agent": "claude"})
    w.delay("tab.close", "vanish")
    rc, res, led, td = run_round(w, "life-gift-vanish",
                                 expect_proc="claude", space=path)
    assert rc == 0 and res["ok"], res
    assert res["workspace"] == "adopted", res
    assert "gift_cleanup_warning" not in res, "条目已不在不是失败"
    assert _rms(w) == [], "无需 rm"
    assert w.sends == [("t50", PROMPT, True)]
    print("3d Bug2 gift entry already gone: treated as cleaned, no rm, no warning ok")

    print("ALL LIFECYCLE TESTS PASSED")


if __name__ == "__main__":
    main()
