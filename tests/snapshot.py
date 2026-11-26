#!/usr/bin/env python3
"""snapshot.py — S 片特征化快照的测试侧设施（ADR-0021 决定 2/5，ADR-0022 决定 5）。

只做三件事，不解释业务含义：
  1. SnapshotWorld：包装 FakeHerdr 的两个命令层入口；每收到一条命令，先读一次磁盘
     台账再委派——照出「先记账后发送」的时序（ADR-0022 决定 5）。不给 Deps 加字段，
     不改分发脚本行为；harness 只取 world.hc_json/app_json 两属性，包装对象合法。
  2. 归一化：把临时目录路径洗成 <TMP>、ISO 时间戳洗成 <TS>，让快照跨机器稳定，
     同时不洗掉任何业务字段（stdout/result/notify/台账四面仍是逐字契约）。
  3. 金样读写与逐字比对：金样缺失或漂移都报断言失败；只有显式 --update 才写盘，
     CI 永不自作主张「更新基线」。

快照结构（每个场景一份 tests/snapshots/<name>.json）：
  rc             退出码
  stdout         归一化后的 stdout 原文（一行 JSON）
  result_file    归一化后的 .result.json 原文（与 stdout 逐字一致）
  notify_file    归一化后的 .notify.json 原文
  ledger_final   归一化后的终态台账（解析后 JSON）
  phase_sequence 命令收口处观察到的 phase 转移（去连续重复）＋终态 phase
  commands       逐命令观察：command / phase（收到命令时盘上 phase）/ changes
                 （相对上一条命令的台账增量；台账未开时 phase 与 changes 均为 null）

纯模块纪律：不赋值 dispatch 全局、不写 os.environ、不直接 import dispatch
（经 harness→fake_herdr 的传递导入），测试侧棘轮天然干净。
"""
import difflib
import json
import re
import sys
from pathlib import Path

import harness
from helpers import PROMPT as _PROMPT

SNAPSHOTS = Path(__file__).resolve().parent / "snapshots"

# now_iso()/learning.now_iso() 的秒精度 UTC ISO；只洗时间值，不动字段名。
_TS_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?")

_MISSING = object()


def normalize_text(text, tmp_dir):
    """路径与时间戳占位符化：临时目录 → <TMP>，ISO 时间戳 → <TS>。
    只动这两类非契约值；字段名、枚举、消息文案一律逐字保留。"""
    if text is None:
        return None
    text = str(text).replace(str(tmp_dir), "<TMP>")
    return _TS_RE.sub("<TS>", text)


def normalize_obj(obj, tmp_dir):
    """递归归一化解析后的 JSON 结构（字符串走 normalize_text）。"""
    if isinstance(obj, str):
        return normalize_text(obj, tmp_dir)
    if isinstance(obj, list):
        return [normalize_obj(v, tmp_dir) for v in obj]
    if isinstance(obj, dict):
        return {k: normalize_obj(v, tmp_dir) for k, v in obj.items()}
    return obj


def argv(td, rid, *, target="t99", prompt=_PROMPT,
         space="main", extra=(), wall_clock="5"):
    """装配基础 argv（与 helpers.argv 同形，多 target/prompt/space/wall_clock 旋钮）。
    prompt 由调用方给，便于 split/reset 等文本闸场景；文件落在 td 内。"""
    pf = Path(td) / "prompt.md"
    pf.write_text(prompt, encoding="utf-8")
    return ["--target", target, "--release-id", rid,
            "--prompt-file", str(pf), "--loop-dir", str(Path(td) / ".loop"),
            "--space", space, "--wall-clock", wall_clock, "--check-in", "1",
            "--settle-seconds", "0.01", "--settle-checks", "2",
            "--adopt-timeout", "0.05", "--adopt-poll", "0.001", *extra]


