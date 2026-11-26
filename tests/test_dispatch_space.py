#!/usr/bin/env python3
"""test_dispatch_space.py — 空间定位测试（#43 grill 定稿，ADR-0011，FakeHerdr 假世界）。

跑法：python3 tests/test_dispatch_space.py
缝：唯一业务缝 harness.run（CLI 契约，T5b/ADR-0021）；场景用 FakeHerdr 世界方法与旋钮构造，
断言只认 stdout 一行 JSON、退出码、台账/result 文件、假世界 sends/hc_calls/app_calls/gifts 日志。
--space 必传，三态解析：/ 或 ~ 开头→路径（folder 匹配，唯一复用/零个 space.new
{name:目录名,folder,focus:false}/多个 space_ambiguous）；sp 前缀→id 直配；
其余→名（唯一匹配，重名 bad_args，不存在 space_not_found）。
覆盖：
  1 CLI 静态契约：--space 缺失 argparse 拒；空串 bad_args；路径非已存在目录 bad_args。
  2 名唯一：select→status 复核→keep→ls 复核（含所在空间）→还原；pane 落目标空间。
  3 id 直配（sp 前缀）；id 不存在 space_not_found。
  4 路径唯一复用（folder==路径）。
  5 路径零匹配：space.new{name:目录名,folder,focus:false}＋select→keep→还原
    ＋赠送 shell 清理（绑 space_created）。
  6 路径多匹配 space_ambiguous；零 select/keep、id 不消耗。
  7 名不存在 space_not_found。
  8 名重名 bad_args（退出码 2、零发送、id 不消耗）。
  9 目标==当前 shown：不 select、不还原（not_needed）。
  10 还原失败降级：restore_error/restore_noop → space_restore_warning 不丢放行。
  11 用户切走不还原（user_switched），领养照常成功。
  12 pane 已在：adopt 提前返回，不 select、不挪 pane，result 回显 space。
  13 零空间统一走 --space：路径→space.new（name=目录名）＋清理；名→space_not_found
    不自动建。
  14 keep 失败（keep_error/no_pane）也还原选中（finally 护栏）。
  15 select 已生效但复核不符：先走还原护栏再 select_error（焦点不滞留）。
  16 落地复核所在空间：pane 落进非目标空间 → space_target_warning 不丢放行。
  17 复活路径＋--space：补领养走空间定位后才发启动命令。
  18 创建路径＋--space：落目标空间、台账留痕；零空间＋路径→space.new name=目录名。
  19 命中空间无 id：space_not_found，不静默退化为不定位。

T5b（ADR-0021，#99）：完整轮用例迁 FakeHerdr＋harness.run（唯一 CLI 缝）。select/restore
失败经 fail("space.select", nth=…) 定位那一次（dispatch 单次不重试）；select 复核抖动经
delay("space.select", "land_after", nth=1) 只抖首次；用户接管经 user_switches_space(on=…)；
空间缺 id 经 hide_space_ids()；跑后选中/条目所在空间经命令层入口只读复核（T4 先例）。
R（#104）：共享常量迁 helpers 单一出处。
"""
import contextlib
import io
import json
import tempfile
from pathlib import Path

import harness
from harness import dispatch
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


def _existing(w, address="t50"):
    """既有 idle agent（loop 标签符）。"""
    w.add_terminal(address=address, name=NAME, turn=5,
                   labels={"loop": "1", "loop-agent": "claude"})


def _main_space(w, panes=()):
    """main 空间；panes 决定哪些终端已有 pane（空＝无标签页条目）。"""
    sp = w.add_space(name="main")
    if panes:
        w.add_tab(sp, terminals=list(panes))
    return sp


def _two_spaces(w, panes=(), *, sp1_folder="/home", sp2_folder="/proj", sp2_name="proj"):
    """两个空间：sp1（main，活动）＋sp2（proj，目标）；panes 决定谁已有 pane。"""
    sp1 = w.add_space(id="sp1", name="main", folder=sp1_folder)
    if panes:
        w.add_tab(sp1, id="tb1", terminals=sorted(panes))
    w.add_space(id="sp2", name=sp2_name, folder=sp2_folder)


