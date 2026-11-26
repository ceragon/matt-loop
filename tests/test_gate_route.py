#!/usr/bin/env python3
"""test_gate_route.py — 分流后闸纯函数 gate_route(route, session, request)（P1，ADR-0020 决定 4）。

跑法：python3 tests/test_gate_route.py
缝：dispatch.gate_route 纯函数直调（无 I/O、不碰 ctx/台账）。表驱动覆盖原次序：
占位四后缀文案 → launch_required → display_name_required → 非法会话名 →
rebuild_busy，并逐个钉相邻双命中（占位先于新鲜闸、launch 先于 display_name、
display_name 先于非法名、非法名/display_name 先于 rebuild_busy）。
"""
from types import SimpleNamespace

import harness
from harness import dispatch
from herdr import Session


def sess(**over):
    raw = {"address": "t1", "state": "live", "turn": 5,
           "labels": {"loop": "1", "loop-agent": "claude"},
           "foreground": {"name": "2.1.331", "argv": ["claude"]},
           "status": {"app": "claude-code", "state": "idle"}}
    raw.update(over)
    return Session.from_raw(raw)


SHELL_FG = {"name": "zsh", "argv": ["/bin/zsh", "-l"]}
WRONG_FG = {"name": "grok", "argv": ["grok"]}
WRONG_STATUS = {"app": "grok", "state": "idle"}


def req(**over):
    kw = {"target": "claude-test", "expect_proc": "claude", "rebuild": False,
          "launch": "zsh -l", "display_name": "Test Tab"}
    kw.update(over)
    return SimpleNamespace(**kw)


R = dispatch.Route
OCCUPIED_HINTS = [
    "呈报占位者给用户；确需重建须经用户显式授权后带 --rebuild（只杀 idle/done）",
    "或换 --target 到另一命名会话；不建不 kill、零发送、id 不消耗",
]
LAUNCH_HINTS = [
    "补 --launch <shell 启动命令> --cwd <目录> 后用同一放行 id 重试（零发送、id 不消耗）",
    "创建参数（会话名、启动命令、cwd、显示名）须随放行一并贴出，见 SKILL.md",
]
DISPLAY_HINTS = [
    "补 --display-name <显示名> 后用同一放行 id 重试（零发送、id 不消耗）",
    "显示名须随放行一并贴出，见 SKILL.md",
]
NAME_HINTS = [
    "--target 推荐传会话名（<kind>-<repo> 或 bot slug）；t 号是保留地址、不能作名字",
]
BUSY_HINTS = ["等占位者落定或请用户到屏幕前处理；kill 只在 idle/done 时执行"]


def err(kind, message, hints, returncode):
    return {"kind": kind, "message": message, "hints": hints,
            "returncode": returncode}


