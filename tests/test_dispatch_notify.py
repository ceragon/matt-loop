#!/usr/bin/env python3
"""test_dispatch_notify.py — 双产物通知投影与通知记录测试（ADR-0014，#52 阶段一）。

跑法：python3 tests/test_dispatch_notify.py
主测试缝＝唯一 harness.run（CLI 契约，T5c/ADR-0021）：FakeHerdr 假世界摆场景，
env / extract / http_open / proxy_resolution / clock 注入，断言只认 rc、stdout JSON、
result.json / notify.json 落盘产物与 POST 请求体字节。覆盖规格阶段一测试矩阵 1–11：
  1 webhook 关闭仍生成正确通知投影（13 字段），不 POST。
  2 纯成功与 needs_user=true 的成功通知都严格只有 13 字段。
  3 questions/blocked/conclusion/rewritten/run_record/elapsed_s/next_skill 逐字来自本轮结果。
  4 error 通知保留完整 kind/message/hints 与部分状态，不套成功白名单。
  5 全量结果保留既有字段；webhook/webhook_error/webhook_error_detail 只在全量结果。
  6 首次 POST 前通知记录已存在。
  7 通知文件与所有 POST 尝试的请求体字节完全相同（含重试与直连兜底）。
  8 代理重试与直连兜底遵守现有次数/退避矩阵（3+1、退避 0.5/1.0，经 clock= 记录）。
  9 通知记录写失败仍发送、退出码与 ok 不变、诊断符合契约、不产生第二条 error 通知；
    全量结果也不可写时 stderr/stdout 不声称记录完整。
  10 非法放行 id 等早期 error 不生成不安全通知路径，既有发送与 stdout 行为不回归。
  11 URL/密钥/Authorization/代理地址/敏感异常消息不进通知/结果/stderr。

T5c（ADR-0021，#99）：完整轮用例迁 FakeHerdr＋harness.run。写失败探针（9/9b）保留
对宿主模块 pathlib.Path 的作用域 mock.patch.object，退出无条件恢复（ADR-0016 决定 7
残留洞纪律）。退避记录经既有 clock= 参数（VirtualClock 子类自带 sleeps 日志）。
常量与助手本文件自带，不 import 供应方文件（去重债归 R）。
"""
import json
import shutil
import tempfile
import urllib.error
from pathlib import Path
from unittest import mock

import harness
from harness import dispatch
from fake_herdr import FakeHerdr

PROMPT = "请实现某个功能\n第二行说明"
ANCHOR = PROMPT.splitlines()[0][:24]
SCREEN = ANCHOR + "\n已完成该功能。"

URL = "https://example.invalid/hook"
SECRET = "s3cret-token"
SENTINELS = (URL, "example.invalid", SECRET, "Bearer", "Authorization",
             "reason-with-host-detail", "envproxy.corp", "hunter2", "3128")

# 固定抽取结果：六字段齐全（含 next_skill），成功通知投影据此凑足 13 字段。
# 选段字段须逐字来自屏幕（verify_extraction），故 conclusion 取 SCREEN 内原句。
CONCLUSION = "已完成该功能。"
QUESTION = "要不要批准接口改动？"
SCREEN_Q = SCREEN + "\n" + QUESTION
EXT = {"needs_user": False, "questions": [],
       "conclusion": CONCLUSION, "blocked": "",
       "reply": SCREEN, "next_skill": "to-spec"}
EXT_NEEDS_USER = {"needs_user": True, "questions": [QUESTION],
                  "conclusion": CONCLUSION, "blocked": "",
                  "reply": SCREEN_Q, "next_skill": "implement"}


class FakeResp:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return b"ok"


class RecordingClock(harness.VirtualClock):
    """自带记录时钟：sleep 序列留痕供退避断言，仍只推进虚拟时间。"""

    def __init__(self):
        super().__init__()
        self.sleeps = []

    def sleep(self, s):
        self.sleeps.append(s)
        super().sleep(s)


def _world(question=False):
    """既有 idle agent t99＋main 空间 pane 已在（领养提前返回）＋稳定屏幕。"""
    w = FakeHerdr()
    w.add_terminal(address="t99", name="claude-test", labels={"loop": "1"})
    sp = w.add_space(name="main")
    w.add_tab(sp, terminals=["t99"])
    w.screen("t99", SCREEN_Q if question else SCREEN)
    return w


def assert_clean(*texts):
    for text in texts:
        for bad in SENTINELS:
            assert bad not in text, (bad, text)


