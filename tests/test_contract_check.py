#!/usr/bin/env python3
"""test_contract_check.py — 契约一致性检查器自身的测试（T3，ADR-0016 决定 6）。

跑法：python3 tests/test_contract_check.py
检查对象是假世界/样例的返回，不是生产代码：必填键是否齐、枚举域取值是否在范围内、
键的类型是否对；不做键集精确相等。样例被改坏时检查器必须当场报错。
"""
import contract

load = contract.load_sample


def must_fail(command, data, label):
    try:
        contract.check(command, data)
    except contract.ContractError:
        return
    raise AssertionError("检查器放过了改坏的样例：" + label)


def main():
    # 1 实测样例原样通过
    contract.check("hc.get", load("hc_get_live.json"))
    contract.check("hc.get", load("hc_get_finished.json"))
    contract.check("hc.get", load("hc_get_shell.json"))
    print("1 real-shaped hc get samples pass")

    # 2 必填键缺失 → 报错（address 是 schema 必填键）
    d = load("hc_get_live.json")
    del d["address"]
    must_fail("hc.get", d, "hc.get 缺 address")

    # 3 枚举域外取值 → 报错（state 只有 live/finished）
    d = load("hc_get_live.json")
    d["state"] = "dead"
    must_fail("hc.get", d, "hc.get state=dead")

    # 4 类型错 → 报错（turn 是整数，不是字符串）
    d = load("hc_get_live.json")
    d["turn"] = "3"
    must_fail("hc.get", d, "hc.get turn 是字符串")

    # 5 嵌套对象同样核对：status.state 枚举域、status.kind 枚举域
    d = load("hc_get_live.json")
    d["status"]["state"] = "paused"
    must_fail("hc.get", d, "status.state=paused")
    d = load("hc_get_live.json")
    d["status"]["kind"] = "login"   # 旧假桩的越域值（schema 只有 permission/question/auth）
    must_fail("hc.get", d, "status.kind=login")

    # 6 键集不精确相等：多余键不报错（真实 hc get 约 30 字段，fake 只需表达被读的部分）
    d = load("hc_get_live.json")
    d["some_future_key"] = {"x": 1}
    contract.check("hc.get", d)
    d = load("hc_get_finished.json")
    d["reason"] = "bogus"      # reason 有 schema 枚举域（8 个取值）
    must_fail("hc.get", d, "reason=bogus")
    print("2-6 broken samples rejected, extra keys tolerated")

    # 7 hc send/wait/read/rm：schema 必填键与枚举域
    contract.check("hc.send", load("hc_send.json"))
    contract.check("hc.wait", load("hc_wait.json"))
    contract.check("hc.read", load("hc_read.json"))
    contract.check("hc.rm", load("hc_rm.json"))
    d = load("hc_wait.json")
    del d["children"]          # WaitResult 必填（旧假桩只返回 reason/status/turn）
    must_fail("hc.wait", d, "hc.wait 缺 children")
    d = load("hc_read.json")
    d["how"] = "current"       # 旧假桩的越域值（schema 只有 screen/history/stitched）
    must_fail("hc.read", d, "hc.read how=current")
    d = load("hc_read.json")
    d["why"] = ""              # 旧假桩的越域值（schema 是 working/blocked/error/null）
    must_fail("hc.read", d, "hc.read why=空串")
    d = load("hc_send.json")
    del d["turn"]
    must_fail("hc.send", d, "hc.send 缺 turn")
    d = load("hc_rm.json")
    d["removed"] = 42
    must_fail("hc.rm", d, "hc.rm removed 非字符串")
    print("7 hc send/wait/read/rm contracts enforced")

    # 8 herdr-app status/ls：实测形状
    contract.check("app.status", load("app_status.json"))
    contract.check("app.ls", load("app_ls.json"))
    d = load("app_status.json")
    del d["space"]
    must_fail("app.status", d, "app.status 缺 space")
    d = load("app_ls.json")
    del d["spaces"][0]["tabs"][0]["terminals"][0]["pane"]
    must_fail("app.ls", d, "app.ls 终端条目缺 pane 键")
    d = load("app_ls.json")
    d["spaces"][0]["tabs"][0]["terminals"][0]["status"] = {"state": "idle"}
    must_fail("app.ls", d, "app.ls status 是对象（应为字符串或 null）")
    d = load("app_ls.json")
    d["recent"] = {}
    must_fail("app.ls", d, "app.ls recent 非数组")
    print("8 app status/ls contracts enforced")

    # 9 do 返回（假设形状，README 显式标注）：space.new 必含 space 键
    contract.check("app.do.space.new", load("app_space_new.json"))
    contract.check("app.do.space.select", load("app_do_empty.json"))
    contract.check("app.do.tab.close", load("app_do_empty.json"))
    contract.check("app.do.tab.rename", load("app_do_empty.json"))
    contract.check("app.do.terminal.keep", {"pane": "<PANE_ID>", "terminal": "<ADDR>"})
    d = load("app_space_new.json")
    del d["space"]
    must_fail("app.do.space.new", d, "space.new 缺 space 键")
    must_fail("app.do.terminal.keep", {"pnae": "<PANE_ID>", "terminal": "<ADDR>"},
              "keep 返回 pane 键抄错")
    print("9 do return contracts enforced")

    # 10 未知命令契约键本身也是错（检查器键抄错同样当场失败）
    must_fail("hc.gett", load("hc_get_live.json"), "未知命令键")
    print("10 unknown contract key rejected")

    print("all contract-check tests passed")


if __name__ == "__main__":
    main()