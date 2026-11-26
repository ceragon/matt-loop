#!/usr/bin/env python3
"""test_dispatch_tools.py — 受理阶段工具定位闸（#130，ADR-0005 决定 7 / ADR-0020 决定 3/5）。

跑法：python3 tests/test_dispatch_tools.py
缝：唯一业务缝 harness.run（CLI 契约）；工具定位经 `locate` 注入返回「找不到」。
默认装配的 locate 对任何名字都返回假路径，覆盖不到「缺工具」分支，故本文件显式注入：
  1 api 后端（显式 env＋带齐 API 配置）、pi 与 uv 都定位不到：正常受理、一轮跑通。
    pi 只在 backend=pi 时才需要（ADR-0005 决定 7）；uv 只在 <3.10 自举路径里用，
    不每轮要求（#129/#130）。
  2 pi 后端、pi 定位不到：missing_tool，文案来自后端相关检查（locate 阶段），
    不是静态闸的旧版本文案。
  3 hc 定位不到：missing_tool，静态闸的文案与两阶段先后次序不变（ADR-0020 决定 3/5
    冻结契约）。
"""
import tempfile

import harness
from helpers import EXT, PROMPT, api_env, argv, ext, world


def locate_missing(*missing):
    """工具定位注入：missing 里的名字返回 None（找不到），其余走 harness 默认假路径。"""
    def locate(name, probe_dirs=None):
        return None if name in missing else harness.default_locate(name, probe_dirs)
    return locate


def run_tools_case(rid, *, env, missing=()):
    """三个用例共用的装配：新建 world 与临时目录，经唯一缝 harness.run 跑一轮。
    返回 (world, run 结果)；断言仍在各用例里。"""
    w = world()
    with tempfile.TemporaryDirectory() as td:
        r = harness.run(argv(td, rid), world=w, env=env,
                        locate=locate_missing(*missing), extract=ext(EXT))
    return w, r


def main():
    # 1 api 后端、缺 pi（及 uv）：正常受理并跑通一轮（ADR-0005 决定 7）
    w, r = run_tools_case("tools-api-nopi-1", env=api_env(), missing=("pi", "uv"))
    assert r.rc == 0, r.result
    assert r.result["ok"] is True and r.result["extract_backend"] == "api", r.result
    assert w.sends == [("t99", PROMPT, True)], w.sends
    assert r.ledger["params"]["extract_backend"] == "api", r.ledger
    print("1 api backend without pi/uv: round admitted and completed OK")

    # 2 pi 后端、缺 pi：missing_tool 由后端相关检查给出（文案保持该检查的版本）
    w, r = run_tools_case("tools-pi-nopi-1",
                          env={"LOOP_EXTRACT_BACKEND": "pi"}, missing=("pi",))
    assert r.rc == 1, r.result
    assert r.result["error"]["kind"] == "missing_tool", r.result
    assert r.result["error"]["message"] == "PATH 与常见目录里都找不到 pi", r.result
    assert w.sends == [], "工具缺失须零发送"
    print("2 pi backend without pi: missing_tool from backend check OK")

    # 3 缺 hc：missing_tool，静态闸文案与次序不变（ADR-0020 决定 3/5）
    w, r = run_tools_case("tools-nohc-1",
                          env={"LOOP_EXTRACT_BACKEND": "pi"}, missing=("hc",))
    assert r.rc == 1, r.result
    assert r.result["error"]["kind"] == "missing_tool", r.result
    assert r.result["error"]["message"] == "找不到 hc", r.result
    assert r.result["error"]["hints"] == [
        "确认 PATH；脚本也会探 ~/.local/bin、homebrew、nvm node bin"], r.result
    assert w.sends == [], "工具缺失须零发送"
    print("3 missing hc: missing_tool message/order unchanged OK")

    print("all dispatch-tools tests passed")


if __name__ == "__main__":
    main()
