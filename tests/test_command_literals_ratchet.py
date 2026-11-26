#!/usr/bin/env python3
"""test_command_literals_ratchet.py — 命令字面量棘轮守卫（G1，#109，ADR-0021 决定 4）。

跑法：python3 tests/test_command_literals_ratchet.py
扫描 `loop/scripts/dispatch.py`：herdr 命令字面量只许留在白名单函数里，白名单
随 G2/G3 迁移逐批缩小、终态清空（清空后删常量、改无条件断言，归最后退役切片）。

判据（可机械检验的「命令字面量」）：传给 `hc_json` / `app_json` 的 argv 里，
首元素是字符串字面量（app 的 `do` 取第二元素为 do 子命令）。变量 argv 不算——
它还没把命令词写进分发脚本。函数级归属取最内层 `def`，模块级记 `<module>`。

自检式「只缩不增」：白名单条目不再命中、或函数已不存在，守卫自己失败，逼 PR
删条。不依赖 git 状态（ADR-0021 Considered Options）。注入假源码覆盖每种判定
路径，守卫绿不是守卫坏了的假绿。
"""
import ast
import collections
from pathlib import Path

DISPATCH = Path(__file__).resolve().parent.parent / "loop" / "scripts" / "dispatch.py"

# 白名单：函数名 -> (理由, 退役切片)。只许缩小；陈旧条目自检必红。
# 首版以实际扫描推导（G1）：15 个函数、35 处字面量。G2 迁会话簇后删 G2 条，
# G3 迁空间簇后清空。
WHITELIST = {
    "adopt": ("领养：status/ls/space.new/terminal.keep/tab.close 与 hc get/rm 混用", "G3"),
    "apply_display_name": ("显示名落地：ls 定位与 tab.rename", "G3"),
    "do_create": ("创建前置：hc run 与 label", "G2"),
    "do_send": ("发送：hc send --if idle 与 done 复核 get / 补发", "G2"),
    "main": ("主流程残留：hc get / kill / read", "G2"),
    "reset_settle": ("前置落定：hc get 与 read", "G2"),
    "resolve_space_id_for_learning": ("学习侧空间解析：只读 ls", "G3"),
    "restore_space": ("选中还原：status 读当前选中", "G3"),
    "select_and_verify": ("空间切换：space.select 与 status 复核", "G3"),
    "send_launch_command": ("启动命令：裸 hc send", "G2"),
    "settle": ("落定复核：hc get", "G2"),
    "wait_for_agent_ready": ("等 agent 就绪：hc get", "G2"),
    "wait_for_reset": ("等前置落定：hc get", "G2"),
    "wait_for_result": ("等结果：hc wait 与兜底 hc get", "G2"),
    "wait_for_shell_ready": ("等 shell 就绪：hc get", "G2"),
}


def command_literal(call):
    """Call 若是 hc_json/app_json 且 argv 首元素是字面量，返回命令词；否则 None。

    两种调用形：`deps.hc_json(argv, timeout)`（argv 在 args[0]）与模块级
    `hc_json(bin, argv, timeout)`（argv 在 args[1]）。
    """
    func = call.func
    if isinstance(func, ast.Attribute):
        name, argv_index = func.attr, 0
    elif isinstance(func, ast.Name):
        name, argv_index = func.id, 1
    else:
        return None
    if name not in ("hc_json", "app_json"):
        return None
    if len(call.args) <= argv_index:
        return None
    argv = call.args[argv_index]
    if not isinstance(argv, (ast.List, ast.Tuple)) or not argv.elts:
        return None
    head = argv.elts[0]
    if not (isinstance(head, ast.Constant) and isinstance(head.value, str)):
        return None
    if name == "app_json" and head.value == "do" and len(argv.elts) > 1:
        sub = argv.elts[1]
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            return "do " + sub.value
    return head.value


class _Collector(ast.NodeVisitor):
    def __init__(self):
        self._scope = []
        self.hits = collections.defaultdict(list)

    def visit_FunctionDef(self, node):
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Call(self, node):
        literal = command_literal(node)
        if literal is not None:
            scope = self._scope[-1] if self._scope else "<module>"
            self.hits[scope].append((node.lineno, literal))
        self.generic_visit(node)


def collect(source):
    """返回 {函数名: [(行号, 命令词)]}；纯函数，不读磁盘、不依赖 git。"""
    collector = _Collector()
    collector.visit(ast.parse(source))
    return dict(collector.hits)


def scan(source, whitelist):
    """(源码, 白名单)→问题列表。空列表＝通过。守卫与自证共用。"""
    hits = collect(source)
    problems = []
    for scope in sorted(hits):
        if scope not in whitelist:
            detail = "; ".join("L%d %s" % item for item in hits[scope])
            problems.append("%s: 未豁免 herdr 命令字面量 %d 处 [%s]"
                            % (scope, len(hits[scope]), detail))
    for scope in sorted(whitelist):
        if scope not in hits:
            problems.append("%s: 白名单条目陈旧（已无命令字面量，须删条）" % scope)
    return problems


def _self_test():
    """注入假源码覆盖每种判定路径——不修改真实文件、不依赖 git。"""
    # 非 argv 字面量不算（普通字符串/注释不误伤）
    assert scan('def f():\n    x = "get"\n', {}) == []
    # hc/app 两种入口、两种调用形都报
    for src in ('def f():\n    deps.hc_json(["get", "t"], 30)\n',
                'def f():\n    hc_json(bin, ["get", "t"], 30)\n',
                'def f():\n    deps.app_json(["ls"], 30)\n',
                'def f():\n    app_json(bin, ["ls"], 30)\n',
                'def f():\n    deps.app_json(["do", "tab.close", "{}"], 30)\n'):
        assert scan(src, {}) != [], src
    # 变量 argv 不算
    assert scan("def f():\n    deps.hc_json(argv, 30)\n", {}) == []
    # 白名单内违规容忍
    assert scan('def f():\n    deps.hc_json(["get"], 30)\n',
                {"f": ("理由", "G2")}) == []
    # 白名单条目陈旧必报（函数还在但已无违规）
    assert scan("def f():\n    x = 1\n", {"f": ("理由", "G2")}) != []
    # 白名单条目陈旧必报（函数已不存在）
    assert scan("def g():\n    x = 1\n", {"f": ("理由", "G2")}) != []
    # 模块级命令字面量归 <module>，可被白名单接住
    assert scan('deps.hc_json(["get"], 30)\n', {}) != []
    assert scan('deps.hc_json(["get"], 30)\n', {"<module>": ("理由", "G2")}) == []
    # 干净文件通过
    assert scan("def f():\n    x = 1\n", {}) == []
    # 嵌套函数取最内层归属
    nested = "def outer():\n    def inner():\n        deps.hc_json([\"get\"], 30)\n"
    assert list(collect(nested)) == ["inner"]
    print("self-test: every verdict path exercised with injected fake source")


def main():
    _self_test()
    source = DISPATCH.read_text(encoding="utf-8")
    hits = collect(source)
    problems = scan(source, WHITELIST)
    assert problems == [], "命令字面量棘轮失败：\n  " + "\n  ".join(problems)
    print("ratchet: %d functions / %d literal sites whitelisted for G2/G3"
          % (len(hits), sum(len(v) for v in hits.values())))
    print("all command-literal ratchet tests passed")


if __name__ == "__main__":
    main()