def run_round(rid, ext=None, opener=None, env_on=True, extra_argv=(), w=None, holder=None,
              proxy_res=None, clock=None):
    """经唯一 harness.run 跑一轮完整 dispatch，读双产物；返回观察项 dict。
    posted 收集每次 POST 的 (请求体字节, proxies)。notify_bytes 为 None 表示文件不存在。
    holder 给定时填入 ld（loop 目录），供 opener 在 POST 时刻定位通知文件断言落盘顺序。"""
    td = tempfile.mkdtemp()
    try:
        pf = Path(td) / "prompt.md"
        pf.write_text(PROMPT, encoding="utf-8")
        ld = Path(td) / ".loop"
        if holder is not None:
            holder["ld"] = ld
        argv = ["--target", "t99", "--release-id", rid,
                "--prompt-file", str(pf), "--loop-dir", str(ld),
                "--space", "main",
                "--wall-clock", "5", "--check-in", "1",
                "--settle-seconds", "0.01", "--settle-checks", "2", *extra_argv]
        posted = []
        if opener is None:
            def opener(req, timeout, proxies):
                posted.append((req.data, proxies))
                return FakeResp()
        r = harness.run(
            argv, world=w if w is not None else _world(),
            env={"LOOP_WEBHOOK_URL": URL if env_on else None,
                 "LOOP_WEBHOOK_SECRET": SECRET if env_on else None},
            extract=lambda pi_bin, args, raw: dict(ext if ext is not None else EXT),
            http_open=opener,
            proxy_resolution=proxy_res if proxy_res is not None else (lambda url: ({}, "none")),
            clock=clock)
        runs = ld / "runs"
        result_text = r.result_path.read_text(encoding="utf-8") if r.result_path.exists() else None
        notify_bytes = r.notify_path.read_bytes() if r.notify_path.exists() else None
        notify_files = sorted(p.name for p in runs.glob("*.notify.json")) if runs.exists() else []
        return {"rc": r.rc, "res": r.result,
                "stdout": r.stdout, "err": r.stderr,
                "result_text": result_text, "notify_bytes": notify_bytes,
                "notify_files": notify_files, "posted": posted, "ld": ld, "td": td}
    finally:
        shutil.rmtree(td, ignore_errors=True)


