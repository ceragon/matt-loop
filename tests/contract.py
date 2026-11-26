"""contract.py — 契约一致性检查器（T3，ADR-0016 决定 6；ADR-0018 修订后为稳定资产）。

核对假世界返回的三样：必填键是否齐、枚举域取值是否在范围内、键的类型是否对。
不做键集精确相等（真实 hc get 约 30 字段，fake 只需表达 dispatch 读的那部分）。
契约来源：研究票 #72 的实测样例与 hc schema / herdr-app 文档
（tests/fixtures/README.md 逐文件分级；假设形状显式标注）。

检查对象是假世界的返回，不是生产代码。FakeHerdr 每次成功返回前自检，
抄错一个键名当场失败，而不是等到某个断言莫名其妙地过。
"""
import json
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load_sample(name):
    """读一份脱敏样例（检查器测试与解析测试共用同一批 fixtures）。"""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class ContractError(AssertionError):
    """返回形状与契约不符（缺必填键 / 枚举域外 / 类型错）。"""


class Enum:
    def __init__(self, *values):
        self.values = values

    def ok(self, v):
        return v in self.values

    def __repr__(self):
        return "enum%r" % (self.values,)


class Nullable:
    def __init__(self, spec):
        self.spec = spec

    def __repr__(self):
        return "nullable(%r)" % (self.spec,)


class Obj:
    """嵌套对象：required 里的键必须在且合规；optional 里的键出现才核对。"""

    def __init__(self, required=None, optional=None):
        self.required = required or {}
        self.optional = optional or {}


class Arr:
    def __init__(self, items):
        self.items = items


class AnyOf:
    """任一规格满足即过（T5a）：防御场景表达用，如 turn 非整数（实测恒整数）。"""

    def __init__(self, *specs):
        self.specs = specs

    def __repr__(self):
        return "any%r" % (self.specs,)


def _type_ok(spec, v):
    if isinstance(spec, AnyOf):
        return any(_type_ok(s, v) for s in spec.specs)
    if isinstance(spec, Enum):
        return spec.ok(v)
    if isinstance(spec, Nullable):
        return v is None or _type_ok(spec.spec, v)
    if spec is bool:
        return isinstance(v, bool)
    if spec is int:
        return isinstance(v, int) and not isinstance(v, bool)
    if spec is float:
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    return isinstance(v, spec)


def _check_obj(prefix, obj, data, errors):
    if not isinstance(data, dict):
        errors.append("%s 应是对象，实为 %r" % (prefix, type(data).__name__))
        return
    for key, spec in obj.required.items():
        if key not in data:
            errors.append("%s 缺必填键 %r" % (prefix, key))
        else:
            _check_value(prefix + "." + key, spec, data[key], errors)
    for key, spec in obj.optional.items():
        if key in data:
            _check_value(prefix + "." + key, spec, data[key], errors)


def _check_value(path, spec, v, errors):
    if isinstance(spec, Obj):
        _check_obj(path, spec, v, errors)
        return
    if isinstance(spec, Nullable) and isinstance(spec.spec, Obj):
        if v is not None:
            _check_obj(path, spec.spec, v, errors)
        return
    if isinstance(spec, Arr):
        if not isinstance(v, list):
            errors.append("%s 应是数组，实为 %r" % (path, type(v).__name__))
            return
        for i, item in enumerate(v):
            _check_value("%s[%d]" % (path, i), spec.items, item, errors)
        return
    if not _type_ok(spec, v):
        errors.append("%s 类型/枚举域不符：期望 %r，实为 %r" % (path, spec, v))


# ---- 契约表（键为命令名；hc 侧与 app 侧分命名空间） ----

_STATUS = Obj(
    required={"state": Enum("idle", "working", "done", "blocked", "error")},
    optional={"app": str, "kind": Nullable(Enum("permission", "question", "auth")),
              "msg": str, "title": str, "progress": int},
)
_FOREGROUND = Obj(required={"name": str, "argv": list},
                  optional={"cwd": str, "pid": int})
