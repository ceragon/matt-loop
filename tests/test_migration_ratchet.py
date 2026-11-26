#!/usr/bin/env python3
"""test_migration_ratchet.py — 测试侧棘轮守卫（T4，ADR-0016 决定 10 / ADR-0021 决定 4）。

跑法：python3 tests/test_migration_ratchet.py
一个普通测试文件（CI 逐文件循环自动收）。核心是纯函数 scan(源码映射, 白名单)→问题列表：
AST 扫描测试目录全部 .py（含守卫自身），禁止两类新增违规——
  1 以 dispatch 为根的属性赋值（含 dispatch.time.sleep 嵌套链、元组目标、增量赋值）；
  2 os.environ 写入（下标赋值/删除、update/pop/setdefault/clear）。
字符串常量里的假源码不构成违规（不自伤）。

白名单是文件级字面量列表，每条带理由与退役切片。自检式「只缩不增」：
  - 非白名单文件出现违规→失败；
  - 白名单条目匹配不到任何真实违规→失败（逼 PR 删条）；
  - 终态列表清空、守卫变无条件断言（最后一个退役切片执行，P2/⑤ 与 G1/P2 之后）。
不依赖 git 状态（ADR-0021 Considered Options），任何检出环境都成立。
守卫用注入的假源码自证能报错（每种判定路径各一例），「守卫绿」不是守卫坏了的假绿。
"""
import ast
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent

# os.environ 的写方法（读方法 get/keys/items/copy 等不算违规）。
_ENV_WRITE_METHODS = frozenset({"update", "pop", "setdefault", "clear"})

# 文件级白名单：filename -> (理由, 退役切片)。只许缩小，不许新增；陈旧条目自检必红。
# 首版以实际扫描推导（T4）；T5a–T5d 把 16 个消费方按文件迁完、逐条删（21→15→11→8→5）；
# R 删三个供应方残留兼容面、删条（5→2，#104）；P2 收口 harness 的模块全局替换与
# test_command_parse 的 run_cmd 作用域替换（2→1）；P3/⑤ 把学习环境洞收口到
# deps.env，harness 不再作用域改写 os.environ（1→0）。终态片删常量改无条件断言；
# 本片保留空表，自检机制不动。
WHITELIST = {}


def _is_dispatch_attr_target(node):
    """目标是否「以 dispatch 为根的属性赋值」： unwrap Attribute/Subscript 链到根 Name。
    须至少一层 Attribute（dispatch = x 这种纯名字重绑定不算；dispatch.x / dispatch.time.sleep
    / dispatch.env["k"] 算）。"""
    seen_attr = False
    while isinstance(node, (ast.Attribute, ast.Subscript)):
        if isinstance(node, ast.Attribute):
            seen_attr = True
        node = node.value
    return seen_attr and isinstance(node, ast.Name) and node.id == "dispatch"


def _is_os_environ(node):
    """表达式是否 os.environ（Attribute os.environ，或 from os import environ 后的裸名）。"""
    if isinstance(node, ast.Attribute):
        return node.attr == "environ" and isinstance(node.value, ast.Name) and node.value.id == "os"
    return isinstance(node, ast.Name) and node.id == "environ"


def _targets(node):
    """展开赋值目标（元组/列表目标逐元素）。"""
    if isinstance(node, (ast.Tuple, ast.List)):
        for elt in node.elts:
            yield from _targets(elt)
    else:
        yield node


def find_violations(source):
    """AST 扫描一段源码，返回违规描述列表（带行号）。纯函数，不读磁盘、不依赖 git。"""
    tree = ast.parse(source)
    viols = []
    for node in ast.walk(tree):
        # 1 dispatch 为根的属性赋值（Assign/AnnAssign/AugAssign）
        if isinstance(node, ast.Assign):
            targets = [t for tgt in node.targets for t in _targets(tgt)]
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            targets = list(_targets(node.target))
        else:
            targets = []
        for t in targets:
            if _is_dispatch_attr_target(t):
                viols.append("L%d dispatch.<名> 赋值" % node.lineno)
            elif isinstance(t, ast.Subscript) and _is_os_environ(t.value):
                viols.append("L%d os.environ 下标赋值" % node.lineno)
        # 2 os.environ 下标删除
        if isinstance(node, ast.Delete):
            for t in node.targets:
                if isinstance(t, ast.Subscript) and _is_os_environ(t.value):
                    viols.append("L%d os.environ 下标删除" % node.lineno)
        # 3 os.environ 写方法调用
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if _is_os_environ(node.func.value) and node.func.attr in _ENV_WRITE_METHODS:
                viols.append("L%d os.environ.%s 写入" % (node.lineno, node.func.attr))
    return viols


