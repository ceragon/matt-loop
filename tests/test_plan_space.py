#!/usr/bin/env python3
"""test_plan_space.py — 空间匹配策略纯函数 plan_space(space, layout)（P1，ADR-0020 决定 7）。

跑法：python3 tests/test_plan_space.py
缝：dispatch.plan_space 纯函数直调（无 I/O、不建空间、不 select、不抛错）。
薄包装在 parse_space 之上，只把三态结果映射成 reuse(id)/create(name, folder)/
error(reason, message, hint)；匹配规则仍是 parse_space 唯一实现，本测试只钉
映射面与错误三类（space_ambiguous／space_not_found／bad_args）。
"""
import json

import harness
from harness import dispatch
from herdr import Layout


def layout(spaces):
    return Layout(raw={"spaces": spaces})


def _spaces():
    return [{"id": "sp1", "name": "main", "folder": "/tmp/example-a"},
            {"id": "sp2", "name": "proj", "folder": "/tmp/example-b"}]


def reuse(space_id):
    return dispatch.SpacePlan(kind="reuse", space_id=space_id)


def create(name, folder):
    return dispatch.SpacePlan(kind="create", name=name, folder=folder)


def error(reason, message, hint):
    return dispatch.SpacePlan(kind="error", reason=reason,
                              message=message, hint=hint)


# (用例名, space, spaces, 期望 SpacePlan)
CASES = [
    ("名唯一 → reuse", "proj", _spaces(), reuse("sp2")),
    ("sp 前缀 id 直配 → reuse", "sp2", _spaces(), reuse("sp2")),
    ("路径唯一（尾斜杠归一）→ reuse", "/tmp/example-b/", _spaces(), reuse("sp2")),
    ("路径零匹配 → create（name=目录名、folder=归一路径）",
     "/tmp/example-none", _spaces(), create("example-none", "/tmp/example-none")),
    ("ls 空列表 + 路径 → create", "/tmp/example-none", [], create("example-none",
                                                                  "/tmp/example-none")),
    ("路径多匹配 → space_ambiguous",
     "/tmp/example-dup",
     [{"id": "sp1", "name": "a", "folder": "/tmp/example-dup"},
      {"id": "sp2", "name": "b", "folder": "/tmp/example-dup"}],
     error("space_ambiguous",
           "--space 路径 /tmp/example-dup 按 folder 命中 2 个空间，无法定夺",
           "改传空间 id（sp…）或唯一名消除歧义")),
    ("id 不存在 → space_not_found", "sp999", _spaces(),
     error("space_not_found", "--space id sp999 在 ls 里找不到", None)),
    ("名不存在 → space_not_found（名不自动建空间）", "no-such", _spaces(),
     error("space_not_found", "--space 名 no-such 在 ls 里找不到",
           "核对目标空间名/id/路径；名不自动建空间，传路径零匹配才建")),
    ("名重名 → bad_args",
     "dup",
     [{"id": "sp1", "name": "dup", "folder": "/tmp/example-a"},
      {"id": "sp2", "name": "dup", "folder": "/tmp/example-b"}],
     error("bad_args", "--space dup 重名命中 2 个空间，无法定夺", None)),
    ("命中空间缺 id → space_not_found",
     "proj", [{"id": None, "name": "proj", "folder": "/tmp/example-b"}],
     error("space_not_found", "--space proj 命中的空间缺 id，无法定位", None)),
    ("layout 缺 spaces 键 → 按零匹配处理",
     "/tmp/example-none", None, create("example-none", "/tmp/example-none")),
]


def main():
    for i, (label, space, spaces, want) in enumerate(CASES, 1):
        lay = Layout(raw={}) if spaces is None else layout(spaces)
        got = dispatch.plan_space(space, lay)
        assert got == want, (label, got, want)
        print(f"{i} {label} ok")

    # 纯函数：不建空间、不改 layout、重复调用同结果
    spaces = _spaces()
    lay = layout(spaces)
    snapshot = json.dumps(spaces, sort_keys=True)
    assert dispatch.plan_space("/tmp/example-none", lay) == \
        dispatch.plan_space("/tmp/example-none", lay)
    assert json.dumps(spaces, sort_keys=True) == snapshot
    print("purity (no mutation, deterministic) ok")
    print("ALL PLAN_SPACE TESTS PASSED")


if __name__ == "__main__":
    main()