class SnapshotWorld:
    """世界组合：包装 FakeHerdr，在每条命令到达时读一次磁盘台账并记录增量。

    screen_after_run：hc run 成功返回后给新终端摆稳定屏幕（创建/重建路径的
    回读与既有路径同形，快照才只反映分流差异而非屏幕偶差）。
    wait_reason：把 hc wait 的 reason 改写成给定值（构造 wait_for_result 的
    check-in 兜底分支；契约自检仍在委派内完成）。
    """

    def __init__(self, world, ledger_path, tmp_dir, *,
                 screen_after_run=None, wait_reason=None):
        self._world = world
        self._ledger_path = Path(ledger_path)
        self._tmp = tmp_dir
        self._screen_after_run = screen_after_run
        self._wait_reason = wait_reason
        self._prev = None
        self.trace = []

    # ---- 命令层入口（与 Deps 的两个字段一一对应） ----

    def hc_json(self, hc_bin, args, timeout_s):
        self._tap("hc " + args[0])
        ret = self._world.hc_json(hc_bin, args, timeout_s)
        if args[0] == "run" and self._screen_after_run is not None:
            addr = ret.get("address")
            if addr:
                self._world.screen(addr, self._screen_after_run)
        if args[0] == "wait" and self._wait_reason is not None:
            ret = dict(ret, reason=self._wait_reason)
        return ret

    def app_json(self, app_bin, args, timeout_s):
        name = args[0] if args[0] != "do" else "do " + args[1]
        self._tap("app " + name)
        return self._world.app_json(app_bin, args, timeout_s)

    # ---- 逐命令台账观察 ----

    def _read_ledger(self):
        if not self._ledger_path.is_file():
            return None
        return normalize_obj(
            json.loads(self._ledger_path.read_text(encoding="utf-8")), self._tmp)

    def _tap(self, command):
        cur = self._read_ledger()
        if cur is None:
            self.trace.append({"command": command, "phase": None, "changes": None})
            return
        prev = self._prev if self._prev is not None else {}
        changes = {k: v for k, v in cur.items()
                   if prev.get(k, _MISSING) is _MISSING or prev.get(k) != v}
        self._prev = cur
        self.trace.append({"command": command, "phase": cur.get("phase"),
                           "changes": changes})


def _phase_sequence(trace, final_ledger):
    """命令收口处观察到的 phase 转移（去连续重复）＋终态 phase。
    read/extracting 等不邻近命令的阶段不在此列——它们由台账终态与 Journal
    单测覆盖；这里钉的是「先记账后发送」可观察到的次序。"""
    seq = []
    for entry in trace:
        phase = entry.get("phase")
        if phase is not None and (not seq or seq[-1] != phase):
            seq.append(phase)
    final_phase = (final_ledger or {}).get("phase")
    if final_phase is not None and (not seq or seq[-1] != final_phase):
        seq.append(final_phase)
    return seq


def _read_text(path):
    return path.read_text(encoding="utf-8") if path is not None and path.is_file() else None


def _build(name, result, world, tmp_dir):
    return {
        "scenario": name,
        "rc": result.rc,
        "stdout": normalize_text(result.stdout, tmp_dir),
        "result_file": normalize_text(_read_text(result.result_path), tmp_dir),
        "notify_file": normalize_text(_read_text(result.notify_path), tmp_dir),
        "ledger_final": normalize_obj(result.ledger, tmp_dir),
        "phase_sequence": _phase_sequence(world.trace, result.ledger),
        "commands": world.trace,
    }


def assert_snapshot(name, actual, update=False):
    """与金样逐字比对；update=True 才写盘。漂移时给 unified diff 摘要。"""
    path = SNAPSHOTS / (name + ".json")
    text = json.dumps(actual, ensure_ascii=False, indent=1) + "\n"
    if update:
        SNAPSHOTS.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return
    if not path.is_file():
        raise AssertionError(
            "快照缺失：tests/snapshots/%s.json（生成用 --update）" % name)
    expected = path.read_text(encoding="utf-8")
    if text == expected:
        return
    diff = difflib.unified_diff(
        expected.splitlines(), text.splitlines(),
        fromfile="golden/" + path.name, tofile="actual/" + path.name, lineterm="")
    raise AssertionError("快照漂移：%s\n%s"
                         % (path.name, "\n".join(list(diff)[:100])))


def run_case(name, world, tmp_dir, argv_list, *, update=False,
             screen_after_run=None, wait_reason=None, **harness_kwargs):
    """包装世界 → 跑一轮 → 组装快照 → 比对。返回 harness 结果供场景断言。"""
    argv_list = list(argv_list)
    ledger_path = (Path(argv_list[argv_list.index("--loop-dir") + 1]) / "runs"
                   / (argv_list[argv_list.index("--release-id") + 1] + ".json"))
    world = SnapshotWorld(world, ledger_path, str(tmp_dir),
                          screen_after_run=screen_after_run,
                          wait_reason=wait_reason)
    result = harness.run(argv_list, world=world, **harness_kwargs)
    assert_snapshot(name, _build(name, result, world, str(tmp_dir)), update)
    result.trace = world.trace   # 场景可做少量金样之外的补充断言
    return result


def update_requested():
    return "--update" in sys.argv[1:]