def scan(sources, whitelist):
    """(文件名→源码映射, 白名单)→问题列表。空列表＝通过。纯函数，守卫与自证共用。"""
    problems = []
    for name in sorted(sources):
        viols = find_violations(sources[name])
        if name in whitelist:
            if not viols:
                problems.append("%s: 白名单条目陈旧（已无违规，须删条）" % name)
        elif viols:
            problems.append("%s: 未豁免违规 %d 处 [%s]" % (name, len(viols), "; ".join(viols)))
    # 白名单条目指向的文件已不存在（删/改名）→ 匹配不到任何真实违规，同样逼删条。
    for name in sorted(whitelist):
        if name not in sources:
            problems.append("%s: 白名单条目陈旧（文件已不存在，须删条）" % name)
    return problems


def _self_test():
    """注入假源码覆盖每种判定路径——不修改真实文件、不依赖 git。"""
    # 干净文件出现新违规必报（dispatch 赋值）
    assert scan({"clean.py": "dispatch.hc_json = fake\n"}, {}) != []
    # 白名单条目陈旧必报（逼删条）：已无违规
    assert scan({"old.py": "x = 1\n"}, {"old.py": ("理由", "R")}) != []
    # 白名单条目陈旧必报：文件已不存在（删/改名）
    assert scan({}, {"gone.py": ("理由", "R")}) != []
    # 白名单内违规容忍
    assert scan({"old.py": "dispatch.hc_json = fake\n"}, {"old.py": ("理由", "R")}) == []
    # 全干净通过
    assert scan({"a.py": "x = 1\n"}, {}) == []
    # dispatch 嵌套链 / 元组目标 / 增量赋值都报
    for src in ("dispatch.time.sleep = f\n",
                "dispatch.a, dispatch.b = 1, 2\n",
                "dispatch.n += 1\n",
                'dispatch.env["k"] = "v"\n'):
        assert find_violations(src), src
    # os.environ 写入各形态都报
    for src in ('os.environ["X"] = "1"\n',
                'del os.environ["X"]\n',
                'os.environ.update({"X": "1"})\n',
                'os.environ.pop("X", None)\n',
                'os.environ.setdefault("X", "1")\n',
                'os.environ.clear()\n',
                'os.environ["X"] += "1"\n',
                'environ["X"] = "1"\n'):
        assert find_violations(src), src
    # 读不算违规：os.environ.get / dict(os.environ) / 读 dispatch 属性 / 名字重绑定
    for src in ("y = os.environ.get('X')\n",
                "y = dict(os.environ)\n",
                "z = dispatch.hc_json\n",
                "dispatch = other\n",
                "for k in os.environ: pass\n"):
        assert find_violations(src) == [], src
    # 字符串常量里的假源码不自伤
    assert find_violations('CODE = "dispatch.hc_json = fake"\n') == []
    assert find_violations("CODE = 'os.environ[\"X\"] = \"1\"'\n") == []
    print("self-test: every verdict path exercised with injected fake source")


def main():
    _self_test()
    # 守卫扫描自身在内的真实测试目录；白名单自检式只缩不增。
    sources = {p.name: p.read_text(encoding="utf-8")
               for p in sorted(TESTS_DIR.glob("*.py"))}
    assert "test_migration_ratchet.py" in sources, "守卫须扫描自身"
    problems = scan(sources, WHITELIST)
    assert problems == [], "棘轮守卫失败：\n  " + "\n  ".join(problems)
    print("ratchet: %d files scanned, %d whitelisted, no new violations"
          % (len(sources), len(WHITELIST)))
    print("all migration-ratchet tests passed")


if __name__ == "__main__":
    main()