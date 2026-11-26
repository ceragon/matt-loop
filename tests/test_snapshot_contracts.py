#!/usr/bin/env python3
"""test_snapshot_contracts.py — S 片特征化快照：次序契约与两态学习（ADR-0021 决定 2/5）。

跑法：python3 tests/test_snapshot_contracts.py            # 与金样逐字比对
      python3 tests/test_snapshot_contracts.py --update   # 首次生成/显式更新基线

缝与归一化同 test_snapshot_routes.py（harness.run ＋ FakeHerdr ＋ SnapshotWorld 逐命令
读台账）。本文件专钉 ADR-0021 决定 2 点名的次序契约：
  1 五处含义各异的 NotFound 之二：等 shell 时名字未解析→继续等（最终成功）
  2 五处之三：等 agent 时名字失解析→launch_failed（终端启动期死亡语义）
  3 五处之四：赠送 shell 轮询 not_found→视同已清完（gone，不发 rm）
  4 五处之五：hc rm 时 not_found→视同清理完成（continue）
    （五处之一＝分流 not_found→创建，已由 routes 的 create 成功路径照住）
  5 find_app_bin 先求值：herdr-app 缺失报 missing_tool，不被吞成 adopt_failed
  6 原生 kind 穿透之一：分流非 not_found 直接 raise（外部 kind 原样外泄）
  7 原生 kind 穿透之二：wait_for_result 的未包装 get（check-in 兜底）
  8 split_required 先于 reset_required（同一 prompt 双命中先报 split）
  9 reset_required 单命中（对照：无 split 形态时纪律闸照常拦）
  10/11 LOOP_LEARNING off/on 两态（存储目录经 harness 作用域 env 指向临时目录）
"""
import tempfile
from pathlib import Path

import helpers
import snapshot
from fake_herdr import FakeHerdr
from helpers import EXT, LAUNCH, NAME, DISPLAY, SCREEN

UPDATE = snapshot.update_requested()


