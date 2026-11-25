"""harness.py — 唯一 harness（T3，ADR-0016 决定 9）。

唯一知道 loop/scripts 位置的地方：加载时放进 sys.path 并导出 dispatch。
run(...) 只做一件事：装配依赖对象、调用分发脚本入口、把返回码与产物整理成
测试可断言的东西。不解释业务含义。

全局副作用纪律（ADR-0016 决定 7）：时钟、环境变量、工具定位全走 Deps 注入，
不写 os.environ。残留洞——reset_settle 与 resolve_space_id_for_learning 仍读
模块级 hc_json/app_json/time，check_structure_and_tools 仍读模块级 find_tool
（收口归管线片 P2）——在作用域内替换、退出无条件恢复，不留共享污染。
"""
import contextlib
import io
import json
import sys
import types
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "loop" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
import dispatch  # noqa: E402,F401  （导出给测试文件）

# 测试隔离默认：既有套件走 pi 后端 mock extract、学习关闭（不碰真实学习目录）。
# 调用方可用 env 覆盖/追加；这是装配默认，不是业务解释。
DEFAULT_ENV = {"LOOP_EXTRACT_BACKEND": "pi", "LOOP_LEARNING": "off"}

# 默认抽取桩：固定六字段形状（reply 空）。断言回复内容的测试自带 extract。
_DEFAULT_EXT = {"needs_user": False, "questions": [], "conclusion": "",
                "blocked": "", "reply": "", "next_skill": ""}


class VirtualClock:
    """虚拟时钟：sleep 只推进虚拟时间，不真睡（ADR-0016 决定 3）。"""

    def __init__(self):
        self.t = 0.0

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.t += s


def _argv_value(argv, flag):
    """机械取 argv 里某 flag 的值（产物路径定位用，不解释业务）。"""
    argv = list(argv)
    if flag in argv:
        i = argv.index(flag)
        if i + 1 < len(argv):
            return argv[i + 1]
    return None


def run(argv, *, world, env=None, extract=None, locate=None, http_open=None,
        run_cmd=None, clock=None):
    """装配 Deps、跑一轮 dispatch.main、整理返回码与产物。

    world：FakeHerdr（两个命令层入口填进 Deps 的 hc_json/app_json 字段）。
    env：隔离环境变量映射（合并 DEFAULT_ENV，不写 os.environ）。
    extract/locate/http_open/run_cmd：可选依赖覆盖；clock 缺省虚拟时钟。
    返回 SimpleNamespace：rc、stdout、stderr、result（stdout JSON）、
    ledger/notify（落盘 JSON，存在才读）、各产物路径与 loop_dir。
    """
    clock = clock if clock is not None else VirtualClock()
    env_map = dict(DEFAULT_ENV)
    env_map.update(env or {})
    locate = locate if locate is not None else (
        lambda name, probe_dirs=None: "/fake/" + name)
    extract = extract if extract is not None else (
        lambda pi_bin, args, turn_path: dict(_DEFAULT_EXT))
    deps = dispatch.Deps(hc_json=world.hc_json, app_json=world.app_json,
                         monotonic=clock.monotonic, sleep=clock.sleep,
                         env=env_map, locate=locate, http_open=http_open,
                         run_cmd=run_cmd, extract=extract)
    # 残留全局洞的作用域内覆盖（模块 docstring 所列），退出无条件恢复。
    # 临时产物：这四行 dispatch.<名> = 赋值随 P2 收口残留洞后删除；T4 立测试侧
    # 棘轮时本文件须进白名单（ADR-0021 决定 4），白名单只许缩小。
    saved = (dispatch.hc_json, dispatch.app_json, dispatch.time, dispatch.find_tool)
    dispatch.hc_json = world.hc_json
    dispatch.app_json = world.app_json
    dispatch.time = types.SimpleNamespace(monotonic=clock.monotonic, sleep=clock.sleep)
    dispatch.find_tool = locate
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = dispatch.main(list(argv), deps=deps)
    finally:
        (dispatch.hc_json, dispatch.app_json,
         dispatch.time, dispatch.find_tool) = saved
    stdout = out.getvalue()
    result = json.loads(stdout.strip()) if stdout.strip() else None
    loop_dir = _argv_value(argv, "--loop-dir")
    rid = _argv_value(argv, "--release-id")
    runs = Path(loop_dir) / "runs" if loop_dir and rid else None
    ledger_path = runs / (rid + ".json") if runs else None
    result_path = runs / (rid + ".result.json") if runs else None
    notify_path = runs / (rid + ".notify.json") if runs else None
    raw_path = runs / (rid + ".raw.md") if runs else None

    def read_json(p):
        if p is not None and p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
        return None

    return types.SimpleNamespace(
        rc=rc, stdout=stdout, stderr=err.getvalue(), result=result,
        ledger=read_json(ledger_path), notify=read_json(notify_path),
        ledger_path=ledger_path, result_path=result_path,
        notify_path=notify_path, raw_path=raw_path, loop_dir=loop_dir)