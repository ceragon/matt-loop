#!/usr/bin/env python3
"""test_failure_maps.py — 每阶段失败映射小表（P3，ADR-0020 决定 11）。

跑法：python3 tests/test_failure_maps.py
缝：`pipeline.translate(failure, rules)` 纯函数＋各阶段就近的规则表。断言只认
外部映射结果（外部 kind / 文案前缀与后缀 / 附加 hints）与两处显式 PASS 规则：
  - locate 里 session 抛的非 NotFound 失败原样穿透；
  - await_result 里未包装的 get 原样穿透。
每个表一组表驱动用例；同一网关操作在不同阶段映射到不同外部 kind（send 在启动
命令阶段是 launch_failed，在前置与主任务阶段是 send_refused / hc_failed）由同一张
send 阶段表按 stage 各自钉住（#133 R2）。
"""
import harness  # noqa: F401  （把 loop/scripts 放进 sys.path）
import pipeline
from herdr import HerdrFailure


def failure(op, stage, kind, *, source="hc", message="boom", hints=("h1",)):
    return HerdrFailure(kind, message, list(hints), op=op, source=source, stage=stage)


def mapped(table, f):
    """调用 translate 并返回映射出的 DispatchError；PASS 视为断言失败。"""
    try:
        return pipeline.translate(f, table)
    except HerdrFailure as e:
        raise AssertionError("期望映射，实际 PASS：kind=%r op=%r stage=%r"
                             % (e.kind, e.op, e.stage))


def passed(table, f):
    """translate 必须原样重抛同一个失败（显式 PASS 或进程层穿透）。"""
    try:
        pipeline.translate(f, table)
    except HerdrFailure as e:
        assert e is f, (e, f)
        return
    raise AssertionError("期望原样穿透，实际被映射了")


def check(table, cases):
    """表驱动：每条 (failure, 期望) 走一遍。期望 kind=None 表示 PASS。"""
    for f, expected in cases:
        if expected is None:
            passed(table, f)
            continue
        kind, message, hints = expected
        got = mapped(table, f)
        assert got.kind == kind, (f.op, f.stage, f.kind, got.kind, kind)
        assert got.message == message, (f.op, f.stage, f.kind, got.message, message)
        if hints is not None:
            assert got.hints == hints, (f.op, f.stage, f.kind, got.hints, hints)