def main():
    # 1 webhook 关闭仍生成正确通知投影，不 POST
    o = run_round("nf-off-1", env_on=False)
    assert o["rc"] == 0 and o["res"]["ok"] and o["res"]["webhook"] == "off", o["res"]
    assert o["posted"] == [], o["posted"]
    assert o["notify_bytes"] is not None, "webhook 关闭也须生成通知记录"
    notify = json.loads(o["notify_bytes"].decode("utf-8"))
    assert set(notify) == set(dispatch.NOTIFY_SUCCESS_FIELDS), notify
    assert o["notify_files"] == ["nf-off-1.notify.json"], o["notify_files"]
    print("1 webhook off: notify projection still written, no POST ok")

    # 2 纯成功与 needs_user=true 都严格 13 字段
    for tag, ext, w in (("pure", EXT, None), ("needs", EXT_NEEDS_USER, _world(question=True))):
        o = run_round("nf-11-" + tag, ext=ext, w=w)
        assert o["rc"] == 0 and o["res"]["ok"], o["res"]
        notify = json.loads(o["notify_bytes"].decode("utf-8"))
        assert set(notify) == set(dispatch.NOTIFY_SUCCESS_FIELDS), (tag, notify)
        assert len(notify) == 13, (tag, notify)
        assert notify["needs_user"] is ext["needs_user"], (tag, notify)
    print("2 pure success and needs_user=true: exactly 13 fields ok")

    # 3 判读字段逐字来自本轮结果
    o = run_round("nf-verbatim-1", ext=EXT_NEEDS_USER, w=_world(question=True))
    assert o["rc"] == 0 and o["res"]["ok"], o["res"]
    notify = json.loads(o["notify_bytes"].decode("utf-8"))
    res = o["res"]
    for k in ("ok", "release_id", "target", "needs_user", "questions", "conclusion",
              "blocked", "rewritten", "run_record", "elapsed_s", "next_skill",
              "complete", "complete_reason"):
        assert notify[k] == res[k], (k, notify[k], res[k])
    assert notify["questions"] == [QUESTION], notify
    assert notify["conclusion"] == CONCLUSION, notify
    assert notify["next_skill"] == "implement", notify
    assert notify["rewritten"] == {}, notify
    assert notify["run_record"] == res["run_record"], notify
    print("3 decision fields verbatim from this round's result ok")

    # 4 error 通知保留完整对象与部分状态，不套白名单
    w = _world()
    w.fail("send", "error", kind="status_mismatch", nth=1)   # 第 1 次 send 拒发＝重置前置（旧桩同款 kind）
    o = run_round("nf-err-1", w=w, extra_argv=("--reset", "clear"))
    assert o["rc"] == 1 and not o["res"]["ok"], o["res"]
    notify = json.loads(o["notify_bytes"].decode("utf-8"))
    assert notify["error"]["kind"] and notify["error"]["message"], notify
    assert isinstance(notify["error"]["hints"], list), notify
    assert notify["error"]["reset_sent"] is False, notify   # 前置已发？refuse 第 1 次＝重置前置
    assert "task_name" in notify and "reset" in notify, notify  # 非白名单字段仍在
    assert set(notify) != set(dispatch.NOTIFY_SUCCESS_FIELDS), notify
    # error 通知＝发送前完整对象（去掉 POST 后才加的 webhook 诊断）
    expect = {k: v for k, v in o["res"].items()
              if k not in ("webhook", "webhook_error", "webhook_error_detail")}
    assert notify == expect, (notify, expect)
    print("4 error notification: full kind/message/hints + partial state, no whitelist ok")

    # 5 发送诊断只在全量结果；通知投影不含 webhook 诊断
    def fail_open(req, timeout, proxies):
        o_posted.append((req.data, proxies))
        raise OSError("connection refused")
    o_posted = []
    o = run_round("nf-diag-1", opener=fail_open)
    assert o["rc"] == 0 and o["res"]["ok"] and o["res"]["webhook"] == "failed", o["res"]
    assert o["res"]["webhook_error"] == "OSError", o["res"]
    assert "webhook_error_detail" in o["res"], o["res"]
    notify = json.loads(o["notify_bytes"].decode("utf-8"))
    for k in ("webhook", "webhook_error", "webhook_error_detail"):
        assert k not in notify, notify
        assert k in o["res"], o["res"]
    assert set(notify) == set(dispatch.NOTIFY_SUCCESS_FIELDS), notify
    print("5 send diagnostics only in full result, not in notify projection ok")

    # 6 首次 POST 前通知记录已存在（在 opener 内于 POST 时刻断言落盘先于发送）
    seen = {}
    holder = {}
    def check_exists_open(req, timeout, proxies):
        np_ = holder["ld"] / "runs" / "nf-before-1.notify.json"
        if "first" not in seen:
            seen["first"] = (np_.exists(), np_.read_bytes() == req.data if np_.exists() else False)
        return FakeResp()
    o = run_round("nf-before-1", opener=check_exists_open, holder=holder)
    assert o["rc"] == 0 and o["res"]["webhook"] == "sent", o["res"]
    assert seen["first"] == (True, True), ("首次 POST 时通知记录须已存在且字节相同", seen)
    assert o["notify_bytes"] is not None
    print("6 notify record exists before first POST (asserted at POST time) ok")

    # 7 通知文件与所有 POST 尝试字节完全相同（重试 2 次后成功）
    posted7 = []
    def flaky_open(req, timeout, proxies):
        posted7.append((req.data, proxies))
        if len(posted7) < 3:
            raise OSError("transient")
        return FakeResp()
    o = run_round("nf-retry-1", opener=flaky_open)
    assert o["rc"] == 0 and o["res"]["webhook"] == "sent", o["res"]
    assert len(posted7) == 3, posted7
    assert all(b == o["notify_bytes"] for b, _ in posted7), "重试须复用同一组字节"
    print("7 notify bytes identical across all retry POST attempts ok")

    # 8 代理重试＋直连兜底：3+1 次、退避 0.5/1.0、字节一致、脱敏
    posted8 = []
    clock8 = RecordingClock()
    proxy = {"https": "http://envproxy.corp:8080"}
    def proxy_fail_direct_ok(req, timeout, proxies):
        posted8.append((req.data, proxies))
        if proxies:
            raise urllib.error.URLError("reason-with-host-detail")
        return FakeResp()
    o = run_round("nf-fallback-1", opener=proxy_fail_direct_ok,
                  proxy_res=lambda url: (dict(proxy), "env"),
                  clock=clock8)
    assert o["rc"] == 0 and o["res"]["webhook"] == "sent", o["res"]
    assert [p for _, p in posted8] == [proxy, proxy, proxy, {}], posted8
    assert clock8.sleeps[-2:] == [0.5, 1.0], clock8.sleeps   # 末尾两次＝webhook 退避（前面是落定静置）
    assert all(b == o["notify_bytes"] for b, _ in posted8), "兜底也复用同一组字节"
    assert_clean(o["stdout"], o["err"], o["result_text"], o["notify_bytes"].decode("utf-8"))
    print("8 proxy retry + direct fallback: 3+1 attempts, backoff, byte reuse, redaction ok")

    # 9 通知记录写失败仍发送、ok/退出码不变、诊断符合契约、无第二条 error 通知
    #   写失败探针对宿主模块 pathlib.Path 作用域替换，退出无条件恢复。
    real_write_bytes = Path.write_bytes
    real_write_text = Path.write_text
    def fail_notify_write(self, data):
        if self.name.endswith(".notify.json"):
            raise OSError("notify disk full")
        return real_write_bytes(self, data)
    posted9 = []
    def ok_open9(req, timeout, proxies):
        posted9.append((req.data, proxies))
        return FakeResp()
    with mock.patch.object(Path, "write_bytes", fail_notify_write):
        o = run_round("nf-wfail-1", opener=ok_open9)
    assert o["rc"] == 0 and o["res"]["ok"], o["res"]          # 写失败不连累整轮
    assert o["res"]["webhook"] == "sent", o["res"]            # 仍尝试唤醒
    assert len(posted9) == 1, posted9                         # 不递归第二条 error 通知
    assert o["res"]["notify_record_error"] == "OSError", o["res"]
    assert o["notify_bytes"] is None, "写失败则通知记录不存在"
    assert "通知记录写入失败" in o["err"] and "OSError" in o["err"], o["err"]
    assert "notify_record_error" in o["res"], o["res"]
    # 投影字节仍正常 POST（写失败不影响发送内容）
    assert json.loads(posted9[0][0].decode("utf-8"))["release_id"] == "nf-wfail-1"
    print("9 notify write failure: still POST, ok/rc unchanged, diagnostic, no 2nd error ok")

    # 9b 全量结果也不可写：stderr/stdout 不声称记录完整
    def fail_result_write(self, data, *a, **k):
        if self.name.endswith(".result.json"):
            raise OSError("result disk full")
        return real_write_text(self, data, *a, **k)
    with mock.patch.object(Path, "write_bytes", fail_notify_write), \
         mock.patch.object(Path, "write_text", fail_result_write):
        o = run_round("nf-wfail-2")
    assert o["rc"] == 0 and o["res"]["ok"], o["res"]
    assert o["res"]["notify_record_error"] == "OSError", o["res"]
    assert o["result_text"] is None, "结果文件写失败则不存在"
    assert "通知记录写入失败" in o["err"] and "结果文件写入失败" in o["err"], o["err"]
    assert "notify_record_error" in o["stdout"], "stdout 须带写失败诊断、不声称完整"
    print("9b result also unwritable: stderr/stdout do not claim complete record ok")

    # 10 早期 error（bad_release_id）：不构造通知路径，既有发送与 stdout 不回归
    posted10 = []
    def ok_open10(req, timeout, proxies):
        posted10.append((req.data, proxies))
        return FakeResp()
    o = run_round("_bad-id", opener=ok_open10)
    assert o["rc"] == 1 and not o["res"]["ok"], o["res"]
    assert o["res"]["error"]["kind"] == "bad_release_id", o["res"]
    assert o["notify_bytes"] is None and o["notify_files"] == [], o["notify_files"]
    assert o["result_text"] is None, "早期 error 无可安全落盘的结果文件名"
    assert len(posted10) == 1, posted10                       # 仍发 webhook
    body = json.loads(posted10[0][0].decode("utf-8"))
    assert body["release_id"] == "_bad-id" and body["ok"] is False, body
    assert body["error"]["kind"] == "bad_release_id", body
    print("10 early error (bad_release_id): no unsafe notify path, send/stdout unchanged ok")

    # 11 脱敏：URL/密钥/头/代理/敏感消息不进通知/结果/stderr（失败 webhook 场景）
    def leak_open(req, timeout, proxies):
        raise urllib.error.URLError("reason-with-host-detail")
    o = run_round("nf-redact-1", opener=leak_open)
    assert o["res"]["webhook"] == "failed", o["res"]
    assert_clean(o["stdout"], o["err"], o["result_text"],
                 o["notify_bytes"].decode("utf-8"))
    print("11 redaction: no URL/secret/header/proxy/sensitive msg in notify/result/stderr ok")

    # 附：webhook 开/关生成相同通知投影（除 elapsed_s 计时外逐字一致）
    off = run_round("nf-parity-off", env_on=False)
    on = run_round("nf-parity-on", env_on=True)
    noff = json.loads(off["notify_bytes"].decode("utf-8"))
    non = json.loads(on["notify_bytes"].decode("utf-8"))
    for k in ("elapsed_s", "release_id", "run_record"):  # 计时/放行 id/临时目录路径逐轮不同
        noff.pop(k); non.pop(k)
    assert noff == non, (noff, non)
    print("extra: webhook on/off produce identical projection (modulo timing/id/path) ok")

    print("all notify dual-artifact tests passed")


if __name__ == "__main__":
    main()
