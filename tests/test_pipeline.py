#!/usr/bin/env python3
"""test_pipeline.py — 管线入口 run(request, deps) 的直接测试（P2，ADR-0020 决定 1/2）。

跑法：python3 tests/test_pipeline.py
缝：pipeline.run(request, deps) 本身——不碰 stdout、webhook，也不读 result/notify 文件；
只看 Outcome（returncode/payload/result_path/notify_path）与台账落盘。既有 main 级
覆盖与 S 快照仍是回归主护栏，本文件只钉管线入口的对外契约：
  1 成功一轮 → Outcome.returncode=0、payload.ok=True、两个交付路径指向本轮产物；
  2 受理前早期 error（静态闸 bad_args）→ 两个交付路径均为 None（不建通知记录）；
  3 run 永不外抛 DispatchError，统一转 error Outcome 并写台账 error 终态；
  4 非预期异常（RuntimeError）照旧冒泡，不被宽容成 error Outcome。
"""
import dataclasses
import tempfile
from pathlib import Path

import harness  # noqa: F401  （把 loop/scripts 放进 sys.path）
import pipeline
from fake_herdr import FakeHerdr
from helpers import PROMPT, SCREEN, EXT, world


def make_deps(fake_world, *, extract=None, env=None):
    env_map = {"LOOP_EXTRACT_BACKEND": "pi", "LOOP_LEARNING": "off"}
    env_map.update(env or {})
    clock = harness.VirtualClock()
    if extract is None:
        ext = dict(EXT)
        fields = {k: v for k, v in ext.items() if k != "reply"}
        extract = lambda deps, request, turn_path, injection: pipeline.Extraction(
            fields=fields, reply=ext["reply"], next_skill_invalid=False)
    return harness.dispatch.Deps(
        hc_json=fake_world.hc_json, app_json=fake_world.app_json,
        monotonic=clock.monotonic, sleep=clock.sleep,
        env=env_map, locate=lambda name, probe_dirs=None: "/fake/" + name,
        proxy_resolution=lambda url: ({}, "none"), extract=extract)


def make_request(td, rid, *, extra_launch=False):
    pf = Path(td) / "prompt.md"
    pf.write_text(PROMPT, encoding="utf-8")
    return pipeline.Request(
        target="t99", release_id=rid, prompt_file=str(pf), reset="none",
        target_model=None,
        launch="claude --dangerously-skip-permissions" if extra_launch else None,
        cwd="/proj" if extra_launch else None, display_name=None,
        space="main", rebuild=False, expect_proc=None,
        loop_dir=str(Path(td) / ".loop"), pi_model=None,
        timing=pipeline.Timing(
            wall_clock=5.0, check_in=1.0, settle_seconds=0.01, settle_checks=2,
            adopt_timeout=0.05, adopt_poll=0.001, extract_timeout=1200.0))


def main():
    # 1 成功一轮：run 返回 Outcome，交付路径已定但文件不由 run 写
    w = world()
    with tempfile.TemporaryDirectory() as td:
        clock = harness.VirtualClock()
        deps = harness.dispatch.Deps(
            hc_json=w.hc_json, app_json=w.app_json,
            monotonic=clock.monotonic, sleep=clock.sleep,
            env={"LOOP_EXTRACT_BACKEND": "pi", "LOOP_LEARNING": "off"},
            locate=lambda name, probe_dirs=None: "/fake/" + name,
            proxy_resolution=lambda url: ({}, "none"),
            extract=lambda deps, request, turn_path, injection: pipeline.Extraction(
                fields={"needs_user": False, "questions": [], "conclusion": "",
                        "blocked": "", "next_skill": ""},
                reply=SCREEN, next_skill_invalid=False))
        out = pipeline.run(make_request(td, "pipe-ok-1"), deps)
        assert out.returncode == 0, out.payload
        assert out.payload["ok"] is True and out.payload["phase"] == "done", out.payload
        assert "reply" not in out.payload, out.payload      # reply 只落 .md，不进载荷
        assert out.payload["next_skill"] == "", out.payload
        assert out.result_path == Path(td) / ".loop/runs/pipe-ok-1.result.json"
        assert out.notify_path == Path(td) / ".loop/runs/pipe-ok-1.notify.json"
        ledger = Path(td) / ".loop/runs/pipe-ok-1.json"
        assert ledger.is_file(), "run 仍写台账"
        assert not out.result_path.exists() and not out.notify_path.exists(), \
            "交付层归 main：run 不写 result/notify"
    print("1 run success: Outcome payload + paths, delivery left to main OK")

    # 2 受理前早期 error：两个交付路径均 None
    with tempfile.TemporaryDirectory() as td:
        req = make_request(td, "pipe-early-1", extra_launch=True)
        req = dataclasses.replace(req, cwd=None)   # --launch 缺 --cwd
        out = pipeline.run(req, make_deps(world()))
        assert out.returncode == 2, out.payload
        assert out.payload["error"]["kind"] == "bad_args", out.payload
        assert out.result_path is None and out.notify_path is None, out
    print("2 early error: both delivery paths None OK")

    # 3 DispatchError 不外抛：转 error Outcome 并写台账 error 终态
    w = FakeHerdr()
    w.add_terminal(address="t99", name="claude-test", labels={"loop": "1"})
    sp = w.add_space(name="main")
    w.add_tab(sp, terminals=["t99"])
    w.screen("t99", SCREEN)
    w.fail("get", kind="bogus")     # 分流 get 异常（非 not_found）→ 原生 kind 穿透
    with tempfile.TemporaryDirectory() as td:
        out = pipeline.run(make_request(td, "pipe-err-1"), make_deps(w))
        assert out.returncode != 0 and out.payload["ok"] is False, out.payload
        ledger = Path(td) / ".loop/runs/pipe-err-1.json"
        # 分流在台账 open 之前失败 → 预检失败零写入（id 不消耗）
        assert not ledger.exists(), "预检失败零写入"
    print("3 DispatchError -> error Outcome, no exception OK")

    # 4 非预期异常照旧冒泡
    def boom(deps, request, turn_path, injection):
        raise RuntimeError("probe boom")

    w = world()
    with tempfile.TemporaryDirectory() as td:
        try:
            pipeline.run(make_request(td, "pipe-boom-1"), make_deps(w, extract=boom))
            raise AssertionError("非预期异常须原样冒泡")
        except RuntimeError as e:
            assert str(e) == "probe boom", e
    print("4 unexpected exception bubbles OK")

    print("all pipeline tests passed")


if __name__ == "__main__":
    main()
