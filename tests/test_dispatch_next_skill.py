#!/usr/bin/env python3
"""test_dispatch_next_skill.py — next_skill 预测字段测试（ADR-0008 修订，#48 阶段一）。

跑法：python3 tests/test_dispatch_next_skill.py
主测试缝＝唯一 harness.run（CLI 契约，T5d/ADR-0021）：FakeHerdr 假世界摆场景，
env / http_open / run_cmd 注入，真实抽取入口 dispatch.extract 经 harness 残留洞吸收
密闭跑；断言只认 rc、stdout JSON、result.json 落盘产物与请求体/argv。
纯函数（learning.project_next_skill / parse_extraction / verify_extraction）直调保留原位。
覆盖：
  1 project_next_skill：七项原样、"" 合法弃答；缺失/非字符串/带斜杠/带参数/集外 → ""＋invalid。
  2 parse：合法 next_skill 透传；缺失/非字符串投影 ""；meta 记 invalid；ext 恒六字段。
  3 形状门不变：缺 conclusion 仍 extraction_invalid；next_skill 不受形状门检查。
  4 verify：瞎编 next_skill 不进逐字校验、rewritten 空；全部瞎编门不变。
  5 e2e api：带预测 → result/结果文件逐字带 next_skill、无 next_command；指令含六字段。
  6 e2e api：缺预测 → next_skill="" 且 ok=true（宽容投影）。
  7 e2e pi：同形状透传；pi 指令含六字段。
  8 extract.md：next_skill 规格、唯一例外、七候选、预测优先级在位。

T5d（ADR-0021，#99）：e2e 用例迁 FakeHerdr＋harness.run。
R（#104）：共享常量与 world/api_env/FakeResp 迁 helpers 单一出处；EXT 五字段旧形状
经 helpers.EXT5 单一出处（BASE_EXT 缺字段用例依赖键缺失）。
"""
import json
import tempfile
import types
from pathlib import Path

import harness
from harness import dispatch
import learning
from helpers import PROMPT, SCREEN, world, api_env, FakeResp
from helpers import EXT5 as EXT   # 五字段旧形状（helpers 单一出处）

BASE_EXT = dict(EXT)  # 五字段旧形状（conclusion=""、reply=SCREEN）


def parse_ok(obj, meta=None):
    return dispatch.parse_extraction(json.dumps(obj, ensure_ascii=False), meta)


def run_round(td, rid, ext_obj, backend):
    """经唯一 harness.run 跑一轮完整 dispatch（真实抽取链＋网络/子进程出口桩）；
    返回 (rc, result, loop_dir, captured)。"""
    pf = Path(td) / "prompt.md"
    pf.write_text(PROMPT, encoding="utf-8")
    ld = Path(td) / ".loop"
    argv = ["--target", "t99", "--release-id", rid,
            "--prompt-file", str(pf), "--loop-dir", str(ld),
            "--space", "main",
            "--wall-clock", "5", "--check-in", "1",
            "--settle-seconds", "0.01", "--settle-checks", "2"]
    captured = {}
    payload = json.dumps(ext_obj, ensure_ascii=False)
    if backend == "api":
        def fake_open(req, timeout, proxies):
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return FakeResp({"choices": [{"message": {"content": payload}}]})
        r = harness.run(argv, world=world(), env=api_env(),
                        extract=dispatch.extract, http_open=fake_open)
    else:
        def fake_run(argv_, timeout):
            captured["argv"] = argv_
            return types.SimpleNamespace(returncode=0, stdout=payload, stderr="")
        r = harness.run(argv, world=world(), env={"LOOP_EXTRACT_BACKEND": "pi"},
                        extract=dispatch.extract, run_cmd=fake_run)
    return r.rc, r.result, ld, captured