def main():
    # ---- locate：非 NotFound 的 session 失败原样穿透（显式 PASS 之一） ----
    check(pipeline._LOCATE_RULES, [
        (failure("session", None, "unavailable"), None),
        (failure("session", None, "missing_tool"), None),
    ])
    print("1 locate: non-NotFound session failure passes through (explicit PASS) ok")

    # ---- send 阶段一张表：首发（blocked→send_refused／其余→hc_failed）、复核
    # get 原生穿透、补发与启动命令发送共用同一张表，按 stage 区分 ----
    extra = ["未再发送；台账已记录本次尝试"]
    check(pipeline._SEND_RULES, [
        (failure("send", "send", "blocked"),
         ("send_refused", "hc send 失败：boom", ["h1"] + extra)),
        (failure("send", "send", "unavailable"),
         ("hc_failed", "hc send 失败：boom", ["h1"] + extra)),
        (failure("send", "send", "missing_tool"), None),
        (failure("send", "send", "tool_timeout"), None),
        # 复核 get 失败：原生 kind 穿透；只有 status_mismatch 映射。
        (failure("send", "refused_not_done", "status_mismatch"),
         ("send_refused", "boom", ["h1"])),
        (failure("send", "refused_not_done", "unavailable"), None),
    ])
    print("2 send table: blocked->send_refused, other->hc_failed, process/native pass ok")

    # ---- 补发（第二次复核与裸发送）：同一张 send 表按 stage 区分 ----
    extra = ["本轮不再发送；台账已记录两次尝试"]
    check(pipeline._SEND_RULES, [
        (failure("send", "refused_before_resend", "status_mismatch"),
         ("send_refused", "boom", ["h1"])),
        (failure("send", "refused_before_resend", "unavailable"), None),
        (failure("send", "resend", "blocked"),
         ("send_refused", "done 补发仍失败：boom", ["h1"] + extra)),
        (failure("send", "resend", "missing_tool"), None),
    ])
    print("3 send table: refused_before_resend + resend mapped by the second stage ok")

    # ---- 启动命令发送（裸 send，stage=None）：同一张 send 表 → launch_failed ----
    check(pipeline._SEND_RULES, [
        (failure("send", None, "unavailable"),
         ("launch_failed", "启动命令发送失败：boom",
          ["h1", "主任务未发送；台账已记录本次尝试"])),
        (failure("send", None, "tool_timeout"), None),
    ])
    print("4 send table: launch-send stage -> launch_failed, process-layer passes ok")

    # ---- bring_up 一张表：创建命名会话（hc run）与打 loop 标签按 op 区分 ----
    check(pipeline._BRING_UP_RULES, [
        (failure("create_session", "missing_address", "missing_address"),
         ("launch_failed", "boom", ["h1"])),
        (failure("create_session", None, "hc_failed"),
         ("launch_failed", "hc run 创建命名会话失败（hc_failed）：boom",
          ["h1", "同名 live 终端存在（name_taken）时不建不 kill；"
                 "经用户放行后另起一轮或改用既有会话"])),
        (failure("create_session", None, "missing_tool"), None),
        (failure("label_session", None, "unavailable"),
         ("launch_failed", "打 loop 标签失败：boom",
          ["h1", "终端已建（launched:true）；无标签终端下轮会被拒收编"])),
        (failure("label_session", None, "hc_output"), None),
    ])
    print("5 bring-up table: create-session / label by op, launch_failed ok")

    # ---- 等 shell / 等 agent（同 op/stage，两张表区分文案） ----
    check(pipeline._SHELL_READY_RULES, [
        (failure("session", None, "unavailable"),
         ("launch_failed", "等 shell 就绪时 hc get 失败：boom", ["h1"])),
    ])
    check(pipeline._AGENT_READY_RULES, [
        (failure("session", None, "unavailable"),
         ("launch_failed", "等 agent 就绪时 hc get 失败：boom", ["h1"])),
    ])
    print("6 shell/agent ready tables: same op/stage, distinct prefixes ok")

    # ---- 重建 kill 占位者 ----
    check(pipeline._KILL_RULES, [
        (failure("kill", None, "unavailable"),
         ("launch_failed", "重建 kill 占位者失败：boom",
          ["h1", "占位者未死；本轮停止，经用户放行后另起一轮"])),
    ])
    print("7 rebuild-kill table: launch_failed ok")

    # ---- await_result：wait_change 包装，未包装 get 原样穿透（显式 PASS 之二） ----
    check(pipeline._AWAIT_RESULT_RULES, [
        (failure("wait_change", None, "unavailable"),
         ("hc_failed", "hc wait 失败：boom", ["h1"])),
        (failure("wait_change", None, "missing_tool"), None),
        (failure("session", None, "unavailable"), None),
        (failure("session", None, "blocked"), None),
    ])
    print("8 await-result table: wait wrapped, unwrapped get passes (explicit PASS) ok")

    # ---- 领养（app 源；hints 由调用方换成 retry，映射只给 reason/文案） ----
    check(pipeline._ADOPT_OPEN_RULES, [
        (failure("selected_space", None, "app_failed", source="app"),
         ("app_closed", "herdr-app 没开或不可达，领养失败：boom", ["h1"])),
        (failure("layout", None, "app_failed", source="app"),
         ("app_closed", "herdr-app ls 失败，领养失败：boom", ["h1"])),
    ])
    check(pipeline._ADOPT_CREATE_RULES, [
        (failure("create_space", "missing_space", "space_output", source="app"),
         ("keep_error", "boom", ["h1"])),
        (failure("create_space", None, "app_failed", source="app"),
         ("keep_error", "space.new 失败：boom", ["h1"])),
    ])
    check(pipeline._ADOPT_RECHECK_RULES, [
        (failure("selected_space", None, "app_failed", source="app"),
         ("keep_error", "space.new 后 status 复核失败：boom", ["h1"])),
    ])
    print("9 adopt tables: open/create/recheck map to adopt_reason + prefix ok")

    # ---- keep 落地：三种领域失败 + 兜底 ----
    check(pipeline._KEEP_RULES, [
        (failure("keep", "keep_command", "app_failed", source="app"),
         ("keep_error", "terminal.keep 失败：boom", ["h1"])),
        (failure("keep", "keep_poll", "app_failed", source="app"),
         ("app_closed", "keep 后 herdr-app ls 失败：boom", ["h1"])),
        (failure("keep", "no_pane", "no_placement", source="app"),
         ("no_pane", "boom", ["h1"])),
        # 兜底：任何其它 stage 都落 keep_error（基线最后的 fallback）。
        (failure("keep", "something_else", "app_failed", source="app"),
         ("keep_error", "terminal.keep 失败：boom", ["h1"])),
    ])
    print("10 keep table: keep_command/keep_poll/no_pane + catch-all ok")

    # ---- focus 进入：复核不符带「，keep 未发」后缀；其余 select_error ----
    check(pipeline._FOCUS_RULES, [
        (failure("focus", "focus_select_mismatch", "status_mismatch", source="app"),
         ("select_error", "boom，keep 未发", ["h1"])),
        (failure("focus", "space_select", "app_failed", source="app"),
         ("select_error", "space.select 失败：boom", ["h1"])),
        (failure("focus", "space_verify", "app_failed", source="app"),
         ("select_error", "space.select 失败：boom", ["h1"])),
    ])
    print("11 focus table: mismatch suffix + catch-all select_error ok")

    # ---- op/stage 隔离：不属于该表的失败原样穿透（不是漏写） ----
    passed(pipeline._SEND_RULES, failure("kill", None, "unavailable"))
    passed(pipeline._ADOPT_OPEN_RULES, failure("focus", None, "app_failed", source="app"))
    print("12 op/stage isolation: failures outside a table pass through ok")

    print("ALL FAILURE MAP TESTS PASSED")


if __name__ == "__main__":
    main()
