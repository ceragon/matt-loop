#!/usr/bin/env python3
"""test_dispatch_io_failed.py — 中途本地 IO 失败的交付测试（ADR-0024，#111，spec #87）。

跑法：python3 tests/test_dispatch_io_failed.py
唯一业务缝 harness.run（CLI 契约）；故障注入按 spec Testing Decisions 逐点：
  - 静态：预把 runs 建成 regular file（mkdir exist_ok 只容忍目录；exists() 对
    ENOTDIR 返回 False，不撞 duplicate 闸；root-safe）。
  - 世界组合：包装 FakeHerdr 的两个命令层入口，在指定命令完成后破坏磁盘
    （harness 只取 world.hc_json/app_json 两属性，传包装对象合法；不是 monkeypatch）。
  - Deps.extract 注入：回调里破坏干净回复路径／本轮原料路径（api 专项先例：
    真实抽取入口作 extract 传入）。
  - 作用域 Path 读探针：抽取提示词读失败（通知降级测试既有先例，mock.patch.object
    退出无条件恢复，棘轮合法——不赋值 dispatch 全局、不写 os.environ）。
断言只认外部行为：rc、stdout 一行 JSON、result/notify 落盘产物、POST 字节与次数、
假世界 sends/hc_calls 日志；不断言内部调用序。既有全套测试零改动零失败是
「成功路径与既有 error 路径行为不变」的特征化证明。
"""
import json
import shutil
import tempfile
from pathlib import Path
from unittest import mock

import harness
from harness import dispatch
from helpers import PROMPT, SCREEN, EXT, world, argv, api_env, extraction

URL = "https://example.invalid/hook"
SECRET = "s3cret-token"


class FakeResp:
    """http_open 桩响应（notify 套件 b"ok" 形状）。"""

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return b"ok"


class BreakingWorld:
    """世界组合：包装 FakeHerdr，某条 hc 命令首次完成后执行破坏回调。
    harness 只取 hc_json/app_json 两个属性，包装对象合法（spec 测试缝表）。"""

    def __init__(self, w, command, break_fn):
        self._w = w
        self._command = command
        self._break = break_fn
        self._broken = False

    def hc_json(self, hc_bin, args, timeout_s):
        ret = self._w.hc_json(hc_bin, args, timeout_s)
        if not self._broken and args[0] == self._command:
            self._broken = True
            self._break()
        return ret

    def app_json(self, app_bin, args, timeout_s):
        return self._w.app_json(app_bin, args, timeout_s)


def run_round(rid, w, *, setup=None, extract=None, env=None):
    """经唯一 harness.run 跑一轮（webhook 恒开、记录 POST 字节），读产物。
    setup(td) 在跑前布置静态破坏；holder 需要时经 setup 闭包捕获路径。"""
    td = tempfile.mkdtemp()
    try:
        a = argv(td, rid)
        if setup is not None:
            setup(Path(td))
        posted = []

        def opener(req, timeout, proxies):
            posted.append(req.data)
            return FakeResp()

        r = harness.run(
            a, world=w,
            env={"LOOP_WEBHOOK_URL": URL, "LOOP_WEBHOOK_SECRET": SECRET,
                 **(env or {})},
            extract=extract, http_open=opener,
            proxy_resolution=lambda url: ({}, "none"))
        result_text = (r.result_path.read_text(encoding="utf-8")
                       if r.result_path.is_file() else None)
        notify_bytes = (r.notify_path.read_bytes()
                        if r.notify_path.is_file() else None)
        runs = Path(td) / ".loop" / "runs"
        files = sorted(p.name for p in runs.iterdir()) if runs.is_dir() else []
        return {"rc": r.rc, "res": r.result, "stdout": r.stdout, "err": r.stderr,
                "ledger": r.ledger, "result_text": result_text,
                "notify_bytes": notify_bytes, "posted": posted,
                "runs": runs, "files": files}
    finally:
        shutil.rmtree(td, ignore_errors=True)


