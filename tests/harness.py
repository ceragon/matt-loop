"""harness.py — 唯一 harness（T3，ADR-0016 决定 9）。

唯一知道 loop/scripts 位置的地方：加载时放进 sys.path 并导出 dispatch。
run(...) 只做一件事：装配依赖对象、调用分发脚本入口、把返回码与产物整理成
测试可断言的东西。不解释业务含义。

全局副作用纪律（ADR-0016 决定 7）：时钟、环境变量、工具定位、命令层、webhook
出口与代理决议全走 Deps 注入；P2 已把残留的模块全局替换（hc_json/app_json/time/
find_tool/webhook_open/run_cmd/webhook_proxy_resolution）全部收口，harness 不再
赋值任何 dispatch 全局。P3/⑤ 把学习的环境洞也收口：`learning.enabled`、
`learning_dir` 与学习事件时间戳全部走 `deps.env` / `deps.now`，harness 因此不再
作用域内改写 os.environ（ADR-0016 决定 7 的最后一段已删除）。
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
import pipeline  # noqa: E402,F401  （导出给测试文件）

# 测试隔离默认：既有套件走 pi 后端 mock extract、学习关闭（不碰真实学习目录）。
# 调用方可用 env 覆盖/追加；这是装配默认，不是业务解释。
DEFAULT_ENV = {"LOOP_EXTRACT_BACKEND": "pi", "LOOP_LEARNING": "off"}

# 默认抽取桩：固定六字段形状（reply 空），按 Deps.extract 契约返回 Extraction。
_DEFAULT_EXT = {"needs_user": False, "questions": [], "conclusion": "",
                "blocked": "", "reply": "", "next_skill": ""}
_DEFAULT_FIELDS = {k: v for k, v in _DEFAULT_EXT.items() if k != "reply"}


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
        run_cmd=None, clock=None, proxy_resolution=None):
    """装配 Deps、跑一轮 dispatch.main、整理返回码与产物。

    world：FakeHerdr（两个命令层入口填进 Deps 的 hc_json/app_json 字段）。
    env：隔离环境变量映射（合并 DEFAULT_ENV 后交给 Deps.env；不再改写 os.environ）。
    extract/locate/http_open/run_cmd/proxy_resolution：可选依赖覆盖；clock 缺省虚拟时钟。
    返 SimpleNamespace：rc、stdout、stderr、result（stdout JSON）、
    ledger/notify（落盘 JSON，存在才读）、各产物路径与 loop_dir。
    """
    clock = clock if clock is not None else VirtualClock()
    env_map = dict(DEFAULT_ENV)
    env_map.update(env or {})
    locate = locate if locate is not None else (
        lambda name, probe_dirs=None: "/fake/" + name)
    extract = extract if extract is not None else (
        lambda deps, request, turn_path, injection: pipeline.Extraction(
            fields=dict(_DEFAULT_FIELDS), reply=_DEFAULT_EXT["reply"],
            next_skill_invalid=False))
    deps = dispatch.Deps(hc_json=world.hc_json, app_json=world.app_json,
                         monotonic=clock.monotonic, sleep=clock.sleep,
                         env=env_map, locate=locate, http_open=http_open,
                         run_cmd=run_cmd, extract=extract,
                         proxy_resolution=proxy_resolution)
    out, err = io.StringIO(), io.StringIO()
    # 依赖全经 Deps 注入（含 env），不触碰任何模块全局或 os.environ。
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = dispatch.main(list(argv), deps=deps)
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
        # is_file 而非 exists：IO 故障注入可能把产物路径变成目录（#111），
        # 不可读即视同不存在，harness 不崩、测试照常断言外部行为。
        if p is not None and p.is_file():
            return json.loads(p.read_text(encoding="utf-8"))
        return None

    return types.SimpleNamespace(
        rc=rc, stdout=stdout, stderr=err.getvalue(), result=result,
        ledger=read_json(ledger_path), notify=read_json(notify_path),
        ledger_path=ledger_path, result_path=result_path,
        notify_path=notify_path, raw_path=raw_path, loop_dir=loop_dir)
