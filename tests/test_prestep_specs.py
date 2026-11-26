#!/usr/bin/env python3
"""test_prestep_specs.py — 前置次序纯函数 prestep_specs(request, route)（P1，ADR-0020 决定 6）。

跑法：python3 tests/test_prestep_specs.py
缝：dispatch.prestep_specs 纯函数直调（无 I/O、不碰台账）。表驱动覆盖
「重置 → 切模型」次序、重置独立/切模型独立/两者同传、新鲜会话不产出重置
spec 并记 reset_skipped: fresh_launch（切模型照跑）。
"""
from types import SimpleNamespace

import harness
from harness import dispatch


R = dispatch.Route


def req(**over):
    kw = {"reset": "none", "target_model": None}
    kw.update(over)
    return SimpleNamespace(**kw)


def prestep(kind, command):
    return dispatch.Prestep(kind=kind, command=command)


# (用例名, request, route, 期望 specs 元组, 期望 reset_skipped)
CASES = [
    ("既有路径 + 重置 → 只发重置",
     req(reset="new"), R.existing, (prestep("reset", "/new"),), None),
    ("既有路径 + 切模型 → 只发切模型",
     req(target_model="opus"), R.existing, (prestep("model", "/model opus"),), None),
    ("既有路径 + 重置 + 切模型 → 重置先于切模型",
     req(reset="clear", target_model="opus"), R.existing,
     (prestep("reset", "/clear"), prestep("model", "/model opus")), None),
    ("既有路径 + --reset=none → 空",
     req(), R.existing, (), None),
    ("创建（新鲜）+ 重置 → 不发重置，记 fresh_launch",
     req(reset="new"), R.create, (), "fresh_launch"),
    ("复活（新鲜）+ 重置 → 不发重置，记 fresh_launch",
     req(reset="compact"), R.revive, (), "fresh_launch"),
    ("强制重建（新鲜）+ 重置 → 不发重置，记 fresh_launch",
     req(reset="clear"), R.rebuild, (), "fresh_launch"),
    ("创建（新鲜）+ 重置 + 切模型 → 只发切模型，仍记 fresh_launch",
     req(reset="new", target_model="opus"), R.create,
     (prestep("model", "/model opus"),), "fresh_launch"),
    ("创建（新鲜）+ 仅切模型 → 只发切模型，不记 skip（没有重置可跳）",
     req(target_model="opus"), R.create,
     (prestep("model", "/model opus"),), None),
    ("既有路径 + 切模型名字原样透传（不映射、不校验）",
     req(target_model="gpt-5.1-codex"), R.existing,
     (prestep("model", "/model gpt-5.1-codex"),), None),
]


def main():
    for i, (label, request, route, specs, skipped) in enumerate(CASES, 1):
        plan = dispatch.prestep_specs(request, route)
        assert isinstance(plan.specs, tuple), (label, type(plan.specs))
        assert plan.specs == specs, (label, plan.specs)
        assert plan.reset_skipped == skipped, (label, plan.reset_skipped)
        print(f"{i} {label} ok")
    print("ALL PRESTEP_SPECS TESTS PASSED")


if __name__ == "__main__":
    main()
