#!/usr/bin/env python3
"""test_command_parse.py — 命令执行桩的解析测试（T3，ADR-0016 决定 4/6）。

跑法：python3 tests/test_command_parse.py
fake 说 dict、绕过真实错误信封解析；本文件用 Deps(run_cmd=…) 注入命令执行器，
喂脱敏真实信封给 hc_json / app_json 的真实解析路径，断言映射出的错误种类与
附加字段（P2 收口：不再作用域替换模块全局 run_cmd）。信封样例来自 tests/fixtures/
（来源分级见 README）。不靠真进程。
噪声护栏（T5d 自 turn_cut slice8 并入，#50/#99 Q3）：非 JSON stdout／非零退出的
其余纯文本 stderr/stdout 摘录 300 字符护栏——FakeHerdr 说 dict 造不出的故障留在本缝。
"""
import json
import types

import contract
from harness import dispatch

load = contract.load_sample


def ns(rc, stdout, stderr=""):
    return types.SimpleNamespace(returncode=rc, stdout=stdout, stderr=stderr)


def deps_with(fn, *, env=None):
    """命令层 Deps：run_cmd 注入为桩，三个 bin 填占位路径（不解析工具）。"""
    deps = dispatch.Deps(run_cmd=fn, env=env)
    deps.hc_bin = "/fake/hc"
    deps.app_bin = "/fake/herdr-app"
    deps.pi_bin = "/fake/pi"
    return deps


def expect(kind, fn):
    try:
        fn()
    except dispatch.DispatchError as e:
        assert e.kind == kind, (e.kind, kind)
        return e
    raise AssertionError("预期 DispatchError(%s) 未抛" % kind)


