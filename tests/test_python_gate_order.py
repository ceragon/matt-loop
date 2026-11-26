#!/usr/bin/env python3
"""test_python_gate_order.py — 版本门必须先于兄弟模块 import（#135）。

背景：兄弟模块（herdr/pipeline/turn 等）用了 `str | None` 这类 3.10+ 语法。若版本门
写在它们之后，Python <3.10 会先撞 SyntaxError，既不自举也报不出 python_too_old。

本守卫只断言源码顺序这一条结构不变量：`sys.version_info < (3, 10)` 的检查出现在
第一个兄弟模块 import 之前。行为本身（3.9 自举、缺 uv 报 python_too_old）在 3.9 真机
验收，CI 只有 3.12 覆盖不到。
"""
from pathlib import Path

DISPATCH = Path(__file__).resolve().parent.parent / "loop" / "scripts" / "dispatch.py"
GATE_MARK = "sys.version_info < (3, 10)"
# 兄弟模块（部署单元内、dispatch 直接 import 的模块）的 import 首词组合。
SIBLING_IMPORTS = ("import learning", "import pipeline", "from errors import",
                   "from herdr import", "from pipeline import", "from turn import")


def main():
    lines = DISPATCH.read_text(encoding="utf-8").splitlines()
    gate = next((i for i, ln in enumerate(lines) if GATE_MARK in ln), None)
    assert gate is not None, "dispatch.py 里找不到版本门：%s" % GATE_MARK
    siblings = [i for i, ln in enumerate(lines) if ln.startswith(SIBLING_IMPORTS)]
    assert siblings, "dispatch.py 里找不到兄弟模块 import"
    first = min(siblings)
    assert gate < first, (
        "版本门在第 %d 行、第一个兄弟 import 在第 %d 行：门必须先于 import（#135）"
        % (gate + 1, first + 1))
    print("python gate precedes sibling imports ok (gate line %d < first import line %d)"
          % (gate + 1, first + 1))


if __name__ == "__main__":
    main()