# (用例名, route, session, request, 期望 None 或 期望错误 dict)
CASES = [
    # —— 占位四后缀文案（首个闸，先于全部新鲜闸）——
    ("占位：无 loop 标签 → 拒收编",
     R.occupied,
     sess(address="t60", turn=3, labels={}, foreground=WRONG_FG,
          status=WRONG_STATUS),
     req(launch=None, display_name=None),
     err("session_occupied",
         "会话名 claude-test 被占用：t60 前台=grok turn=3 状态=idle（无 loop 标签，拒收编）",
         OCCUPIED_HINTS, 1)),
    ("占位：loop 标签与 --expect-proc 不符",
     R.occupied,
     sess(address="t61", turn=4, labels={"loop": "1", "loop-agent": "grok"}),
     req(),
     err("session_occupied",
         "会话名 claude-test 被占用：t61 前台=claude turn=4 状态=idle（loop 标签与 --expect-proc 不符）",
         OCCUPIED_HINTS, 1)),
    ("占位：前台 shell 但 status 非 null（异常态）",
     R.occupied,
     sess(address="t62", turn=0, foreground=SHELL_FG,
          status={"app": "x", "state": "idle"}),
     req(),
     err("session_occupied",
         "会话名 claude-test 被占用：t62 前台=/bin/zsh turn=0 状态=idle（前台 shell 但 status 非 null，异常态）",
         OCCUPIED_HINTS, 1)),
    ("占位：前台与 --expect-proc 不符",
     R.occupied,
     sess(address="t63", turn=5, foreground=WRONG_FG, status=WRONG_STATUS),
     req(),
     err("session_occupied",
         "会话名 claude-test 被占用：t63 前台=grok turn=5 状态=idle（前台与 --expect-proc 不符）",
         OCCUPIED_HINTS, 1)),
    ("占位双命中：缺 launch 与 display_name 仍先报 session_occupied（无地址时回显 target）",
     R.occupied,
     sess(address=None, turn=2, labels={}, foreground=WRONG_FG,
          status=WRONG_STATUS),
     req(launch=None, display_name=None),
     err("session_occupied",
         "会话名 claude-test 被占用：claude-test 前台=grok turn=2 状态=idle（无 loop 标签，拒收编）",
         OCCUPIED_HINTS, 1)),
    # —— launch_required 先于 display_name_required ——
    ("创建双命中：缺 launch＋缺 display_name → launch_required 先报",
     R.create, None, req(launch=None, display_name=None),
     err("launch_required", "目标会话不存在或已死，创建需要 --launch（＋--cwd）",
         LAUNCH_HINTS, 2)),
    ("创建：launch 已给、缺 display_name → display_name_required",
     R.create, None, req(display_name=None),
     err("display_name_required", "目标会话不存在或已死，创建需要 --display-name",
         DISPLAY_HINTS, 2)),
    ("复活：缺 launch → launch_required",
     R.revive, sess(foreground=SHELL_FG, status=None),
     req(launch=None),
     err("launch_required", "目标是空闲 shell，复活需要 --launch（＋--cwd）",
         LAUNCH_HINTS, 2)),
    ("复活：缺 display_name → display_name_required",
     R.revive, sess(foreground=SHELL_FG, status=None),
     req(display_name=None),
     err("display_name_required", "目标是空闲 shell，复活需要 --display-name",
         DISPLAY_HINTS, 2)),
    # —— 非法会话名（create/rebuild 才查） ——
    ("创建：非法会话名（含空格）→ bad_args",
     R.create, None, req(target="Bad Name"),
     err("bad_args", "--target Bad Name 不是合法会话名（1–32 位 a-z0-9-_），无法创建",
         NAME_HINTS, 2)),
    ("创建：t 号保留地址 → bad_args",
     R.create, None, req(target="t7"),
     err("bad_args", "--target t7 不是合法会话名（1–32 位 a-z0-9-_），无法创建",
         NAME_HINTS, 2)),
    ("重建：非法名先于 rebuild_busy（双命中）",
     R.rebuild, sess(address="t64", turn=3, labels={}, foreground=WRONG_FG,
                     status={"app": "grok", "state": "working"}),
     req(target="T7"),
     err("bad_args", "--target T7 不是合法会话名（1–32 位 a-z0-9-_），无法创建",
         NAME_HINTS, 2)),
    ("复活：不查会话名（空闲 shell 已有名字）", R.revive,
     sess(foreground=SHELL_FG, status=None), req(target="Anything Goes"), None),
    # —— rebuild_busy（最后一道） ——
    ("重建：占位者 working → rebuild_busy",
     R.rebuild, sess(address="t65", turn=3, labels={}, foreground=WRONG_FG,
                     status={"app": "grok", "state": "working"}),
     req(),
     err("rebuild_busy", "占位者状态是 working，不是 idle/done，拒杀", BUSY_HINTS, 1)),
    ("重建：占位者未自报 → rebuild_busy",
     R.rebuild, sess(address="t66", turn=3, labels={}, foreground=WRONG_FG,
                     status=None),
     req(),
     err("rebuild_busy", "占位者状态是 未自报，不是 idle/done，拒杀", BUSY_HINTS, 1)),
    ("重建双命中：缺 display_name 先于 rebuild_busy",
     R.rebuild, sess(address="t67", turn=3, labels={}, foreground=WRONG_FG,
                     status={"app": "grok", "state": "working"}),
     req(display_name=None),
     err("display_name_required", "强制重建需要 --display-name", DISPLAY_HINTS, 2)),
    ("重建：idle 占位者 → 放行", R.rebuild,
     sess(address="t68", turn=3, labels={}, foreground=WRONG_FG,
          status={"app": "grok", "state": "idle"}),
     req(), None),
    # —— 既有路径不查新鲜闸 ——
    ("既有：缺 launch/display_name 也不报错（复用轮）",
     R.existing, sess(), req(launch=None, display_name=None), None),
]


def _check_error(exc, want):
    assert isinstance(exc, dispatch.DispatchError), type(exc)
    assert exc.kind == want["kind"], exc.kind
    assert exc.message == want["message"], exc.message
    assert exc.hints == want["hints"], exc.hints
    assert exc.returncode == want["returncode"], exc.returncode


def main():
    for i, (label, route, session, request, want) in enumerate(CASES, 1):
        if want is None:
            assert dispatch.gate_route(route, session, request) is None, label
        else:
            try:
                dispatch.gate_route(route, session, request)
            except dispatch.DispatchError as e:
                _check_error(e, want)
            else:
                raise AssertionError(f"{label}: 未抛错")
        print(f"{i} {label} ok")
    print("ALL GATE_ROUTE TESTS PASSED")


if __name__ == "__main__":
    main()
