#!/usr/bin/env python3
"""test_dispatch_next_command.py — next_command 预测字段测试（ADR-0008，mock hc/网络，不打真实 agent）。

跑法：python3 loop/scripts/test_dispatch_next_command.py
覆盖：
  1 parse：next_command 字符串原样透传；空串保持空。
  2 parse：缺失 → ""；非字符串（dict/int/null/list）→ ""，不整轮报错。
  3 形状门不变：缺 conclusion 仍 extraction_invalid；next_command 不受形状门检查。
  4 verify：瞎编 next_command 不进逐字校验、rewritten 空；全部瞎编门不变。
  5 e2e api：带预测 → result/结果文件逐字带 next_command；指令含六字段。
  6 e2e api：缺预测 → next_command="" 且 ok=true（宽容投影）。
  7 e2e pi：同形状透传；pi 指令含六字段。
  8 extract.md：next_command 规格与「不推测」规则显式例外在位。
"""
import contextlib
import io
import json
import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dispatch  # noqa: E402

from test_dispatch_api_extract import (  # noqa: E402
    PROMPT, SCREEN, EXT, FakeHc, FakeResp, api_env, env,
)

BASE_EXT = dict(EXT)  # 五字段旧形状（conclusion=""、reply=SCREEN）


def parse_ok(obj):
    return dispatch.parse_extraction(json.dumps(obj, ensure_ascii=False))


def run_round(td, rid, ext_obj, backend):
    """跑一轮完整 dispatch（mock hc＋抽取出口）；返回 (rc, result, loop_dir, captured)。"""
    pf = Path(td) / "prompt.md"
    pf.write_text(PROMPT, encoding="utf-8")
    ld = Path(td) / ".loop"
    argv = ["--target", "t99", "--release-id", rid,
            "--prompt-file", str(pf), "--loop-dir", str(ld),
            "--wall-clock", "5", "--check-in", "1",
            "--settle-seconds", "0.01", "--settle-checks", "2"]
    dispatch.hc_json = FakeHc()
    dispatch.check_structure_and_tools = lambda: None
    dispatch.shutil.which = lambda name: "/fake/" + name
    dispatch.time.sleep = lambda s: None
    captured = {}
    payload = json.dumps(ext_obj, ensure_ascii=False)
    if backend == "api":
        def fake_open(req, timeout, proxies=None):
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return FakeResp({"choices": [{"message": {"content": payload}}]})
        dispatch.webhook_open = fake_open
    else:
        def fake_run(argv_, timeout):
            captured["argv"] = argv_
            return types.SimpleNamespace(returncode=0, stdout=payload, stderr="")
        dispatch.run_cmd = fake_run
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = dispatch.main(argv)
    res = json.loads(buf.getvalue().strip())
    return rc, res, ld, captured


def main():
    # 1 字符串原样透传；空串保持空
    ext = parse_ok(dict(BASE_EXT, next_command="/to-spec"))
    assert ext["next_command"] == "/to-spec", ext
    assert set(ext) == set(dispatch.EXTRACT_FIELDS), ext
    ext = parse_ok(dict(BASE_EXT, next_command=""))
    assert ext["next_command"] == "", ext
    print("1 next_command str passthrough, empty stays empty ok")

    # 2 缺失/非字符串 → ""，不整轮报错
    ext = parse_ok(BASE_EXT)
    assert ext["next_command"] == "", ext
    for bad in ({"a": 1}, 42, None, ["x"]):
        ext = parse_ok(dict(BASE_EXT, next_command=bad))
        assert ext["next_command"] == "", (bad, ext)
    print("2 missing/non-str next_command -> empty string, round not failed ok")

    # 3 形状门不变：缺 conclusion 仍拒；next_command 不受形状门检查
    bad = {k: v for k, v in BASE_EXT.items() if k != "conclusion"}
    for obj in (bad, dict(bad, next_command="/wayfinder")):
        try:
            dispatch.parse_extraction(json.dumps(obj, ensure_ascii=False))
            raise AssertionError("缺 conclusion 应 extraction_invalid")
        except dispatch.DispatchError as e:
            assert e.kind == "extraction_invalid", e.kind
    print("3 shape gate unchanged: missing conclusion still invalid ok")

    # 4 瞎编 next_command 豁免逐字校验；全部瞎编门不变
    ext = dict(BASE_EXT, conclusion="已完成该功能。",
               next_command="/implement pop-xxxx.1")
    rewritten = dispatch.verify_extraction(ext, SCREEN, "/fake/raw.md")
    assert rewritten == {}, rewritten
    ext = {"needs_user": False, "questions": ["瞎编的问题"],
           "conclusion": "瞎编的结论", "blocked": "瞎编的弹框",
           "reply": SCREEN, "next_command": "/wayfinder"}
    try:
        dispatch.verify_extraction(ext, SCREEN, "/fake/raw.md")
        raise AssertionError("全部瞎编应 extraction_rewritten")
    except dispatch.DispatchError as e:
        assert e.kind == "extraction_rewritten", e.kind
    print("4 fabricated next_command exempt from verbatim; all-fabricated gate unchanged ok")

    # 5 e2e api：带预测 → result/结果文件逐字带；指令含六字段
    with api_env():
        with tempfile.TemporaryDirectory() as td:
            rc, res, ld, cap = run_round(
                td, "nc-api-1", dict(BASE_EXT, next_command="/to-spec"), "api")
            assert rc == 0 and res["ok"], res
            assert res["next_command"] == "/to-spec", res
            assert res["rewritten"] == {}, res
            on_disk = json.loads((ld / "runs" / "nc-api-1.result.json")
                                 .read_text(encoding="utf-8"))
            assert on_disk == res and on_disk["next_command"] == "/to-spec"
            user_msg = cap["body"]["messages"][1]["content"]
            assert "next_command" in user_msg and "六个字段" in user_msg
    print("5 e2e api: prediction in result/file verbatim, instruction six fields ok")

    # 6 e2e api：缺预测 → "" 且 ok
    with api_env():
        with tempfile.TemporaryDirectory() as td:
            rc, res, ld, _ = run_round(td, "nc-api-2", BASE_EXT, "api")
            assert rc == 0 and res["ok"], res
            assert res["next_command"] == "", res
    print("6 e2e api: missing prediction -> empty string, round ok ok")

    # 7 e2e pi：同形状透传；pi 指令含六字段
    with env(LOOP_EXTRACT_BACKEND="pi"):
        with tempfile.TemporaryDirectory() as td:
            rc, res, ld, cap = run_round(
                td, "nc-pi-1",
                dict(BASE_EXT, next_command="/implement pop-test.1"), "pi")
            assert rc == 0 and res["ok"], res
            assert res["next_command"] == "/implement pop-test.1", res
            assert res["extract_backend"] == "pi", res
            instr = " ".join(cap["argv"])
            assert "next_command" in instr and "六个字段" in instr
    print("7 e2e pi: passthrough + six-field instruction ok")

    # 8 extract.md 规格与例外在位
    md = (Path(__file__).resolve().parent.parent / "prompts" / "extract.md")
    text = md.read_text(encoding="utf-8")
    assert "next_command" in text and "唯一推测字段" in text
    assert "唯一例外是 `next_command`" in text
    assert "六个字段全部必须出现" in text
    print("8 extract.md spec + explicit carve-out present ok")

    print("ALL NEXT-COMMAND TESTS PASSED")


if __name__ == "__main__":
    main()
