#!/usr/bin/env python3
"""test_parse_space.py — --space 三态解析归一纯函数单元测试（#70，ADR-0011）。

跑法：python3 tests/test_parse_space.py
缝（预定，#70 验收）：
  1 dispatch.parse_space 纯函数直调（无 I/O、无全局替换，棘轮天然干净）：
    三态匹配判定与消息——路径（唯一/零/多）、名（唯一/重名/不存在）、id（命中/
    不存在）、命中缺 id、路径归一化；纯函数不查路径存在性（那是 main 预检的
    bad_args），路径样例用 /tmp/example 占位。
  2 harness.run CLI 缝：两个调用方「零匹配」策略差异——领养执行 space.new、
    学习不记录不建（resolve_space_id_for_learning 只读投影）。CLI 受理要求
    --space 路径是已存在目录，路径态用真实临时目录。
"""
import json
import tempfile
from pathlib import Path

import harness
from harness import dispatch
from fake_herdr import FakeHerdr
from helpers import PROMPT, SCREEN, NAME


def _spaces():
    """两个空间（占位 folder）：sp1 main / sp2 proj。"""
    return [{"id": "sp1", "name": "main", "folder": "/tmp/example-a"},
            {"id": "sp2", "name": "proj", "folder": "/tmp/example-b"}]


def test_path_unique():
    # 路径唯一匹配 → 复用既有空间 id，不提案新建、无错误
    assert dispatch.parse_space("/tmp/example-b", _spaces()) == {
        "target_id": "sp2", "new_space": None, "error": None}


def test_path_zero():
    # 路径零匹配 → 只给出新建提案（name=目录名、focus:False），建不建归调用方
    assert dispatch.parse_space("/tmp/example-none", _spaces()) == {
        "target_id": None,
        "new_space": {"name": "example-none", "folder": "/tmp/example-none",
                      "focus": False},
        "error": None}
    # 零空间（ls 空列表）同样走提案，不是特殊分支
    assert dispatch.parse_space("/tmp/example-none", []) == {
        "target_id": None,
        "new_space": {"name": "example-none", "folder": "/tmp/example-none",
                      "focus": False},
        "error": None}


def test_path_ambiguous():
    # 路径多匹配 → space_ambiguous（数据态歧义），消息带归一路径与命中数
    spaces = [{"id": "sp1", "name": "a", "folder": "/tmp/example-dup"},
              {"id": "sp2", "name": "b", "folder": "/tmp/example-dup"}]
    assert dispatch.parse_space("/tmp/example-dup", spaces) == {
        "target_id": None, "new_space": None,
        "error": {"reason": "space_ambiguous",
                  "message": "--space 路径 /tmp/example-dup 按 folder 命中 2 个空间，无法定夺",
                  "hint": "改传空间 id（sp…）或唯一名消除歧义"}}


def test_name_states():
    # 名唯一 → 复用 id
    assert dispatch.parse_space("proj", _spaces()) == {
        "target_id": "sp2", "new_space": None, "error": None}
    # 名不存在 → space_not_found，不提案新建（名不自动建空间）
    assert dispatch.parse_space("no-such", _spaces()) == {
        "target_id": None, "new_space": None,
        "error": {"reason": "space_not_found",
                  "message": "--space 名 no-such 在 ls 里找不到",
                  "hint": "核对目标空间名/id/路径；名不自动建空间，传路径零匹配才建"}}
    # 名重名 → bad_args（参数错误，区别于路径多匹配的数据态 space_ambiguous）
    spaces = [{"id": "sp1", "name": "dup", "folder": "/tmp/example-a"},
              {"id": "sp2", "name": "dup", "folder": "/tmp/example-b"}]
    assert dispatch.parse_space("dup", spaces) == {
        "target_id": None, "new_space": None,
        "error": {"reason": "bad_args",
                  "message": "--space dup 重名命中 2 个空间，无法定夺",
                  "hint": None}}


def test_id_states():
    # sp 前缀 → id 直配
    assert dispatch.parse_space("sp2", _spaces()) == {
        "target_id": "sp2", "new_space": None, "error": None}
    # id 不存在 → space_not_found（id 不自动建空间）
    assert dispatch.parse_space("sp999", _spaces()) == {
        "target_id": None, "new_space": None,
        "error": {"reason": "space_not_found",
                  "message": "--space id sp999 在 ls 里找不到", "hint": None}}
    # 三态优先级：sp 前缀恒按 id 解析，即使恰有空间叫这个名（两份旧实现同此判定）
    spaces = [{"id": "sp1", "name": "spooky", "folder": "/tmp/example-a"}]
    assert dispatch.parse_space("spooky", spaces)["error"]["reason"] == "space_not_found"
    assert dispatch.parse_space("spooky", spaces)["error"]["message"] == \
        "--space id spooky 在 ls 里找不到"


def test_missing_id():
    # 命中空间缺 id → space_not_found，不静默退化为不定位（路径态与名态各一）
    spaces = [{"id": None, "name": "proj", "folder": "/tmp/example-b"}]
    assert dispatch.parse_space("/tmp/example-b", spaces) == {
        "target_id": None, "new_space": None,
        "error": {"reason": "space_not_found",
                  "message": "--space 路径 /tmp/example-b 命中的空间缺 id，无法定位",
                  "hint": None}}
    assert dispatch.parse_space("proj", spaces) == {
        "target_id": None, "new_space": None,
        "error": {"reason": "space_not_found",
                  "message": "--space proj 命中的空间缺 id，无法定位", "hint": None}}


