#!/usr/bin/env python3
"""test_fake_herdr.py — 假世界 FakeHerdr 自身的测试（T3，ADR-0016 决定 5）。

跑法：python3 tests/test_fake_herdr.py
只测测试基础设施本身：场景方法构造的世界经两个命令层入口可观察、
三类旋钮（故障/延迟/外部干扰）生效、故障键拼错在声明阶段报错。不解释业务。
"""
import json

import contract
from fake_herdr import FakeHerdr
from harness import dispatch


def raises(kind, fn):
    try:
        fn()
    except dispatch.HcFailure as e:
        assert e.kind == kind, (e.kind, kind)
        return e
    raise AssertionError("预期 HcFailure(%s) 未抛" % kind)


def app_raises(fn):
    try:
        fn()
    except dispatch.AppFailure as e:
        return e
    raise AssertionError("预期 AppFailure 未抛")


def main():
    # 1 既有终端：按地址与按名字都解析；返回过契约检查
    w = FakeHerdr()
    w.add_terminal(address="t99", name="claude-test", labels={"loop": "1"})
    g = w.hc_json(None, ["get", "t99"], 30)
    contract.check("hc.get", g)
    assert g["state"] == "live" and g["status"]["state"] == "idle"
    assert g["labels"] == {"loop": "1"} and g["turn"] == 5
    assert w.hc_json(None, ["get", "claude-test"], 30)["address"] == "t99"
    raises("not_found", lambda: w.hc_json(None, ["get", "nope"], 30))
    print("1 existing terminal resolves by address and name")

    # 2 已死终端：foreground 键整个缺失、status:null（实测形状）；名字只解析 live
    w = FakeHerdr()
    w.add_terminal(address="t99", name="claude-test", state="finished")
    g = w.hc_json(None, ["get", "t99"], 30)
    contract.check("hc.get", g)
    assert "foreground" not in g and g["status"] is None
    assert g["state"] == "finished" and g["exit_code"] is not None
    raises("not_found", lambda: w.hc_json(None, ["get", "claude-test"], 30))
    print("2 finished terminal: foreground absent, status null, name unresolvable")

    # 3 空闲 shell：status:null、前台是 shell
    w = FakeHerdr()
    w.add_terminal(address="t99", fg="shell")
    g = w.hc_json(None, ["get", "t99"], 30)
    assert g["status"] is None and g["foreground"]["name"] == "zsh"
    print("3 idle shell: status null, shell foreground")

    # 4 send：--if idle 守卫语义＋终端级 turn 累计（返回处理前基线）
    w = FakeHerdr()
    w.add_terminal(address="t99", turn=5)
    r = w.hc_json(None, ["send", "--if", "idle", "--", "t99", "你好"], 120)
    contract.check("hc.send", r)
    assert r["turn"] == 5
    assert w.hc_json(None, ["get", "t99"], 30)["turn"] == 6
    assert w.sends == [("t99", "你好", True)]
    # working → status_mismatch；done → status_mismatch（裸 send 才补发）
    w.set_status("t99", "working")
    raises("status_mismatch",
           lambda: w.hc_json(None, ["send", "--if", "idle", "--", "t99", "x"], 120))
    w.set_status("t99", "done")
    raises("status_mismatch",
           lambda: w.hc_json(None, ["send", "--if", "idle", "--", "t99", "x"], 120))
    r = w.hc_json(None, ["send", "--", "t99", "x"], 120)
    assert r["turn"] == 6 and w.sends[-1] == ("t99", "x", False)
    print("4 send: --if idle guard, terminal-level turn baseline")

    # 5 wait/read：完整 WaitResult / ReadResult 形状
    w = FakeHerdr()
    w.add_terminal(address="t99", turn=7)
    w.screen("t99", "屏幕内容")
    wt = w.hc_json(None, ["wait", "t99", "--timeout", "1.0"], 31)
    contract.check("hc.wait", wt)
    assert wt["reason"] == "status" and wt["status"] == "idle" and wt["turn"] == 7
    rd = w.hc_json(None, ["read", "t99", "--lines", "1000"], 30)
    contract.check("hc.read", rd)
    assert rd["text"] == "屏幕内容" and rd["how"] in ("screen", "history", "stitched")
    print("5 wait/read return full contract shapes")

    # 6 run：创建 shell 终端 turn 0；同名 live → name_taken（真实仲裁语义）
    w = FakeHerdr()
    r = w.hc_json(None, ["run", "--bg", "--keep", "--no-parent", "--name",
                         "claude-test", "--cwd", "/proj", "--", "zsh", "-l"], 120)
    contract.check("hc.run", r)
    assert r["state"] == "live" and r["turn"] == 0 and r["status"] is None
    addr = r["address"]
    assert w.hc_json(None, ["get", "claude-test"], 30)["address"] == addr
    raises("name_taken",
           lambda: w.hc_json(None, ["run", "--name", "claude-test", "--", "zsh", "-l"], 120))
    print("6 run creates shell terminal, name_taken arbitrated")

    # 7 label/kill/rm：标签增删、kill 置 finished、rm 的 live 拒绝（schema kind=live）
    w = FakeHerdr()
    w.add_terminal(address="t99", name="claude-test")
    r = w.hc_json(None, ["label", "t99", "loop=1", "loop-agent=claude"], 30)
    contract.check("hc.label", r)
    assert r["labels"] == {"loop": "1", "loop-agent": "claude"}
    w.hc_json(None, ["label", "t99", "loop-agent="], 30)
    assert w.hc_json(None, ["get", "t99"], 30)["labels"] == {"loop": "1"}
    raises("live", lambda: w.hc_json(None, ["rm", "t99"], 30))
    r = w.hc_json(None, ["kill", "t99"], 120)
    contract.check("hc.kill", r)
    assert r["state"] == "finished"
    r = w.hc_json(None, ["rm", "t99"], 30)
    contract.check("hc.rm", r)
    assert r["removed"] == "t99"
    raises("not_found", lambda: w.hc_json(None, ["rm", "t99"], 30))
    print("7 label/kill/rm semantics")

    # 8 启动效果：agent 起来 / stuck_shell 卡壳 / die 死亡 / dialog 弹框 / wrong_agent
    for effect, check in [
        ("agent", lambda g: g["status"]["state"] == "idle"
         and g["foreground"]["argv"][0] == "claude"),
        ("stuck_shell", lambda g: g["status"] is None
         and g["foreground"]["name"] == "zsh"),
        ("die", lambda g: g["state"] == "finished"),
        ("dialog", lambda g: g["status"]["state"] == "blocked"
         and g["status"]["kind"] in ("permission", "question", "auth")),
        ("wrong_agent", lambda g: g["status"]["app"] == "grok"),
    ]:
        w = FakeHerdr()
        w.add_terminal(address="t99", fg="shell", turn=0)
        w.will_launch("claude --dangerously-skip-permissions", effect)
        w.hc_json(None, ["send", "--", "t99", "claude --dangerously-skip-permissions"], 120)
        g = w.hc_json(None, ["get", "t99"], 30)
        assert check(g), (effect, g)
    print("8 launch effects: agent/stuck_shell/die/dialog/wrong_agent")

    # 8b 启动中：run 建 shell → 发启动命令 → agent 未就绪（前台仍是 shell、turn 0、
    #    无自报状态），与「空闲 shell」（既有、turn>0）可区分
    w = FakeHerdr()
    w.will_launch("claude --dangerously-skip-permissions", "stuck_shell")
    r = w.hc_json(None, ["run", "--name", "claude-test", "--cwd", "/proj",
                         "--", "zsh", "-l"], 120)
    w.hc_json(None, ["send", "--", "claude-test",
                     "claude --dangerously-skip-permissions"], 120)
    g = w.hc_json(None, ["get", r["address"]], 30)
    assert g["turn"] == 0 and g["status"] is None
    assert g["foreground"]["name"] == "zsh"   # 启动命令已发、agent 未起来
    print("8b starting-up scenario distinguishable from idle shell")

    # 9 多空间多标签页：ls 三层树投影过契约；status 报选中空间
    w = FakeHerdr()
    w.add_terminal(address="t50", name="claude-test")
    w.add_terminal(address="t60")
    sp1 = w.add_space(name="main")
    sp2 = w.add_space(name="side", folder="/proj")
    w.add_tab(sp1, terminals=["t50"])
    w.add_tab(sp1, name="第二个标签页", terminals=["t60"], panes=False)
    w.add_tab(sp2, terminals=["t50"])   # 同一终端归属两个空间
    ls = w.app_json(None, ["ls"], 30)
    contract.check("app.ls", ls)
    assert [sp["name"] for sp in ls["spaces"]] == ["main", "side"]
    assert ls["spaces"][0]["tabs"][1]["terminals"][0]["pane"] is None
    assert ls["spaces"][0]["tabs"][0]["terminals"][0]["status"] == "idle"
    st = w.app_json(None, ["status"], 30)
    contract.check("app.status", st)
    assert st["space"] == sp1
    print("9 multi-space multi-tab ls/status projection")

    # 10 terminal.keep：落进当前选中空间的新标签页，pane 落地
    w = FakeHerdr()
    w.add_terminal(address="t50")
    sp1 = w.add_space(name="main")
    w.app_json(None, ["do", "terminal.keep", json.dumps({"terminal": "t50"})], 30)
    ls = w.app_json(None, ["ls"], 30)
    entry = ls["spaces"][0]["tabs"][0]["terminals"][0]
    assert entry["terminal"] == "t50" and entry["pane"]
    print("10 keep lands pane into selected space")

    # 11 space.select：改选中；未知 id → AppFailure
    w = FakeHerdr()
    sp1 = w.add_space(name="main")
    sp2 = w.add_space(name="side")
    w.app_json(None, ["do", "space.select", json.dumps({"id": sp2})], 30)
    assert w.app_json(None, ["status"], 30)["space"] == sp2
    app_raises(lambda: w.app_json(None, ["do", "space.select",
                                         json.dumps({"id": "sp-nope"})], 30))
    print("11 space.select changes selection, unknown id fails")

    # 12 space.new：建空间＋赠送 shell；零空间时新空间即被显示
    w = FakeHerdr()
    r = w.app_json(None, ["do", "space.new",
                          json.dumps({"name": "proj", "folder": "/proj",
                                      "focus": False})], 30)
    contract.check("app.do.space.new", r)
    assert r["space"] and r["terminal"]
    assert w.app_json(None, ["status"], 30)["space"] == r["space"]
    gift = w.hc_json(None, ["get", r["terminal"]], 30)
    assert gift["status"] is None and gift["foreground"]["name"] == "zsh"
    print("12 space.new creates space with gifted shell")

    # 13 tab.close 结束终端、tab.rename 改名（ls 复核可见）
    w = FakeHerdr()
    w.add_terminal(address="t50")
    sp1 = w.add_space(name="main")
    tb = w.add_tab(sp1, terminals=["t50"])
    w.app_json(None, ["do", "tab.rename",
                      json.dumps({"id": tb, "name": "新名字"})], 30)
    assert w.app_json(None, ["ls"], 30)["spaces"][0]["tabs"][0]["name"] == "新名字"
    w.app_json(None, ["do", "tab.close",
                      json.dumps({"id": tb, "confirmed": True})], 30)
    assert w.app_json(None, ["ls"], 30)["spaces"][0]["tabs"] == []
    assert w.hc_json(None, ["get", "t50"], 30)["state"] == "finished"
    print("13 tab.close finishes terminal, tab.rename lands name")

    # 14 故障旋钮（命令, 模式, 参数）：error 按 kind 与 nth 定位那一次调用
    w = FakeHerdr()
    w.add_terminal(address="t99")
    w.fail("send", "error", kind="blocked", nth=2)
    w.hc_json(None, ["send", "--if", "idle", "--", "t99", "第一条"], 120)
    e = raises("blocked",
               lambda: w.hc_json(None, ["send", "--if", "idle", "--", "t99", "第二条"], 120))
    assert e.detail == {"error": {"kind": "blocked", "message": e.message}}
    w.hc_json(None, ["send", "--if", "idle", "--", "t99", "第三条"], 120)
    assert [s[1] for s in w.sends] == ["第一条", "第三条"]
    print("14 fail knob: error with kind and nth")

    # 15 noop：命令成功但效果不落地（rename 成功、ls 复核名不变）
    w = FakeHerdr()
    w.add_terminal(address="t50")
    sp1 = w.add_space(name="main")
    tb = w.add_tab(sp1, terminals=["t50"])
    w.fail("tab.rename", "noop")
    w.app_json(None, ["do", "tab.rename",
                      json.dumps({"id": tb, "name": "新名字"})], 30)
    assert w.app_json(None, ["ls"], 30)["spaces"][0]["tabs"][0]["name"] is None
    w.fail("space.select", "noop")
    sp2 = w.add_space(name="side")
    w.app_json(None, ["do", "space.select", json.dumps({"id": sp2})], 30)
    assert w.app_json(None, ["status"], 30)["space"] == sp1
    print("15 noop knob: success without effect")

    # 16 延迟旋钮：keep 的 pane 第 N 次 ls 才落地（#40 异步落地）
    w = FakeHerdr()
    w.add_terminal(address="t50")
    w.add_space(name="main")
    w.delay("terminal.keep", "land_after", after=2)
    w.app_json(None, ["do", "terminal.keep", json.dumps({"terminal": "t50"})], 30)
    ls1 = w.app_json(None, ["ls"], 30)
    assert ls1["spaces"][0]["tabs"][0]["terminals"][0]["pane"] is None
    ls2 = w.app_json(None, ["ls"], 30)
    assert ls2["spaces"][0]["tabs"][0]["terminals"][0]["pane"]
    print("16 delay knob: pane lands after N ls polls")

    # 17 延迟旋钮：tab.close 后第 N 次 get 才 finished（#46 Bug2）；vanish 直接收走条目
    w = FakeHerdr()
    w.add_terminal(address="t50")
    sp1 = w.add_space(name="main")
    tb = w.add_tab(sp1, terminals=["t50"])
    w.delay("tab.close", "land_after", after=2)
    w.app_json(None, ["do", "tab.close", json.dumps({"id": tb, "confirmed": True})], 30)
    assert w.hc_json(None, ["get", "t50"], 30)["state"] == "live"
    assert w.hc_json(None, ["get", "t50"], 30)["state"] == "finished"
    w = FakeHerdr()
    w.add_terminal(address="t50")
    sp1 = w.add_space(name="main")
    tb = w.add_tab(sp1, terminals=["t50"])
    w.delay("tab.close", "vanish")
    w.app_json(None, ["do", "tab.close", json.dumps({"id": tb, "confirmed": True})], 30)
    raises("not_found", lambda: w.hc_json(None, ["get", "t50"], 30))
    print("17 delay knob: finish countdown and vanish")

    # 18 延迟旋钮：select 已生效但紧接的 status 报旧选中（复核抖动）
    w = FakeHerdr()
    sp1 = w.add_space(name="main")
    sp2 = w.add_space(name="side")
    w.delay("space.select", "land_after", after=2)
    w.app_json(None, ["do", "space.select", json.dumps({"id": sp2})], 30)
    assert w.app_json(None, ["status"], 30)["space"] == sp1   # 抖动：报旧值
    assert w.app_json(None, ["status"], 30)["space"] == sp2
    print("18 delay knob: stale status read after select")

    # 19 故障键守卫：命令名拼错/模式拼错/不适用组合在声明阶段抛错
    for bad in [lambda: FakeHerdr().fail("sned", "error"),
                lambda: FakeHerdr().delay("terminal.keep", "land_afterr"),
                lambda: FakeHerdr().fail("get", "bogus_mode"),
                lambda: FakeHerdr().fail("get", "noop"),          # 只读命令无 noop
                lambda: FakeHerdr().delay("send", "land_after"),  # 不适用的延迟组合
                lambda: FakeHerdr().delay("tab.close", "bogus"),
                lambda: FakeHerdr().fail("keys", "error")]:  # 真实命令但假世界未实现
        try:
            bad()
        except ValueError:
            continue
        raise AssertionError("守卫放过了非法旋钮声明")
    print("19 fault-key guard rejects typos at declaration time")

    # 20 外部干扰：app 没开着（一切 app 命令失败，错误信封是假设形状）
    w = FakeHerdr()
    w.add_space(name="main")
    w.app_closed()
    for args in (["status"], ["ls"],
                 ["do", "terminal.keep", json.dumps({"terminal": "t50"})]):
        e = app_raises(lambda a=args: w.app_json(None, a, 30))
        assert e.detail == {"error": "not running"}
    print("20 event: app not running")

    # 21 外部干扰：用户切走空间（立即；或挂在某条命令之后——落地窗口内的接管）
    w = FakeHerdr()
    sp1 = w.add_space(name="main")
    sp2 = w.add_space(name="side")
    w.user_switches_space()
    assert w.app_json(None, ["status"], 30)["space"] == sp2
    w = FakeHerdr()
    w.add_terminal(address="t50")
    sp1 = w.add_space(name="main")
    w.add_space(name="side")
    w.user_switches_space(on="terminal.keep")
    assert w.app_json(None, ["status"], 30)["space"] == sp1   # 尚未触发
    w.app_json(None, ["do", "terminal.keep", json.dumps({"terminal": "t50"})], 30)
    assert w.app_json(None, ["status"], 30)["space"] != sp1   # keep 后用户接管
    print("21 event: user switches space (immediate or after a command)")

    # 22 外部干扰：pane 落进非选中空间；ls 不暴露标签页 id
    w = FakeHerdr()
    w.add_terminal(address="t50")
    sp1 = w.add_space(name="main")
    sp2 = w.add_space(name="side")
    w.pane_lands_elsewhere()
    w.app_json(None, ["do", "terminal.keep", json.dumps({"terminal": "t50"})], 30)
    ls = w.app_json(None, ["ls"], 30)
    landed = [sp["id"] for sp in ls["spaces"]
              if any(t["terminal"] == "t50" and t["pane"]
                     for tb in sp["tabs"] for t in tb["terminals"])]
    assert landed == [sp2]
    w.hide_tab_ids()
    ls = w.app_json(None, ["ls"], 30)
    assert all(tb["id"] is None for sp in ls["spaces"] for tb in sp["tabs"])
    print("22 events: pane lands elsewhere, tab ids hidden")

    # 23 外部干扰：会话被外部杀掉（finished、reason=killed，名字失解析）
    w = FakeHerdr()
    w.add_terminal(address="t99", name="claude-test")
    w.session_killed_externally("t99")
    g = w.hc_json(None, ["get", "t99"], 30)
    assert g["state"] == "finished" and g["reason"] == "killed"
    assert g["exit_code"] is None   # 被杀无正常退出码
    raises("not_found", lambda: w.hc_json(None, ["get", "claude-test"], 30))
    print("23 event: session killed externally")

    # 24 屏幕序列：read 逐次消费一条屏幕文本，耗尽后重复最后一条（先变后静置可构造）
    w = FakeHerdr()
    w.add_terminal(address="t99")
    w.screen_sequence("t99", ["第一屏", "第二屏", "静置屏"])
    texts = [w.hc_json(None, ["read", "t99", "--lines", "1000"], 30)["text"]
             for _ in range(5)]
    assert texts == ["第一屏", "第二屏", "静置屏", "静置屏", "静置屏"], texts
    rd = w.hc_json(None, ["read", "t99", "--lines", "1000"], 30)
    contract.check("hc.read", rd)   # 返回形状不变、契约自检照过
    print("24 screen sequence: consumed per read, repeats last after exhaustion")

    # 25 turn 缺失/非整数场景（T5a，reset 纪律防御用例需要）：turn=None → get 无 turn 键；
    #    turn=False → 原样返回（契约 optional turn 容 bool，与「id 可空＝缺 id 场景表达」
    #    同一先例：实测 turn 恒整数，bool 是故意摆出的防御场景）。
    w = FakeHerdr()
    w.add_terminal(address="t99", turn=None)
    g = w.hc_json(None, ["get", "t99"], 30)
    contract.check("hc.get", g)
    assert "turn" not in g
    w = FakeHerdr()
    w.add_terminal(address="t99", turn=False)
    g = w.hc_json(None, ["get", "t99"], 30)
    contract.check("hc.get", g)
    assert g["turn"] is False
    print("25 turn absent (None) / non-integer (False) scenarios expressible")

    print("all fake-herdr tests passed")


if __name__ == "__main__":
    main()