#!/usr/bin/env python3
"""test_snapshot_routes.py — S 片特征化快照：五路分流（ADR-0021 决定 2/5）。

跑法：python3 tests/test_snapshot_routes.py            # 与金样逐字比对
      python3 tests/test_snapshot_routes.py --update   # 首次生成/显式更新基线

缝：唯一业务缝 harness.run（CLI 契约）＋ FakeHerdr；SnapshotWorld 在每条命令到达时
读一次磁盘台账（ADR-0022 决定 5），把「先记账后发送」的时序钉进金样。断言只认外部
四面：stdout JSON、.result.json、.notify.json、台账终态与逐命令增量；时间戳与临时
路径归一化后逐字比对。分发脚本零改动、Deps 零新增字段。

覆盖五路分流的成功路径＋关键故障点：
  1 既有成功（不建不 kill，发送/等待/回读/抽取全链）
  2 创建成功（run→label→等 shell→启动→领养→显示名→主任务）
  3 复活成功（原地重发启动命令，不 run/label/kill）
  4 强制重建成功（kill 占位者→同名创建）
  5 占位呈报（session_occupied，不建不 kill、零发送、id 不消耗）
  6-8 创建静态闸：launch_required / display_name_required / 非法会话名 bad_args
  9 duplicate_release 第一闸（零命令、id 不消耗）
  10 启动命令发送失败（launch_failed 部分状态）
  11 等 agent 就绪超时（launch_failed，启动已发）
  12 rebuild_busy（working 占位者拒杀）
  13 既有但非 idle（not_idle）
  14 --rebuild 误传既有分流（bad_args）
"""
import tempfile
from pathlib import Path

import helpers
import snapshot
from fake_herdr import FakeHerdr
from helpers import EXT, LAUNCH, NAME, DISPLAY, SCREEN

UPDATE = snapshot.update_requested()


def case(name, world, td, *, rid, target="t99", extra=(),
         screen_after_run=None, wall_clock="5"):
    """装配 argv、经 SnapshotWorld 跑一轮、比对快照；返回 harness 结果。"""
    a = snapshot.argv(td, rid, target=target, extra=extra, wall_clock=wall_clock)
    return snapshot.run_case(
        name, world, td, a, update=UPDATE,
        extract=helpers.ext(EXT), screen_after_run=screen_after_run)


def _create_world(effect="agent"):
    w = FakeHerdr()
    w.add_space(name="main")
    if effect is not None:
        w.will_launch(LAUNCH, effect)
    return w


def _fresh_extra():
    return ["--launch", LAUNCH, "--cwd", "/proj",
            "--display-name", DISPLAY, "--expect-proc", "claude"]