def main():
    # 1 hc 错误信封（实测形状 {"error":{kind,message,hint}}）→ HerdrFailure 透传 kind
    envelope = load("hc_error_not_found.json")
    deps = deps_with(lambda argv, ts: ns(3, json.dumps(envelope), "hc: no terminal"))
    e = expect("not_found", lambda: deps.hc_json(["get", "t404"], 30))
    assert isinstance(e, dispatch.HerdrFailure)
    assert e.returncode == 3 and e.detail == envelope
    assert any("hc: no terminal" in h for h in e.hints)
    print("1 hc error envelope: kind passthrough, rc, detail, hints")

    # 2 hc 兼容形状：顶层 {"kind": ...} 也认
    deps = deps_with(lambda argv, ts: ns(1, '{"kind": "status_mismatch"}'))
    e = expect("status_mismatch", lambda: deps.hc_json(["send"], 120))
    assert e.detail == {"kind": "status_mismatch"}
    print("2 hc top-level kind compat")

    # 3 hc rc 0 但 stdout 不是 JSON 对象 → hc_output
    deps = deps_with(lambda argv, ts: ns(0, "not json"))
    expect("hc_output", lambda: deps.hc_json(["get", "t99"], 30))
    deps = deps_with(lambda argv, ts: ns(0, "[1,2]"))
    expect("hc_output", lambda: deps.hc_json(["get", "t99"], 30))
    print("3 hc non-object stdout -> hc_output")

    # 4 hc rc 1 且无 JSON 可解析 → hc_failed、detail None
    deps = deps_with(lambda argv, ts: ns(1, "", "boom"))
    e = expect("hc_failed", lambda: deps.hc_json(["get", "t99"], 30))
    assert e.detail is None and e.returncode == 1
    print("4 hc unparseable failure -> hc_failed")

    # 5 hc stderr 兜底：stdout 空、stderr 带信封
    deps = deps_with(lambda argv, ts: ns(3, "", json.dumps(envelope)))
    e = expect("not_found", lambda: deps.hc_json(["get", "t404"], 30))
    assert e.detail == envelope
    print("5 hc stderr envelope fallback")

    # 6 app 错误信封（实测：error 是字符串）→ 统一 app_failed，detail 逐字
    app_err = load("app_do_error.json")
    deps = deps_with(lambda argv, ts: ns(1, json.dumps(app_err)))
    e = expect("app_failed", lambda: deps.app_json(["do", "--help"], 30))
    assert isinstance(e, dispatch.HerdrFailure)
    assert e.detail == app_err and e.returncode == 1
    assert "unknown command" in e.message
    print("6 app string error envelope -> app_failed")

    # 7 app 兼容形状：error 是对象时取 kind/message 进消息
    deps = deps_with(lambda argv, ts: ns(1, '{"error": {"kind": "x", "message": "y"}}'))
    e = expect("app_failed", lambda: deps.app_json(["ls"], 30))
    assert "x" in e.message
    print("7 app object error compat")

    # 8 app rc 0 非 JSON 对象 → app_output
    deps = deps_with(lambda argv, ts: ns(0, "plain text"))
    expect("app_output", lambda: deps.app_json(["ls"], 30))
    print("8 app non-object stdout -> app_output")

    # 9 app 未开信封（假设形状，README 标注）→ 同样 app_failed
    closed = load("app_not_running.json")
    deps = deps_with(lambda argv, ts: ns(1, json.dumps(closed)))
    e = expect("app_failed", lambda: deps.app_json(["status"], 30))
    assert e.detail == closed
    print("9 app not-running envelope (assumed shape) -> app_failed")

    # 10 argv 拼装契约：hc 的 --json 紧跟子命令；app 原样透传、不插 --json
    seen = []
    deps = deps_with(lambda argv, ts: (seen.append(list(argv)), ns(0, "{}"))[1])
    deps.hc_json(["send", "--", "t99", "-开头文本"], 120)
    deps.app_json(["do", "tab.close", "{}"], 30)
    assert seen[0] == ["/fake/hc", "send", "--json", "--", "t99", "-开头文本"]
    assert seen[1] == ["/fake/herdr-app", "do", "tab.close", "{}"]
    print("10 argv assembly: --json right after hc subcommand, app passthrough")

    # 11 噪声护栏（T5d 自 turn_cut slice8 并入，#50/#99）：其余纯文本 stderr/stdout
    #    摘录的 300 字符护栏保持不变（#50 只改 JSON 截断）。
    long = "噪" * 500
    # hc_output：rc 0 但 stdout 非 JSON
    deps = deps_with(lambda argv, ts: ns(0, long, ""))
    e = expect("hc_output", lambda: deps.hc_json(["read", "t99"], 30))
    assert e.hints == [long[:300]], e.hints
    # HerdrFailure：rc 1，stderr/stdout 摘录各 300
    deps = deps_with(lambda argv, ts: ns(1, long, long))
    e = expect("hc_failed", lambda: deps.hc_json(["get", "t99"], 30))
    assert isinstance(e, dispatch.HerdrFailure)
    assert e.hints == [long[:300], long[:300]], e.hints
    # app_output：rc 0 但 stdout 非 JSON
    deps = deps_with(lambda argv, ts: ns(0, long, ""))
    e = expect("app_output", lambda: deps.app_json(["ls"], 30))
    assert e.hints == [long[:300]], e.hints
    # parse_extraction：找不到形状 JSON 时 stdout 摘录 300（纯函数直调）
    try:
        dispatch.parse_extraction(long)
        raise AssertionError("should raise extraction_invalid")
    except dispatch.DispatchError as e:
        assert e.kind == "extraction_invalid" and e.hints[-1] == long[:300], e.hints
    # extract_via_pi：非零退出时 stderr/stdout 摘录 300（经 Deps 注入 run_cmd）
    deps = deps_with(lambda argv, ts: ns(1, "", long),
                     env={"LOOP_EXTRACT_BACKEND": "pi"})
    # prompt_file 必填（回声参照读取）；用本文件作可读提示词，内容与本用例无关。
    request = types.SimpleNamespace(
        pi_model=None, prompt_file=__file__,
        timing=types.SimpleNamespace(extract_timeout=1200.0))
    e = expect("extraction_failed",
               lambda: deps.extract(request, "/fake/turn.md", ""))
    assert e.hints[-1] == long[:300], e.hints
    print("11 noise guards: other 300-char excerpts unchanged ok")

    print("all command-parse tests passed")


if __name__ == "__main__":
    main()
