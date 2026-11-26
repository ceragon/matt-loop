#!/usr/bin/env python3
"""test_extract_fields_consistency.py — 抽取字段清单跨文件一致性守卫（#133 R6）。

跑法：python3 tests/test_extract_fields_consistency.py
守住三处互相独立的声明不漂移：
  1 prompts/extract.md「输出」节声明的六字段 == dispatch.EXTRACT_FIELDS；
  2 通知白名单里由抽取派生的字段 == EXTRACT_FIELDS 去掉 reply（reply 在通知里
    由 run_record 取代，run_record 必须在场）；
  3 逐字校验面：verify_extraction 只校验选段字段（conclusion/blocked/questions），
    reply 走 verify_reply，needs_user / next_skill 两个非逐字字段豁免。
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


def rewritten(**over):
    """conclusion 恒放一段原文里有的字，保证至少一个被校验字段通过——这样探针
    字段失败时返回 rewritten，而不是触发「全部未过校验」整轮报错。"""
    ext = dict(BASE, conclusion=TURN)
    ext.update(over)
    return dispatch.verify_extraction(ext, TURN, "/fake/turn.md", "/fake/raw.md", "api")


def main():
    # 1 提示词声明的字段集 == 抽取输出形状门用的字段集
    declared = prompt_fields()
    assert len(declared) == 6, declared
    assert set(declared) == set(dispatch.EXTRACT_FIELDS), (
        declared, dispatch.EXTRACT_FIELDS)
    print("1 extract.md declared fields == EXTRACT_FIELDS ok")

    # 2 通知白名单派生自抽取字段：除 reply（以 run_record 提供回看）外全部在场
    notify = set(dispatch.NOTIFY_SUCCESS_FIELDS)
    derived = notify & set(dispatch.EXTRACT_FIELDS)
    assert derived == set(dispatch.EXTRACT_FIELDS) - {"reply"}, derived
    assert "run_record" in notify, notify
    print("2 notify whitelist covers every extract field except reply ok")

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