def main():
    # 1 既有成功
    w = helpers.world()
    with tempfile.TemporaryDirectory() as td:
        r = case("existing_success", w, td, rid="snap-existing-1")
        assert r.rc == 0, r.result
    print("1 existing success snapshot ok")

    # 2 创建成功
    w = _create_world("agent")
    with tempfile.TemporaryDirectory() as td:
        r = case("create_success", w, td, rid="snap-create-1", target=NAME,
                 extra=_fresh_extra(), screen_after_run=SCREEN)
        assert r.rc == 0, r.result
    print("2 create success snapshot ok")

    # 3 复活成功
    w = FakeHerdr()
    w.add_terminal(address="t50", name=NAME, fg="shell", turn=7,
                   labels={"loop": "1", "loop-agent": "claude"})
    sp = w.add_space(name="main")
    w.add_tab(sp, terminals=["t50"])
    w.screen("t50", SCREEN)
    w.will_launch(LAUNCH, "agent")
    with tempfile.TemporaryDirectory() as td:
        r = case("revive_success", w, td, rid="snap-revive-1", target=NAME,
                 extra=_fresh_extra())
        assert r.rc == 0, r.result
    print("3 revive success snapshot ok")

    # 4 强制重建成功
    w = FakeHerdr()
    w.add_terminal(address="t60", name=NAME, fg="wrong", status="idle",
                   turn=3, labels={})
    w.add_space(name="main")
    w.will_launch(LAUNCH, "agent")
    with tempfile.TemporaryDirectory() as td:
        r = case("rebuild_success", w, td, rid="snap-rebuild-1", target=NAME,
                 extra=_fresh_extra() + ["--rebuild"], screen_after_run=SCREEN)
        assert r.rc == 0, r.result
    print("4 rebuild success snapshot ok")

    # 5 占位呈报
    w = FakeHerdr()
    w.add_terminal(address="t60", name=NAME, fg="wrong", status="idle",
                   turn=3, labels={})
    w.add_space(name="main")
    with tempfile.TemporaryDirectory() as td:
        r = case("occupied_reported", w, td, rid="snap-occupied-1", target=NAME)
        assert r.rc == 1 and r.result["error"]["kind"] == "session_occupied"
    print("5 occupied reported snapshot ok")

    # 6 创建缺 --launch：launch_required
    w = _create_world("agent")
    with tempfile.TemporaryDirectory() as td:
        r = case("static_launch_required", w, td, rid="snap-launch-req-1",
                 target=NAME)
        assert r.rc == 2 and r.result["error"]["kind"] == "launch_required"
    print("6 launch_required snapshot ok")

    # 7 新鲜路径缺 --display-name：display_name_required
    w = _create_world("agent")
    with tempfile.TemporaryDirectory() as td:
        r = case("static_display_name_required", w, td, rid="snap-dname-req-1",
                 target=NAME, extra=["--launch", LAUNCH, "--cwd", "/proj"])
        assert r.rc == 2 and r.result["error"]["kind"] == "display_name_required"
    print("7 display_name_required snapshot ok")

    # 8 非法会话名：bad_args
    w = _create_world("agent")
    with tempfile.TemporaryDirectory() as td:
        r = case("static_bad_session_name", w, td, rid="snap-badname-1",
                 target="Bad_Name", extra=_fresh_extra())
        assert r.rc == 2 and r.result["error"]["kind"] == "bad_args"
    print("8 bad session name snapshot ok")

    # 9 duplicate_release 第一闸：预置台账，零命令、id 不消耗
    w = helpers.world()
    with tempfile.TemporaryDirectory() as td:
        runs = Path(td) / ".loop" / "runs"
        runs.mkdir(parents=True)
        (runs / "snap-dup-1.json").write_text("{}", encoding="utf-8")
        r = case("static_duplicate_release", w, td, rid="snap-dup-1")
        assert r.rc == 1 and r.result["error"]["kind"] == "duplicate_release"
    print("9 duplicate_release snapshot ok")

    # 10 启动命令发送失败：launch_failed 部分状态（终端已建、启动未发）
    w = _create_world("agent")
    w.fail("send", "error", nth=1)
    with tempfile.TemporaryDirectory() as td:
        r = case("create_launch_send_failure", w, td, rid="snap-launch-err-1",
                 target=NAME, extra=_fresh_extra(), screen_after_run=SCREEN)
        assert r.rc == 1 and r.result["error"]["kind"] == "launch_failed"
        assert r.result["launched"] is True and "launch_sent" not in r.result
    print("10 launch send failure snapshot ok")

    # 11 等 agent 就绪超时（前台一直 shell）：launch_failed，启动已发
    w = _create_world("stuck_shell")
    with tempfile.TemporaryDirectory() as td:
        r = case("create_agent_timeout", w, td, rid="snap-agent-timeout-1",
                 target=NAME, extra=_fresh_extra(), wall_clock="0.3",
                 screen_after_run=SCREEN)
        assert r.rc == 1 and r.result["error"]["kind"] == "launch_failed"
        assert r.result["launch_sent"] is True
    print("11 agent-ready timeout snapshot ok")

    # 12 rebuild_busy：working 占位者拒杀
    w = FakeHerdr()
    w.add_terminal(address="t60", name=NAME, fg="wrong", status="working",
                   turn=3, labels={})
    w.add_space(name="main")
    with tempfile.TemporaryDirectory() as td:
        r = case("rebuild_busy", w, td, rid="snap-rebuild-busy-1", target=NAME,
                 extra=_fresh_extra() + ["--rebuild"])
        assert r.rc == 1 and r.result["error"]["kind"] == "rebuild_busy"
    print("12 rebuild_busy snapshot ok")

    # 13 既有但非 idle：not_idle
    w = helpers.world()
    w.set_status("t99", "working")
    with tempfile.TemporaryDirectory() as td:
        r = case("existing_not_idle", w, td, rid="snap-not-idle-1")
        assert r.rc == 1 and r.result["error"]["kind"] == "not_idle"
    print("13 existing not_idle snapshot ok")

    # 14 --rebuild 误传既有分流：bad_args
    w = helpers.world()
    with tempfile.TemporaryDirectory() as td:
        r = case("rebuild_misuse", w, td, rid="snap-rebuild-misuse-1",
                 extra=_fresh_extra() + ["--rebuild"])
        assert r.rc == 2 and r.result["error"]["kind"] == "bad_args"
    print("14 rebuild misuse snapshot ok")

    print("ALL SNAPSHOT ROUTE TESTS PASSED")


if __name__ == "__main__":
    main()