# Terminal（hc get / run / label / kill 共用；schema 必填键）
_TERMINAL = Obj(
    required={"address": str, "labels": dict, "argv": list, "cwd": str,
              "state": Enum("live", "finished"), "created_ms": int,
              "screen": Enum("exact", "inexact"), "cols": int, "rows": int},
    # turn 容 bool 是假世界的「turn 非整数」防御场景表达（实测 turn 恒整数，
    # 与 ls 的 id 可空同一先例）；turn 缺失场景由假世界直接不返回该键表达。
    optional={"name": Nullable(str), "title": str, "turn": AnyOf(int, bool),
              "status": Nullable(_STATUS), "foreground": Nullable(_FOREGROUND),
              "exit_code": Nullable(int),
              "reason": Nullable(Enum("exited", "killed", "server_stop",
                                      "host_shutdown", "signaled", "holder_lost",
                                      "stopped_by_restart", "spawn_pending")),
              "finished_ms": Nullable(int), "signal": Nullable(str),
              "status_ms": Nullable(int), "pid": int, "parent": Nullable(str),
              "keep": bool, "notify": bool, "rm": bool, "reported": bool,
              "identity": str, "template": Nullable(str), "die_with_parent": bool,
              "records": dict},
)

CONTRACTS = {
    "hc.get": _TERMINAL,
    "hc.run": _TERMINAL,
    "hc.label": _TERMINAL,
    "hc.kill": _TERMINAL,
    # SendResult：必填仅 turn（schema；started/status/wait 可选）
    "hc.send": Obj(required={"turn": int},
                   optional={"started": Nullable(bool), "status": Nullable(str),
                             "wait": Nullable(dict)}),
    # WaitResult：必填 terminal/ended/reason/turn/children（schema）
    "hc.wait": Obj(
        required={"terminal": str, "ended": bool,
                  "reason": Enum("status", "exit", "output", "timeout",
                                 "not_started", "children", "message", "running"),
                  "turn": int, "children": list},
        optional={"status": Nullable(str), "exit_code": Nullable(int),
                  "signal": Nullable(str)}),
    # ReadResult：how ∈ screen/history/stitched；why ∈ working/blocked/error/null
    "hc.read": Obj(required={"text": str, "complete": bool,
                             "how": Enum("screen", "history", "stitched")},
                   optional={"why": Nullable(Enum("working", "blocked", "error"))}),
    # RemoveResult
    "hc.rm": Obj(required={"removed": str}),
    # herdr-app status：dispatch 只读 space；其余键出现才核对（实测形状）
    "app.status": Obj(
        required={"space": Nullable(str)},
        optional={"tab": Nullable(str), "focused_pane": Nullable(str),
                  "agent": Nullable(str), "layout": str, "zoom": float,
                  "font_size": float, "ui_font_size": float,
                  "floats": dict, "overlays": list, "errors": list}),
    # herdr-app ls：空间→标签页→终端三层树。id 可空是假世界的「缺 id」场景
    # 表达（实测 id 恒在）；pane:null 语义未实测（ADR-0018 决定 13a）。
    "app.ls": Obj(required={
        "spaces": Arr(Obj(
            required={"id": Nullable(str), "name": Nullable(str),
                      "folder": Nullable(str),
                      "tabs": Arr(Obj(
                          required={"id": Nullable(str), "name": Nullable(str),
                                    "terminals": Arr(Obj(
                                        required={"terminal": str,
                                                  "pane": Nullable(str)},
                                        optional={"status": Nullable(str),
                                                  "app": Nullable(str),
                                                  "name": Nullable(str),
                                                  "sub_agents": list}))},
                          optional={"active": bool, "settled": bool}))},
            optional={"machine": Nullable(str), "active": bool})),
        "recent": list}),
    # do 返回（假设形状，fixtures/README 显式标注）：dispatch 只依赖 space.new 的 space
    "app.do.space.new": Obj(required={"space": str},
                            optional={"tab": str, "terminal": str}),
    # keep 返回是假设形状（无文档）：fake 自行声明的 {pane, terminal} 按必填核对，
    # 抄错键名当场失败；真实契约以 HITL 采样为准（README 标注）。
    "app.do.terminal.keep": Obj(required={"pane": str, "terminal": str}),
    "app.do.space.select": Obj(),
    "app.do.tab.close": Obj(),
    "app.do.tab.rename": Obj(),
}


def check(command, data):
    """核对一条命令的返回；不符抛 ContractError（一次列全所有问题）。"""
    if command not in CONTRACTS:
        raise ContractError("未知命令契约键：%r" % command)
    errors = []
    _check_obj(command, CONTRACTS[command], data, errors)
    if errors:
        raise ContractError("%s 返回形状与契约不符：\n  %s" % (command, "\n  ".join(errors)))