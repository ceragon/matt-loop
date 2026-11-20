#!/usr/bin/env python3
"""test_dispatch_no_summary.py — summary 移除回归测试（mock hc/pi，不打真实 agent）。

跑法：python3.12 loop/scripts/test_dispatch_no_summary.py
覆盖：
  1 parse_extraction：pi 不给 summary → 照常解析，结果无 summary 键。
  2 parse_extraction：pi 多给 summary（字符串或任意类型）→ 直接丢弃，不报错。
  3 parse_extraction：其余字段形状校验不变（缺 conclusion 仍 extraction_invalid）。
  4 verify_extraction：ext 里混入瞎编 summary → 不校验、rewritten 保持空。
  5 verify_extraction：conclusion/questions/blocked 逐字校验照旧；全部瞎编仍 error。
  6 端到端：pi 输出带瞎编 summary → result 无 summary、rewritten 空、reply 零坏行。
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
from test_dispatch_reset import PROMPT, SCREEN, FakeHc  # noqa: E402

BASE = {"needs_user": False, "questions": [],
        "conclusion": "已完成该功能。", "blocked": "", "reply": SCREEN}


def parse_ok(obj):
    return dispatch.parse_extraction(json.dumps(obj, ensure_ascii=False))


def main():
    # 1 不给 summary → 照常解析
    ext = parse_ok(BASE)
    assert "summary" not in ext, ext
    assert set(ext) == set(dispatch.EXTRACT_FIELDS), ext
    print("1 no summary in pi output: parses, no summary key ok")

    # 2 多给 summary → 丢弃，类型不限
    for extra in ("pi 瞎编的概括", {"nested": 1}, 42, None):
        obj = dict(BASE, summary=extra)
        ext = parse_ok(obj)
        assert "summary" not in ext, ext
        assert ext["conclusion"] == BASE["conclusion"]
    print("2 extra summary (str/dict/int/null): silently dropped ok")

    # 3 其余形状校验不变
    bad = {k: v for k, v in BASE.items() if k != "conclusion"}
    try:
        dispatch.parse_extraction(json.dumps(bad, ensure_ascii=False))
        raise AssertionError("缺 conclusion 应 extraction_invalid")
    except dispatch.DispatchError as e:
        assert e.kind == "extraction_invalid", e.kind
    print("3 missing conclusion still extraction_invalid ok")

    # 4 混入瞎编 summary 不校验、不进 rewritten
    ext = dict(BASE, summary="原文里根本没有这句话")
    rewritten = dispatch.verify_extraction(ext, SCREEN, "/fake/raw.md")
    assert rewritten == {}, rewritten
    print("4 fabricated summary ignored by verify, rewritten empty ok")

    # 5 conclusion/questions/blocked 校验照旧（配一条逐字问题，避开「全部瞎编」门）
    ext = dict(BASE, conclusion="瞎编的结论", questions=["已完成该功能。"])
    rewritten = dispatch.verify_extraction(ext, SCREEN, "/fake/raw.md")
    assert set(rewritten) == {"conclusion"}, rewritten
    ext = {"needs_user": False, "questions": ["瞎编的问题"],
           "conclusion": "瞎编的结论", "blocked": "瞎编的弹框"}
    try:
        dispatch.verify_extraction(ext, SCREEN, "/fake/raw.md")
        raise AssertionError("全部瞎编应 extraction_rewritten")
    except dispatch.DispatchError as e:
        assert e.kind == "extraction_rewritten", e.kind
    print("5 conclusion/questions/blocked verbatim checks unchanged ok")

    # 6 端到端：pi 输出带瞎编 summary
    with tempfile.TemporaryDirectory() as td:
        pf = Path(td) / "prompt.md"
        pf.write_text(PROMPT, encoding="utf-8")
        ld = Path(td) / ".loop"
        argv = ["--target", "t99", "--release-id", "nosum-e2e",
                "--prompt-file", str(pf), "--loop-dir", str(ld),
                "--wall-clock", "5", "--check-in", "1",
                "--settle-seconds", "0.01", "--settle-checks", "2"]
        pi_out = json.dumps(dict(BASE, summary="pi 瞎编的概括"), ensure_ascii=False)
        dispatch.hc_json = FakeHc()
        dispatch.run_cmd = lambda argv_, timeout: types.SimpleNamespace(
            returncode=0, stdout=pi_out, stderr="")
        dispatch.check_structure_and_tools = lambda: None
        dispatch.shutil.which = lambda name: "/fake/" + name
        dispatch.time.sleep = lambda s: None
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = dispatch.main(argv)
        res = json.loads(buf.getvalue().strip())
        led = json.loads((ld / "runs" / "nosum-e2e.json").read_text(encoding="utf-8"))
        assert rc == 0 and res["ok"], res
        assert "summary" not in res, res
        assert res["rewritten"] == {}, res
        assert led["reply_bad_lines"] == 0, led
        on_disk = json.loads((ld / "runs" / "nosum-e2e.result.json").read_text(encoding="utf-8"))
        assert "summary" not in on_disk and on_disk == res
    print("6 e2e: fabricated summary dropped, rewritten empty, reply 0 bad lines ok")

    print("ALL NO-SUMMARY TESTS PASSED")


if __name__ == "__main__":
    main()