def _kinds(w):
    return [(c[0], c[1] if c[0] == "do" else None) for c in w.app_calls]


def _rms(w):
    return [c[1] for c in w.hc_calls if c[0] == "rm"]


def _shown(w):
    """跑后经命令层入口只读复核当前选中（T4 先例）。"""
    return w.app_json(None, ["status"], 30)["space"]


def _entry_spaces(w, address):
    """跑后经命令层入口只读复核：终端条目所在的空间 id 列表。"""
    ls = w.app_json(None, ["ls"], 30)
    return [sp["id"] for sp in ls["spaces"]
            if any(t["terminal"] == address
                   for tb in sp["tabs"] for t in tb["terminals"])]


def main():
    # 1 CLI 静态契约：--space 缺失 argparse 拒；空串/路径非目录 bad_args
    base = ["--target", NAME, "--release-id", "r1", "--prompt-file", "p.txt"]
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            dispatch.parse_args(base)
        raise AssertionError("--space 应必传")
    except SystemExit as e:
        assert e.code == 2, e.code
    a = dispatch.parse_args(base + ["--space", "proj"])
    assert a.space == "proj"
    w = FakeHerdr()
    _existing(w)
    _main_space(w, panes=("t50",))
    rc, res, led, td = run_round(w, "space-empty-1", expect_proc="claude", space="")
    assert rc == 2 and res["error"]["kind"] == "bad_args", res
    assert res["space"] == "", "请求值回显（传了就带，error 也带）"
    assert w.sends == [] and led is None, "空串拒发、id 不消耗"
    for bad_path in ("/no/such/dir/xyz", str(Path(td) / "prompt.md")):
        w = FakeHerdr()
        _existing(w)
        _main_space(w, panes=("t50",))
        rc, res, led, td2 = run_round(w, "space-badpath-%d" % len(bad_path),
                                      expect_proc="claude", space=bad_path)
        assert rc == 2 and res["error"]["kind"] == "bad_args", (bad_path, res)
        assert w.sends == [] and led is None, bad_path
    print("1 CLI static: --space required, empty/non-dir path bad_args rc2 ok")

    # 2 名唯一：select→复核→keep→ls 复核→还原；pane 落进目标空间
    w = FakeHerdr()
    _existing(w)
    _two_spaces(w)
    rc, res, led, td = run_round(w, "space-name-1", expect_proc="claude", space="proj")
    assert rc == 0 and res["ok"], res
    assert _kinds(w) == [("status", None), ("ls", None),
                         ("do", "space.select"), ("status", None),
                         ("do", "terminal.keep"), ("ls", None),
                         ("status", None), ("do", "space.select"), ("status", None)], _kinds(w)
    assert json.loads(w.app_calls[2][2]) == {"id": "sp2"}, w.app_calls[2]
    assert json.loads(w.app_calls[4][2]) == {"terminal": "t50"}, w.app_calls[4]
    assert json.loads(w.app_calls[7][2]) == {"id": "sp1"}, "还原到原选中空间"
    assert _entry_spaces(w, "t50") == ["sp2"], "keep 应落进选中的目标空间"
    assert _shown(w) == "sp1", "选中还原"
    assert res["space"] == "proj" and res["space_selected"] == "sp2", res
    assert res["space_restore"] == "restored" and res["workspace"] == "adopted", res
    assert res["space_landed"] == "sp2" and "space_target_warning" not in res, res
    assert w.sends == [("t50", PROMPT, True)], w.sends
    print("2 unique name: select->keep->restore, pane in target space ok")

    # 3 id 直配（sp 前缀）
    w = FakeHerdr()
    _existing(w)
    _two_spaces(w)
    rc, res, led, td = run_round(w, "space-id-1", expect_proc="claude", space="sp2")
    assert rc == 0 and res["ok"], res
    assert json.loads(w.app_calls[2][2]) == {"id": "sp2"}
    assert res["space_selected"] == "sp2" and res["space_restore"] == "restored", res
    # id 不存在 → space_not_found
    w = FakeHerdr()
    _existing(w)
    _two_spaces(w)
    rc, res, led, td = run_round(w, "space-id-miss", expect_proc="claude", space="sp999")
    assert rc == 1 and res["error"]["kind"] == "adopt_failed", res
    assert res["adopt_reason"] == "space_not_found", res
    assert _kinds(w) == [("status", None), ("ls", None)] and w.sends == [] and led is None
    print("3 id direct match; missing id -> space_not_found ok")

    # 4 路径唯一复用（folder==路径）
    path = tempfile.mkdtemp()
    w = FakeHerdr()
    _existing(w)
    _two_spaces(w, sp2_folder=path)
    rc, res, led, td = run_round(w, "space-path-reuse", expect_proc="claude", space=path)
    assert rc == 0 and res["ok"], res
    assert not any(c[0] == "do" and c[1] == "space.new" for c in w.app_calls), "唯一匹配复用不建"
    assert json.loads(w.app_calls[2][2]) == {"id": "sp2"}
    assert res["space_selected"] == "sp2" and res["space_landed"] == "sp2", res
    print("4 path unique folder match: reuse, no space.new ok")

    # 5 路径零匹配：space.new{name:目录名,folder,focus:false}＋select→keep→还原＋赠送清理
    path = tempfile.mkdtemp()
    w = FakeHerdr()
    _existing(w)
    _two_spaces(w)
    rc, res, led, td = run_round(w, "space-path-create", expect_proc="claude", space=path)
    assert rc == 0 and res["ok"], res
    assert _kinds(w) == [("status", None), ("ls", None),
                         ("do", "space.new"), ("status", None),
                         ("do", "space.select"), ("status", None),
                         ("do", "terminal.keep"), ("ls", None),
                         ("status", None), ("do", "space.select"), ("status", None),
                         ("do", "tab.close")], _kinds(w)
    sp_new = json.loads(w.app_calls[2][2])
    assert sp_new == {"name": Path(path).name, "folder": path, "focus": False}, sp_new
    assert res["space_created"] is True and res["pane_adopted"] is True, res
    sp_new_id, gift_tab, gifted = w.gifts[0]
    assert json.loads(w.app_calls[11][2]) == {"id": gift_tab, "confirmed": True}
    assert _rms(w) == [gifted], _rms(w)
    assert _shown(w) == "sp1" and res["space_restore"] == "restored", res
    assert res["space_landed"] == sp_new_id, res
    print("5 path zero match: space.new(dirname)+select->keep->restore+gift cleanup ok")

    # 6 路径多匹配 space_ambiguous：零 select/keep、id 不消耗
    path = tempfile.mkdtemp()
    w = FakeHerdr()
    _existing(w)
    _two_spaces(w, sp1_folder=path, sp2_folder=path)
    rc, res, led, td = run_round(w, "space-path-ambig", expect_proc="claude", space=path)
    assert rc == 1 and res["error"]["kind"] == "adopt_failed", res
    assert res["adopt_reason"] == "space_ambiguous", res
    assert _kinds(w) == [("status", None), ("ls", None)] and w.sends == [] and led is None
    print("6 path multi folder match: space_ambiguous, zero writes, id unconsumed ok")

    # 7 名不存在 space_not_found
    w = FakeHerdr()
    _existing(w)
    _two_spaces(w)
    rc, res, led, td = run_round(w, "space-name-miss", expect_proc="claude",
                                 space="no-such-space")
    assert rc == 1 and res["error"]["kind"] == "adopt_failed", res
    assert res["adopt_reason"] == "space_not_found" and res["workspace"] == "failed", res
    assert res["space"] == "no-such-space", "请求值回显"
    assert _kinds(w) == [("status", None), ("ls", None)] and w.sends == [] and led is None
    print("7 name not found: space_not_found, zero writes, id unconsumed ok")

    # 8 名重名 bad_args（退出码 2、零发送、id 不消耗）
    w = FakeHerdr()
    _existing(w)
    _two_spaces(w, sp2_name="main")
    rc, res, led, td = run_round(w, "space-name-dup", expect_proc="claude", space="main")
    assert rc == 2 and res["error"]["kind"] == "bad_args", res
    assert _kinds(w) == [("status", None), ("ls", None)] and w.sends == [] and led is None
    print("8 duplicated name: bad_args rc2, id unconsumed ok")

    # 9 目标==当前 shown：不 select、不还原（not_needed）
    w = FakeHerdr()
    _existing(w)
    _two_spaces(w)
    w.user_switches_space()   # 用户已切到目标空间 sp2
    rc, res, led, td = run_round(w, "space-shown-1", expect_proc="claude", space="proj")
    assert rc == 0 and res["ok"], res
    assert _kinds(w) == [("status", None), ("ls", None),
                         ("do", "terminal.keep"), ("ls", None)], _kinds(w)
    assert res["space_restore"] == "not_needed" and res["space_selected"] == "sp2", res
    assert _shown(w) == "sp2"
    print("9 target==shown: no select, no restore (not_needed) ok")

    # 10 还原失败降级：restore_error/restore_noop → space_restore_warning 不丢放行
    for mode, reason in (("error", "restore_error"), ("noop", "restore_mismatch")):
        w = FakeHerdr()
        _existing(w)
        _two_spaces(w)
        w.fail("space.select", mode, nth=2)   # 第二次 select＝还原
        rc, res, led, td = run_round(w, "space-restore-%s" % mode, expect_proc="claude",
                                     space="proj")
        assert rc == 0 and res["ok"], (mode, res)
        assert res["space_restore"] == "failed", (mode, res)
        assert res["space_restore_warning"]["reason"] == reason, (mode, res)
        assert res["workspace"] == "adopted", "pane 落地仍是硬闸，还原失败不丢放行"
        assert w.sends == [("t50", PROMPT, True)], mode
    print("10 restore failure degrades to warning, release not lost ok")

    # 11 用户切走不还原（user_switched），领养照常成功
    w = FakeHerdr()
    _existing(w)
    _two_spaces(w)
    w.user_switches_space(on="terminal.keep")
    rc, res, led, td = run_round(w, "space-userswitch-1", expect_proc="claude",
                                 space="proj")
    assert rc == 0 and res["ok"], res
    assert res["space_restore"] == "user_switched", res
    assert "space_restore_warning" not in res, "用户接管不是告警"
    assert _shown(w) == "sp1", "不抢回用户的视图"
    assert not any(c[0] == "do" and c[1] == "space.select"
                   and json.loads(c[2]) == {"id": "sp1"} for c in w.app_calls), "不发还原 select"
    assert res["workspace"] == "adopted" and w.sends == [("t50", PROMPT, True)]
    print("11 user took over: skip restore, adopt succeeds ok")

    # 12 pane 已在：adopt 提前返回，不 select、不挪 pane，result 回显 space
    w = FakeHerdr()
    _existing(w)
    _two_spaces(w, panes=("t50",))
    rc, res, led, td = run_round(w, "space-noop-1", expect_proc="claude", space="proj")
    assert rc == 0 and res["ok"], res
    assert _kinds(w) == [("status", None), ("ls", None)], "pane 已在只读探测"
    assert res["space"] == "proj" and "space_selected" not in res and "space_restore" not in res
    assert _shown(w) == "sp1", "选中不动"
    assert w.sends == [("t50", PROMPT, True)]
    print("12 pane present: early return, no select, request echoed ok")

    # 13 零空间统一走 --space：路径→space.new(name=目录名)＋清理（shown 即新空间，不 select）；
    #    名→space_not_found 不自动建
    path = tempfile.mkdtemp()
    w = FakeHerdr()
    _existing(w)
    rc, res, led, td = run_round(w, "space-zero-path", expect_proc="claude", space=path)
    assert rc == 0 and res["ok"], res
    assert _kinds(w) == [("status", None), ("ls", None),
                         ("do", "space.new"), ("status", None),
                         ("do", "terminal.keep"), ("ls", None),
                         ("do", "tab.close")], _kinds(w)
    sp_new = json.loads(w.app_calls[2][2])
    assert sp_new == {"name": Path(path).name, "folder": path, "focus": False}, sp_new
    assert res["space_created"] is True and res["pane_adopted"] is True, res
    assert _rms(w) == [w.gifts[0][2]]
    assert res["space_restore"] == "not_needed", "零空间建后 shown 即目标，不 select 不还原"
    w = FakeHerdr()
    _existing(w)
    rc, res, led, td = run_round(w, "space-zero-name", expect_proc="claude", space="proj")
    assert rc == 1 and res["error"]["kind"] == "adopt_failed", res
    assert res["adopt_reason"] == "space_not_found", res
    assert not any(c[0] == "do" and c[1] == "space.new" for c in w.app_calls), "名不自动建空间"
    assert w.sends == [] and led is None
    print("13 zero-space unified: path creates(dirname)+cleanup; name -> space_not_found ok")

    # 14 keep 失败（keep_error/no_pane）也还原选中（finally 护栏）
    for mode, reason in (("error", "keep_error"), ("noop", "no_pane")):
        w = FakeHerdr()
        _existing(w)
        _two_spaces(w)
        w.fail("terminal.keep", mode)
        rc, res, led, td = run_round(w, "space-%s-restore" % reason,
                                     expect_proc="claude", space="proj")
        assert rc == 1 and res["error"]["kind"] == "adopt_failed", (mode, res)
        assert res["adopt_reason"] == reason, (mode, res)
        assert res["space_restore"] == "restored", (mode, res)
        assert _shown(w) == "sp1", "失败路径也还原选中"
        assert w.sends == [] and led is None, mode
    print("14 keep failure paths restore selection (finally guardrail) ok")

    # 15 select 已生效但复核不符（status 抖动/用户秒切）：先走还原护栏再 select_error
    w = FakeHerdr()
    _existing(w)
    _two_spaces(w)
    w.delay("space.select", "land_after", after=2, nth=1)   # 只首次 select 复核抖动
    rc, res, led, td = run_round(w, "space-flaky-1", expect_proc="claude", space="proj")
    assert rc == 1 and res["error"]["kind"] == "adopt_failed", res
    assert res["adopt_reason"] == "select_error", res
    assert not any(c[0] == "do" and c[1] == "terminal.keep" for c in w.app_calls), "keep 未发"
    assert res["space_restore"] == "restored", "select 已生效，护栏把焦点还原"
    assert _shown(w) == "sp1", "焦点不被留在目标空间"
    assert w.sends == [] and led is None
    # select 命令失败：select_error，选中未动
    w = FakeHerdr()
    _existing(w)
    _two_spaces(w)
    w.fail("space.select", "error")
    rc, res, led, td = run_round(w, "space-selerr-1", expect_proc="claude", space="proj")
    assert rc == 1 and res["adopt_reason"] == "select_error", res
    assert _shown(w) == "sp1" and w.sends == [] and led is None
    print("15 select verify mismatch: restore guardrail then select_error; command error ok")

    # 16 落地复核所在空间：用户切走＋pane 落进用户空间 → 告警不丢放行
    w = FakeHerdr()
    _existing(w)
    _two_spaces(w)
    w.user_switches_space(on="terminal.keep")
    w.pane_lands_elsewhere()
    rc, res, led, td = run_round(w, "space-drift-1", expect_proc="claude", space="proj")
    assert rc == 0 and res["ok"], res
    assert res["space_landed"] == "sp1", res
    assert res["space_target_warning"]["reason"] == "landed_elsewhere", res
    assert res["space_target_warning"]["actual"] == "sp1", res
    assert res["space_restore"] == "user_switched" and res["workspace"] == "adopted", res
    assert w.sends == [("t50", PROMPT, True)], "pane 已落地，恒可见满足，不丢放行"
    print("16 landing recheck: pane landed elsewhere -> warning, release kept ok")

    # 17 复活路径＋--space：pane 缺补领养走空间定位，之后才发启动命令
    w = FakeHerdr()
    w.add_terminal(address="t50", name=NAME, fg="shell", turn=7,
                   labels={"loop": "1", "loop-agent": "claude"})
    _two_spaces(w)
    w.will_launch(LAUNCH, "agent")
    rc, res, led, td = run_round(w, "space-revive-1", launch=LAUNCH, cwd="/proj",
                                 expect_proc="claude", space="proj")
    assert rc == 0 and res["ok"], res
    assert res["space_selected"] == "sp2" and res["space_restore"] == "restored", res
    assert "sp2" in _entry_spaces(w, "t50"), "复活补领养也落进目标空间"
    assert w.sends == [("t50", LAUNCH, False), ("t50", PROMPT, True)], w.sends
    print("17 revive path --space: re-adopt targets space before launch ok")

    # 18 创建路径＋--space：落目标空间、台账留痕；零空间＋路径→space.new name=目录名
    w = FakeHerdr()
    _two_spaces(w)
    w.will_launch(LAUNCH, "agent")
    rc, res, led, td = run_round(w, "space-create-1", launch=LAUNCH, cwd="/proj",
                                 expect_proc="claude", space="proj")
    assert rc == 0 and res["ok"], res
    addr = res["target_address"]
    assert "sp2" in _entry_spaces(w, addr), "创建路径 keep 也落进目标空间"
    assert _shown(w) == "sp1" and res["space_restore"] == "restored", res
    assert res["space"] == "proj" and res["space_selected"] == "sp2"
    assert led["space"] == "proj" and led["space_selected"] == "sp2", "台账留痕"
    assert led["space_restore"] == "restored"
    path = tempfile.mkdtemp()
    w = FakeHerdr()
    w.will_launch(LAUNCH, "agent")
    rc, res, led, td = run_round(w, "space-create-zero", launch=LAUNCH, cwd="/proj",
                                 expect_proc="claude", space=path)
    assert rc == 0 and res["ok"], res
    sp_new = next(json.loads(c[2]) for c in w.app_calls if c[0] == "do" and c[1] == "space.new")
    assert sp_new == {"name": Path(path).name, "folder": path, "focus": False}, sp_new
    assert res["space_created"] is True and led["space_created"] is True
    assert _rms(w) == [w.gifts[0][2]], "赠送 shell 清理绑 space_created"
    # 创建路径空间解析失败：保留已建终端、id 消耗
    w = FakeHerdr()
    _two_spaces(w)
    w.will_launch(LAUNCH, "agent")
    rc, res, led, td = run_round(w, "space-create-miss", launch=LAUNCH, cwd="/proj",
                                 expect_proc="claude", space="no-such")
    assert rc == 1 and res["error"]["kind"] == "adopt_failed", res
    assert res["adopt_reason"] == "space_not_found", res
    assert res["launched"] is True and res["launch_sent"] is True, res
    assert w.hc_json(None, ["get", res["target_address"]], 30)["state"] == "live", "保留已建终端"
    assert led["phase"] == "error:adopt_failed", "id 消耗"
    print("18 create path --space: lands in target, ledger; zero-space path creates(dirname) ok")

    # 19 命中空间无 id：不静默退化为不定位，报 space_not_found
    w = FakeHerdr()
    _existing(w)
    w.add_space(name="proj")
    w.hide_space_ids()
    rc, res, led, td = run_round(w, "space-noid-1", expect_proc="claude", space="proj")
    assert rc == 1 and res["error"]["kind"] == "adopt_failed", res
    assert res["adopt_reason"] == "space_not_found", res
    assert not any(c[0] == "do" and c[1] == "terminal.keep" for c in w.app_calls)
    print("19 matched space without id: space_not_found, no silent fallback ok")

    print("ALL SPACE TESTS PASSED")


if __name__ == "__main__":
    main()