def assert_delivered(o, kind, *, opened=None):
    """每例共同断言：rc=1、stdout 一行 JSON 交付、webhook 照尝试、
    通知记录＝发送前完整 error 对象（不套成功白名单）、io_failed 两态 hints。"""
    assert o["rc"] == 1, o["rc"]
    res = o["res"]
    assert res is not None and res["ok"] is False, res
    assert res["error"]["kind"] == kind, res
    assert len(o["stdout"].strip().splitlines()) == 1, "stdout 须一行 JSON"
    assert json.loads(o["stdout"]) == res
    # 唤醒优先：webhook 照尝试，且不递归第二条 error 通知
    assert len(o["posted"]) == 1, o["posted"]
    body = json.loads(o["posted"][0].decode("utf-8"))
    assert body["error"]["kind"] == kind, body
    if o["notify_bytes"] is not None:
        assert o["posted"][0] == o["notify_bytes"], "POST 字节须与通知记录相同"
        expect = {k: v for k, v in res.items()
                  if k not in ("webhook", "webhook_error", "webhook_error_detail")}
        assert body == expect, (body, expect)
    if o["result_text"] is not None:
        assert o["result_text"].strip() == o["stdout"].strip(), "结果文件与 stdout 逐字一致"
    if kind == "io_failed":
        hints = res["error"]["hints"]
        assert isinstance(hints, list) and hints, hints
        if opened is False:
            assert any("未消耗" in h and "同一放行 id" in h for h in hints), hints
            assert not any("已消耗" in h for h in hints), hints
        elif opened is True:
            assert any("已消耗" in h and "新" in h for h in hints), hints
            assert not any("未消耗" in h for h in hints), hints


