#!/usr/bin/env python3
"""test_extract_fields_consistency.py — 抽取字段清单跨文件一致性守卫（#133 R6）。

跑法：python3 tests/test_extract_fields_consistency.py
守住以下声明不漂移：
  0 抽取指令 `_EXTRACT_INSTRUCTION` 里点名的字段集 == dispatch.EXTRACT_FIELDS
    （指令已由该常量派生，本断言防止将来改回手抄字面后漂移）；
  1 prompts/extract.md「输出」节声明的六字段 == dispatch.EXTRACT_FIELDS；
  2 通知白名单里由抽取派生的字段 == EXTRACT_FIELDS 去掉 reply（reply 不进通知：
    api/pi 档要回看读 result.json 的 run_record，caller 档内联 turn_text）；
    run_record 不在白名单（ADR-0026 移出通知），extract_backend 恒带候选在场；
  3 逐字校验面：verify_extraction 只校验选段字段（conclusion/blocked/questions），
    reply 走 verify_reply，needs_user / next_skill 两个非逐字字段豁免。
pipeline.py / turn.py 没有独立的字段清单声明：前者只在 docstring 说明既有结果载荷，
后者的逐字校验字段已由第 3 条覆盖。
断言只认外部可观察面（提示词文本、常量、纯函数行为），不锁内部结构。
"""
import re
from pathlib import Path

import harness  # noqa: F401  （把 loop/scripts 放进 sys.path）
from harness import dispatch

ROOT = Path(__file__).resolve().parent.parent
TURN = "锚点句"
BASE = {"needs_user": False, "questions": [], "conclusion": "", "blocked": "",
        "reply": "", "next_skill": ""}


def prompt_fields():
    """提示词「## 输出」节里的字段 bullet：- `name`（...。"""
    text = (ROOT / "loop" / "prompts" / "extract.md").read_text(encoding="utf-8")
    section = text.split("## 输出", 1)[1].split("## next_skill", 1)[0]
    return tuple(re.findall(r"^- `([a-z_]+)`（", section, re.MULTILINE))


def instruction_fields():
    """抽取指令里点名的字段标识符（JSON 是大写，不会被这条正则收进来）。"""
    return re.findall(r"[a-z_]+", dispatch._EXTRACT_INSTRUCTION)


def rewritten(**over):
    """conclusion 恒放一段原文里有的字，保证至少一个被校验字段通过——这样探针
    字段失败时返回 rewritten，而不是触发「全部未过校验」整轮报错。"""
    ext = dict(BASE, conclusion=TURN)
    ext.update(over)
    return dispatch.verify_extraction(ext, TURN, "/fake/turn.md", "/fake/raw.md", "api")


def main():
    # 0 抽取指令点名的字段集 == 抽取输出形状门用的字段集
    named = instruction_fields()
    assert len(named) == 6, named
    assert set(named) == set(dispatch.EXTRACT_FIELDS), (
        named, dispatch.EXTRACT_FIELDS)
    print("0 extract instruction fields == EXTRACT_FIELDS ok")

    # 1 提示词声明的字段集 == 抽取输出形状门用的字段集
    declared = prompt_fields()
    assert len(declared) == 6, declared
    assert set(declared) == set(dispatch.EXTRACT_FIELDS), (
        declared, dispatch.EXTRACT_FIELDS)
    print("1 extract.md declared fields == EXTRACT_FIELDS ok")

    # 2 通知白名单派生自抽取字段：除 reply 外全部在场；run_record 已移出（ADR-0026）
    notify = set(dispatch.NOTIFY_CANDIDATE_FIELDS)
    derived = notify & set(dispatch.EXTRACT_FIELDS)
    assert derived == set(dispatch.EXTRACT_FIELDS) - {"reply"}, derived
    assert "run_record" not in notify, notify
    assert "extract_backend" in notify, notify
    print("2 notify whitelist covers every extract field except reply, no run_record ok")

    # 3 逐字校验字段集：选段字段可探针命中，非逐字字段豁免；reply 走 verify_reply
    assert "conclusion" in rewritten(conclusion="瞎编结论", blocked=TURN)
    assert "blocked" in rewritten(blocked="瞎编弹框")
    assert "questions[0]" in rewritten(questions=["瞎编问题"])
    assert rewritten(needs_user=True) == {}
    assert rewritten(next_skill="wayfinder") == {}
    assert dispatch.verify_reply("瞎编回复", TURN)
    verified = {"conclusion", "blocked", "questions"}
    exempt_fields = {"needs_user", "next_skill"}
    assert verified | exempt_fields == set(dispatch.EXTRACT_FIELDS) - {"reply"}
    print("3 verbatim-check field partition == EXTRACT_FIELDS minus reply ok")

    print("ALL EXTRACT FIELD CONSISTENCY TESTS PASSED")


if __name__ == "__main__":
    main()