def main():
    # 1 闭集投影纯函数：七项原样、"" 合法；其余 ""＋invalid
    for skill in learning.NEXT_SKILL_CANDIDATES:
        assert learning.project_next_skill(skill) == (skill, False), skill
    assert learning.project_next_skill("") == ("", False)
    for bad in (None, 42, {"a": 1}, ["x"], "/to-spec", "to-spec #7",
                "tdd", "Implement", " to-spec", "implement-spec extra"):
        assert learning.project_next_skill(bad) == ("", True), bad
    print("1 project_next_skill: closed-set projection + invalid flag ok")

    # 2 parse：合法透传；缺失/非字符串投影 ""；meta 记 invalid；ext 恒六字段
    meta = {}
    ext = parse_ok(dict(BASE_EXT, next_skill="to-spec"), meta)
    assert ext["next_skill"] == "to-spec", ext
    assert meta["next_skill_invalid"] is False, meta
    assert set(ext) == set(dispatch.EXTRACT_FIELDS), ext
    assert "next_command" not in ext, ext
    meta = {}
    ext = parse_ok(dict(BASE_EXT, next_skill=""), meta)
    assert ext["next_skill"] == "" and meta["next_skill_invalid"] is False
    meta = {}
    ext = parse_ok(BASE_EXT, meta)  # 缺失
    assert ext["next_skill"] == "" and meta["next_skill_invalid"] is True
    for bad in ({"a": 1}, 42, None, ["x"], "/implement pop-x"):
        meta = {}
        ext = parse_ok(dict(BASE_EXT, next_skill=bad), meta)
        assert ext["next_skill"] == "", (bad, ext)
        assert meta["next_skill_invalid"] is True, (bad, meta)
    print("2 parse: passthrough / lenient projection / meta invalid / six fields ok")

    # 3 形状门不变：缺 conclusion 仍拒；next_skill 不受形状门检查
    bad = {k: v for k, v in BASE_EXT.items() if k != "conclusion"}
    for obj in (bad, dict(bad, next_skill="wayfinder")):
        try:
            dispatch.parse_extraction(json.dumps(obj, ensure_ascii=False))
            raise AssertionError("缺 conclusion 应 extraction_invalid")
        except dispatch.DispatchError as e:
            assert e.kind == "extraction_invalid", e.kind
    print("3 shape gate unchanged: missing conclusion still invalid ok")

    # 4 瞎编 next_skill 豁免逐字校验；全部瞎编门不变
    ext = dict(BASE_EXT, conclusion="已完成该功能。", next_skill="implement")
    rewritten = dispatch.verify_extraction(ext, SCREEN, "/fake/turn.md", "/fake/raw.md", "pi")
    assert rewritten == {}, rewritten
    ext = {"needs_user": False, "questions": ["瞎编的问题"],
           "conclusion": "瞎编的结论", "blocked": "瞎编的弹框",
           "reply": SCREEN, "next_skill": "wayfinder"}
    try:
        dispatch.verify_extraction(ext, SCREEN, "/fake/turn.md", "/fake/raw.md", "pi")
        raise AssertionError("全部瞎编应 extraction_rewritten")
    except dispatch.DispatchError as e:
        assert e.kind == "extraction_rewritten", e.kind
    print("4 fabricated next_skill exempt from verbatim; all-fabricated gate unchanged ok")

    # 5 e2e api：带预测 → result/结果文件逐字带 next_skill、无 next_command；指令六字段
    with tempfile.TemporaryDirectory() as td:
        rc, res, ld, cap = run_round(
            td, "ns-api-1", dict(BASE_EXT, next_skill="to-spec"), "api")
        assert rc == 0 and res["ok"], res
        assert res["next_skill"] == "to-spec", res
        assert "next_command" not in res, res
        assert res["rewritten"] == {}, res
        on_disk = json.loads((ld / "runs" / "ns-api-1.result.json")
                             .read_text(encoding="utf-8"))
        assert on_disk == res and on_disk["next_skill"] == "to-spec"
        user_msg = cap["body"]["messages"][1]["content"]
        assert "next_skill" in user_msg and "六个字段" in user_msg
        assert "next_command" not in user_msg
    print("5 e2e api: next_skill in result/file verbatim, no next_command, six fields ok")

    # 6 e2e api：缺预测 → "" 且 ok
    with tempfile.TemporaryDirectory() as td:
        rc, res, ld, _ = run_round(td, "ns-api-2", BASE_EXT, "api")
        assert rc == 0 and res["ok"], res
        assert res["next_skill"] == "", res
    print("6 e2e api: missing prediction -> empty string, round ok ok")

    # 7 e2e pi：同形状透传；pi 指令含六字段
    with tempfile.TemporaryDirectory() as td:
        rc, res, ld, cap = run_round(
            td, "ns-pi-1", dict(BASE_EXT, next_skill="implement"), "pi")
        assert rc == 0 and res["ok"], res
        assert res["next_skill"] == "implement", res
        assert res["extract_backend"] == "pi", res
        instr = " ".join(cap["argv"])
        assert "next_skill" in instr and "六个字段" in instr
        assert "next_command" not in instr
    print("7 e2e pi: passthrough + six-field instruction ok")

    # 8 extract.md 规格、例外、七候选、优先级在位
    md = (Path(__file__).resolve().parent.parent / "loop" / "prompts" / "extract.md")
    text = md.read_text(encoding="utf-8")
    assert "next_skill" in text and "唯一推测字段" in text
    assert "唯一例外是 `next_skill`" in text
    assert "六个字段全部必须出现" in text
    assert "next_command" not in text
    for skill in learning.NEXT_SKILL_CANDIDATES:
        assert skill in text, skill
    assert "当前回复明示" in text and "当前近期序列" in text and "历史参考案例" in text
    print("8 extract.md spec + carve-out + candidates + priority present ok")

    print("ALL NEXT-SKILL TESTS PASSED")


if __name__ == "__main__":
    main()