def case(name, world, td, *, rid, target="t99", extra=(), prompt=helpers.PROMPT,
         space="main", env=None, locate=None, screen_after_run=None,
         wait_reason=None):
    """装配 argv、经 SnapshotWorld 跑一轮、比对快照；返回 harness 结果。"""
    a = snapshot.argv(td, rid, target=target, prompt=prompt, space=space, extra=extra)
    kw = {"extract": helpers.ext(EXT)}
    if env is not None:
        kw["env"] = env
    if locate is not None:
        kw["locate"] = locate
    return snapshot.run_case(
        name, world, td, a, update=UPDATE, screen_after_run=screen_after_run,
        wait_reason=wait_reason, **kw)


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
    # 1 等 shell 时 not_found：第一次 get 未解析→继续等，第二次就绪→创建成功
    w = _create_world("agent")
    w.fail("get", "error", kind="not_found", nth=2)
    with tempfile.TemporaryDirectory() as td:
        r = case("notfound_shell_wait_continues", w, td, rid="snap-nf-shell-1",
                 target=NAME, extra=_fresh_extra(), screen_after_run=SCREEN)
        assert r.rc == 0, r.result
    print("1 NotFound in shell wait continues snapshot ok")

    # 2 等 agent 时 not_found：名字失解析→launch_failed
    w = _create_world("agent")
    w.fail("get", "error", kind="not_found", nth=3)
    with tempfile.TemporaryDirectory() as td:
        r = case("notfound_agent_ready", w, td, rid="snap-nf-agent-1",
                 target=NAME, extra=_fresh_extra(), screen_after_run=SCREEN)
        assert r.rc == 1 and r.result["error"]["kind"] == "launch_failed"
    print("2 NotFound in agent wait -> launch_failed snapshot ok")

    # 3 赠送 shell 轮询 not_found：条目已被收走→gone，不发 rm
    w = _create_world("agent")
    w.delay("tab.close", "vanish")
    with tempfile.TemporaryDirectory() as td:
        space_dir = Path(td) / "newspace"
        space_dir.mkdir()
        r = case("gift_gone_no_rm", w, td, rid="snap-gift-gone-1", target=NAME,
                 extra=_fresh_extra(), space=str(space_dir),
                 screen_after_run=SCREEN)
        assert r.rc == 0, r.result
        assert not any(c["command"] == "hc rm" for c in r.trace)
    print("3 gift shell not_found -> gone, no rm snapshot ok")

    # 4 hc rm 时 not_found：视同清理完成（continue），主轮仍成功
    w = _create_world("agent")
    w.fail("rm", "error", kind="not_found")
    with tempfile.TemporaryDirectory() as td:
        space_dir = Path(td) / "newspace"
        space_dir.mkdir()
        r = case("gift_rm_notfound_continues", w, td, rid="snap-gift-rm-1",
                 target=NAME, extra=_fresh_extra(), space=str(space_dir),
                 screen_after_run=SCREEN)
        assert r.rc == 0, r.result
        assert any(c["command"] == "hc rm" for c in r.trace)
    print("4 gift rm not_found -> cleanup complete snapshot ok")

    # 5 find_app_bin 先求值：herdr-app 缺失→missing_tool（不是 adopt_failed）
    w = helpers.world()

    def locate_missing_app(name, probe_dirs=None):
        return None if name == "herdr-app" else "/fake/" + name

    with tempfile.TemporaryDirectory() as td:
        r = case("find_app_bin_missing_tool", w, td, rid="snap-missing-app-1",
                 locate=locate_missing_app)
        assert r.rc == 1 and r.result["error"]["kind"] == "missing_tool"
    print("5 find_app_bin first-eval preserves missing_tool snapshot ok")

    # 6 原生 kind 穿透：分流非 not_found 直接 raise（外部 kind 原样）
    w = helpers.world()
    w.fail("get", "error", kind="unavailable", nth=1)
    with tempfile.TemporaryDirectory() as td:
        r = case("native_kind_branching", w, td, rid="snap-native-branch-1")
        assert r.rc == 1 and r.result["error"]["kind"] == "unavailable"
    print("6 native kind pass-through at branch snapshot ok")

    # 7 原生 kind 穿透：wait_for_result check-in 兜底 get 未包装
    w = helpers.world()
    w.fail("get", "error", kind="unavailable", nth=2)
    with tempfile.TemporaryDirectory() as td:
        r = case("native_kind_wait_get", w, td, rid="snap-native-wait-1",
                 wait_reason="timeout")
        assert r.rc == 1 and r.result["error"]["kind"] == "unavailable"
    print("7 native kind pass-through at wait fallback get snapshot ok")

    # 8 split_required 先于 reset_required：同一 prompt 双命中先报 split
    w = helpers.world()
    with tempfile.TemporaryDirectory() as td:
        r = case("static_split_before_reset", w, td, rid="snap-split-1",
                 prompt="/wayfinder #7\n第二行说明")
        assert r.rc == 2 and r.result["error"]["kind"] == "split_required"
    print("8 split_required before reset_required snapshot ok")

    # 9 reset_required 单命中（无 split 形态）：纪律闸照常拦
    w = helpers.world()
    with tempfile.TemporaryDirectory() as td:
        r = case("static_reset_required", w, td, rid="snap-reset-req-1",
                 prompt="/wayfinder #7")
        assert r.rc == 2 and r.result["error"]["kind"] == "reset_required"
    print("9 reset_required snapshot ok")

    # 10/11 LOOP_LEARNING 两态：同一世界与 argv，仅开关不同
    w_off = helpers.world()
    with tempfile.TemporaryDirectory() as td:
        r = case("learning_off", w_off, td, rid="snap-learning-1")
        assert r.rc == 0, r.result
    w_on = helpers.world()
    with tempfile.TemporaryDirectory() as td:
        r = case("learning_on", w_on, td, rid="snap-learning-1",
                 env={"LOOP_LEARNING": "on",
                      "LOOP_LEARNING_DIR": str(Path(td) / "learn")})
        assert r.rc == 0, r.result
    print("10/11 LOOP_LEARNING off/on snapshots ok")

    print("ALL SNAPSHOT CONTRACT TESTS PASSED")


if __name__ == "__main__":
    main()