def main():
    # 1 runs 目录创建失败（静态：runs 预建成 regular file）→ io_failed 未开态。
    #   台账未开：零发送、id 未消耗；result/notify 写失败各自降级、webhook 照尝试。
    w = world()

    def setup_runs_file(td):
        runs = td / ".loop" / "runs"
        runs.parent.mkdir(parents=True)
        runs.write_text("not a directory", encoding="utf-8")

    o = run_round("io-runs-1", w, setup=setup_runs_file)
    assert_delivered(o, "io_failed", opened=False)
    assert w.sends == [], "runs 创建失败须零发送"
    msg = o["res"]["error"]["message"]
    assert "runs" in msg and "FileExistsError" in msg, msg   # 操作＋路径＋OSError 原文
    assert o["res"]["notify_record_error"] == "FileExistsError", o["res"]
    assert o["result_text"] is None and o["notify_bytes"] is None
    assert "通知记录写入失败" in o["err"] and "结果文件写入失败" in o["err"], o["err"]
    assert o["ledger"] is None, "台账未开"
    print("1 runs mkdir failure: io_failed delivered, unopened hints, degradation ok")

    # 2 台账 open 写失败（世界组合：首条 get 后把台账路径建成目录 → 原子替换抛
    #   IsADirectoryError）。注入在 duplicate 闸之后（exists() 对目录为真、先于 open，
    #   静态预建会先撞 duplicate_release——spec 事实修正②）。首写失败＝台账未开态。
    w = world()
    holder = {}

    def setup_open(td):
        holder["runs"] = td / ".loop" / "runs"

    def break_open():
        runs = holder["runs"]
        runs.mkdir(parents=True, exist_ok=True)
        (runs / "io-open-1.json").mkdir()

    o = run_round("io-open-1", BreakingWorld(w, "get", break_open), setup=setup_open)
    assert_delivered(o, "io_failed", opened=False)
    assert w.sends == [], "首写失败须零发送"
    assert o["ledger"] is None, "台账路径是目录，无可读台账"
    assert o["result_text"] is not None and o["notify_bytes"] is not None
    assert "IsADirectoryError" in o["res"]["error"]["message"], o["res"]
    print("2 ledger open write failure: io_failed delivered, unopened hints ok")

    # 3 中途台账写失败（世界组合：主任务 send 后把台账换成目录）→ 已开态：
    #   id 已消耗、修复后新放行 id；部分状态（target_address/elapsed_s）机械带出；
    #   只停本轮——主任务恰好发一次、不 kill 不重发（US15）。
    w = world()
    holder = {}

    def setup_mid(td):
        holder["runs"] = td / ".loop" / "runs"

    def break_mid():
        lp = holder["runs"] / "io-mid-1.json"
        lp.unlink()
        lp.mkdir()

    o = run_round("io-mid-1", BreakingWorld(w, "send", break_mid), setup=setup_mid)
    assert_delivered(o, "io_failed", opened=True)
    assert w.sends == [("t99", PROMPT, True)], "主任务恰好发一次，不重发"
    assert not any(c[0] == "kill" for c in w.hc_calls), "IO 失败不杀目标会话"
    res = o["res"]
    assert res["target_address"] == "t99", res
    assert "elapsed_s" in res["error"], res
    assert o["ledger"] is None and o["result_text"] is not None
    print("3 mid-run ledger write failure: opened hints, partial state, no kill/resend ok")

    # 4 干净回复落盘失败（Deps.extract 注入：回调里把回复路径建成目录再返回默认
    #   抽取）→ 已开态；error 出口的部分状态机械免费继承：run_record（回读原料已
    #   落盘）与完整性标记（US7/US16）。
    w = world()
    holder = {}

    def setup_reply(td):
        holder["runs"] = td / ".loop" / "runs"

    def extract_break_reply(deps, request, turn_path, injection):
        (holder["runs"] / "io-reply-1.md").mkdir()
        return extraction(EXT)

    o = run_round("io-reply-1", w, setup=setup_reply, extract=extract_break_reply)
    assert_delivered(o, "io_failed", opened=True)
    res = o["res"]
    assert res["error"]["run_record"] == str(holder["runs"] / "io-reply-1.raw.md"), res
    assert res["complete"] is True and res["complete_reason"] == [], res
    assert "io-reply-1.raw.md" in o["files"], "回读原料已落盘"
    assert "io-reply-1.turn.md" in o["files"], "本轮原料已落盘"
    assert "IsADirectoryError" in res["error"]["message"], res
    print("4 clean reply write failure: opened hints, run_record + complete inherited ok")

    # 5 发送前 sha 复核读提示词失败（世界组合：首条 get 后删提示词文件）→
    #   既有 prompt_unreadable 语义（不新增同义 kind，US9）；零发送、id 未消耗。
    w = world()
    holder = {}

    def setup_sha(td):
        holder["pf"] = td / "prompt.md"

    def break_sha():
        holder["pf"].unlink()

    o = run_round("io-sha-1", BreakingWorld(w, "get", break_sha), setup=setup_sha)
    assert_delivered(o, "prompt_unreadable")
    assert o["res"]["error"]["hints"] == [], "既有语义不变"
    assert str(holder["pf"]) in o["res"]["error"]["message"], o["res"]
    assert w.sends == [], "sha 复核失败须零发送"
    assert o["result_text"] is not None and o["notify_bytes"] is not None
    print("5 sha recheck read failure: existing prompt_unreadable semantics ok")

    # 6 api 后端读本轮原料失败（Deps.extract 包装：把本轮原料路径换成目录再委托
    #   真实抽取入口——api 专项先例）→ io_failed 已开态（US11 同形盲区）。
    w = world()

    def extract_break_turn(deps, request, turn_path, injection):
        tp = Path(turn_path)
        tp.unlink()
        tp.mkdir()
        return dispatch.extract(deps, request, turn_path, injection)

    o = run_round("io-turn-1", w, env=api_env(), extract=extract_break_turn)
    assert_delivered(o, "io_failed", opened=True)
    res = o["res"]
    assert res["extract_backend"] == "api", res
    assert "IsADirectoryError" in res["error"]["message"], res
    assert "io-turn-1.turn.md" in res["error"]["message"], res
    assert w.sends == [("t99", PROMPT, True)], w.sends
    print("6 api turn-material read failure: io_failed opened hints ok")

    # 7 抽取提示词读失败（作用域 Path 读探针＋退出无条件恢复——通知降级测试既有
    #   先例，棘轮合法；路径固定在仓库内、无法静态破坏）→ 既有 missing_prompt
    #   语义（US10）。真实 api 抽取入口：is_file 检查通过、read_text 中途失败。
    w = world()
    extract_md = Path(dispatch.__file__).resolve().parent.parent / "prompts" / "extract.md"
    real_read_text = Path.read_text

    def fail_extract_read(self, *a, **k):
        if self == extract_md:
            raise OSError("extract prompt disk gone")
        return real_read_text(self, *a, **k)

    with mock.patch.object(Path, "read_text", fail_extract_read):
        o = run_round("io-ext-1", w, env=api_env(), extract=dispatch.extract)
    assert_delivered(o, "missing_prompt")
    assert o["res"]["error"]["hints"] == [], "既有语义不变"
    assert "extract.md" in o["res"]["error"]["message"], o["res"]
    assert w.sends == [("t99", PROMPT, True)], w.sends
    assert o["ledger"]["phase"] == "error:missing_prompt", "终态台账尽力写入"
    print("7 extract prompt read failure: existing missing_prompt semantics ok")

    print("all io_failed delivery tests passed")


if __name__ == "__main__":
    main()