def test_normalization_and_purity():
    # 路径归一：尾斜杠、. 段、.. 段都按 normpath 归一后匹配
    for p in ("/tmp/example-b/", "/tmp/example-b/.", "/tmp/example-x/../example-b"):
        assert dispatch.parse_space(p, _spaces())["target_id"] == "sp2", p
    # ~ 展开到 home 后匹配（期望值独立取自 Path.home()，不是重算被测表达式）
    home_sp = [{"id": "sph", "name": "home", "folder": str(Path.home() / "example-home")}]
    assert dispatch.parse_space("~/example-home", home_sp)["target_id"] == "sph"
    # 纯函数：不改动入参列表，重复调用结果相同
    spaces = _spaces()
    snapshot = json.dumps(spaces, sort_keys=True)
    r1 = dispatch.parse_space("/tmp/example-none", spaces)
    r2 = dispatch.parse_space("/tmp/example-none", spaces)
    assert r1 == r2 and json.dumps(spaces, sort_keys=True) == snapshot


def _cli_argv(td, rid, target, space, extra=()):
    """写提示词文件并装配基础 argv（虚拟墙钟 5、快速落定/领养轮询）。"""
    pf = Path(td) / "prompt.md"
    pf.write_text(PROMPT, encoding="utf-8")
    return ["--target", target, "--release-id", rid, "--prompt-file", str(pf),
            "--loop-dir", str(Path(td) / ".loop"), "--space", space,
            "--wall-clock", "5", "--check-in", "1",
            "--settle-seconds", "0.01", "--settle-checks", "2",
            "--adopt-timeout", "0.05", "--adopt-poll", "0.001", *extra]


def test_caller_zero_match_policies():
    # 同一个零匹配路径，两个调用方策略相反（#70 验收）：
    # 领养 → 执行 space.new{name:目录名,folder:路径,focus:false}；
    # 学习 → 不记录、不建（space_id_missing 告警，序列未记）。
    # CLI 受理要求路径是已存在目录；不匹配任何空间 folder；作用域退出即清理
    with tempfile.TemporaryDirectory() as path:
        # 领养侧：既有 idle agent 无 pane → 补领养走路径零匹配 → 建空间
        w = FakeHerdr()
        w.add_terminal(address="t50", name=NAME, turn=5,
                       labels={"loop": "1", "loop-agent": "claude"})
        w.add_space(id="sp1", name="main", folder="/tmp/example-other")
        with tempfile.TemporaryDirectory() as td:
            r = harness.run(_cli_argv(td, "ps-adopt-zero", NAME, path,
                                      ("--expect-proc", "claude")), world=w)
            assert r.rc == 0 and r.result["ok"], r.result
            assert r.result["space_created"] is True, r.result
            news = [json.loads(c[2]) for c in w.app_calls
                    if c[0] == "do" and c[1] == "space.new"]
            assert news == [{"name": Path(path).name, "folder": path,
                             "focus": False}], news
        # 学习侧：pane 已在（领养提前返回），学习开启 → 只读投影零匹配 → 不建、不记
        w = FakeHerdr()
        w.add_terminal(address="t99", name="claude-test", labels={"loop": "1"})
        sp = w.add_space(id="sp1", name="main", folder="/tmp/example-other")
        w.add_tab(sp, terminals=["t99"])
        w.screen("t99", SCREEN)
        with tempfile.TemporaryDirectory() as td:
            ldir = Path(td) / "learn"
            r = harness.run(_cli_argv(td, "ps-learn-zero", "t99", path), world=w,
                            env={"LOOP_LEARNING": "on", "LOOP_LEARNING_DIR": str(ldir)})
            assert r.rc == 0 and r.result["ok"], r.result
            assert r.result["learning_warning"]["reason"] == "space_id_missing", r.result
            assert not any(c[0] == "do" and c[1] == "space.new" for c in w.app_calls), \
                "学习侧零匹配绝不建空间"
            seqs = (json.loads((ldir / "sequences.json").read_text(encoding="utf-8"))
                    if (ldir / "sequences.json").exists() else {})
            assert seqs == {}, "未解析出 space id，序列未记"


def main():
    test_path_unique()
    print("1 path unique: reuse id ok")
    test_path_zero()
    print("2 path zero: new_space proposal (caller decides) ok")
    test_path_ambiguous()
    print("3 path multi: space_ambiguous ok")
    test_name_states()
    print("4 name unique/missing/duplicated ok")
    test_id_states()
    print("5 id direct/missing, sp-prefix precedence ok")
    test_missing_id()
    print("6 matched space without id: space_not_found ok")
    test_normalization_and_purity()
    print("7 normalization (~, trailing slash, dot segments) and purity ok")
    test_caller_zero_match_policies()
    print("8 caller zero-match policies: adopt creates, learning neither ok")
    print("ALL PARSE_SPACE TESTS PASSED")


if __name__ == "__main__":
    main()
