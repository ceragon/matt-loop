"""harness.py — 唯一 harness（T3，ADR-0016 决定 9）。

唯一知道 loop/scripts 位置的地方：加载时放进 sys.path 并导出 dispatch。
run(...) 只做一件事：装配依赖对象、调用分发脚本入口、把返回码与产物整理成
测试可断言的东西。不解释业务含义。

全局副作用纪律（ADR-0016 决定 7）：时钟、环境变量、工具定位、命令层、webhook
出口与代理决议全走 Deps 注入；P2 已把残留的模块全局替换（hc_json/app_json/time/
find_tool/webhook_open/run_cmd/webhook_proxy_resolution）全部收口，harness 不再
赋值任何 dispatch 全局。仅剩一条随 ⑤/P3 退役的作用域 env：
  - learning_dir 仍直读 os.environ（learning.enabled 已改为 deps.env），
    LearningStore 的目录解析因此仍需把合并后的 env 映射经 scoped_env 作用域内
    应用到 os.environ（设置＋退出恢复原值、原本不存在的键退出删除）。这是
    ADR-0016 决定 7 学习环境洞例外的最后一段，随 ⑤ 接线收口后删除，终态片兜底。
"""
import contextlib
import io
import json
import os
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


@contextlib.contextmanager
def scoped_env(env_map):
    """作用域 env（T5a 自 run() 抽出，单元探针共用）：作用域内把 env_map 应用到
    os.environ，退出无条件恢复——原本存在的键恢复原值、原本不存在的键删除。
    值为 None 表示作用域内确保该键不存在（单元探针的「无 guard」场景）。
    残留洞纪律（ADR-0016 决定 7）：作用域内替换、退出恢复，不留共享污染。
    P2 后仅剩 learning_dir 直读 os.environ（learning.enabled 已走 deps.env）；
    随 ⑤/P3 收口后本函数与 run() 里的作用域一起删除。"""
    saved = {k: os.environ.get(k) for k in env_map}
    try:
        for k, v in env_map.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, old in saved.items():
            if old is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = old


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
    env：隔离环境变量映射（合并 DEFAULT_ENV，作用域内应用到 os.environ、退出恢复原样）。
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
    # 仅剩 learning_dir 的 os.environ 直读（⑤/P3 收口）；Deps 注入的其余依赖不再
    # 触碰任何模块全局。
    with scoped_env(env_map):
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
