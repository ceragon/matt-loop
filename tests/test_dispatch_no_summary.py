#!/usr/bin/env python3
"""test_dispatch_no_summary.py — summary 移除回归测试（FakeHerdr 假世界，不打真实 agent）。

跑法：python3 tests/test_dispatch_no_summary.py
覆盖：
  1 parse_extraction：pi 不给 summary → 照常解析，结果无 summary 键。
  2 parse_extraction：pi 多给 summary（字符串或任意类型）→ 直接丢弃，不报错。
  3 parse_extraction：其余字段形状校验不变（缺 conclusion 仍 extraction_invalid）。
  4 verify_extraction：ext 里混入瞎编 summary → 不校验、rewritten 保持空。
  5 verify_extraction：conclusion/questions/blocked 逐字校验照旧；全部瞎编仍 error。
  6 端到端：pi 输出带瞎编 summary → result 无 summary、rewritten 空、reply 零坏行。

T5a（ADR-0021，#99）：纯函数直调用例（1–5）保留原位；端到端（6）迁 FakeHerdr＋
harness.run，真实抽取入口 dispatch.extract 作为 extract 依赖传入、注入 run_cmd
（T4 api_extract 先例）。R（#104）：共享常量与 world/argv 构造器迁 helpers 单一出处；BASE（五字段语料 dict）留本地。
"""
import json
import tempfile
import types

import harness
from harness import dispatch
from helpers import SCREEN, world, argv

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
    rewritten = dispatch.verify_extraction(ext, SCREEN, "/fake/turn.md", "/fake/raw.md", "pi")
    assert rewritten == {}, rewritten
    print("4 fabricated summary ignored by verify, rewritten empty ok")

    # 5 conclusion/questions/blocked 校验照旧（配一条逐字问题，避开「全部瞎编」门）
    ext = dict(BASE, conclusion="瞎编的结论", questions=["已完成该功能。"])
    rewritten = dispatch.verify_extraction(ext, SCREEN, "/fake/turn.md", "/fake/raw.md", "pi")
    assert set(rewritten) == {"conclusion"}, rewritten
    ext = {"needs_user": False, "questions": ["瞎编的问题"],
           "conclusion": "瞎编的结论", "blocked": "瞎编的弹框"}
    try:
        dispatch.verify_extraction(ext, SCREEN, "/fake/turn.md", "/fake/raw.md", "pi")
        raise AssertionError("全部瞎编应 extraction_rewritten")
    except dispatch.DispatchError as e:
        assert e.kind == "extraction_rewritten", e.kind
    print("5 conclusion/questions/blocked verbatim checks unchanged ok")

    # 5b 校验闸门回归（#127，零行为改动）：空选段成功、全失败 hints 形状不变、
    #    部分失败只标记。语料＝本轮原料（SCREEN）。
    empty = {"needs_user": False, "questions": [], "conclusion": "",
             "blocked": "", "next_skill": ""}
    rewritten = dispatch.verify_extraction(empty, SCREEN, "/fake/turn.md",
                                           "/fake/raw.md", "api")
    assert rewritten == {}, rewritten
    all_bad = {"needs_user": False, "questions": ["瞎编的问题"],
               "conclusion": "瞎编的结论", "blocked": "瞎编的弹框", "next_skill": ""}
    try:
        dispatch.verify_extraction(all_bad, SCREEN, "/fake/turn.md",
                                   "/fake/raw.md", "api")
        raise AssertionError("全部瞎编应 extraction_rewritten")
    except dispatch.DispatchError as e:
        assert e.kind == "extraction_rewritten" and e.returncode == 1
        # #50 载荷形状不变：每个失败字段一条「字段名＋最接近的原文整行」，
        # 末尾两条路径提示（原料、回读全文）。
        assert len(e.hints) == 5, e.hints
        assert e.hints[0].startswith("conclusion 未过逐字校验，最接近的原文整行：")
        assert e.hints[1].startswith("blocked 未过逐字校验，最接近的原文整行：")
        assert e.hints[2].startswith("questions[0] 未过逐字校验，最接近的原文整行：")
        assert e.hints[3] == "本轮原料已落盘：/fake/turn.md"
        assert e.hints[4] == "回读全文已落盘：/fake/raw.md"
        assert "api 在改写而不是抽取" in e.message
    print("5b empty selections succeed, all-fail hints shape unchanged ok")

    # 6 端到端：pi 输出带瞎编 summary（真实抽取链：extract=dispatch.extract＋注入 run_cmd）
    pi_out = json.dumps(dict(BASE, summary="pi 瞎编的概括"), ensure_ascii=False)
    w = world()
    with tempfile.TemporaryDirectory() as td:
        r = harness.run(argv(td, "nosum-e2e"), world=w,
                        extract=dispatch.extract,
                        run_cmd=lambda argv_, timeout: types.SimpleNamespace(
                            returncode=0, stdout=pi_out, stderr=""))
        assert r.rc == 0 and r.result["ok"], r.result
        assert "summary" not in r.result, r.result
        assert r.result["rewritten"] == {}, r.result
        assert r.ledger["reply_bad_lines"] == 0, r.ledger
        on_disk = json.loads(r.result_path.read_text(encoding="utf-8"))
        assert "summary" not in on_disk and on_disk == r.result
    print("6 e2e: fabricated summary dropped, rewritten empty, reply 0 bad lines ok")

    print("ALL NO-SUMMARY TESTS PASSED")


if __name__ == "__main__":
    main()
