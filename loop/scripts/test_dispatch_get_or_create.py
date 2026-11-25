#!/usr/bin/env python3
"""test_dispatch_get_or_create.py — 命名会话 get-or-create 分流测试（ADR-0009，mock hc/herdr-app）。

跑法：python3 loop/scripts/test_dispatch_get_or_create.py
缝：分发脚本 CLI 契约（进程内 import dispatch，hc_json→NamedFakeHc、app_json→FakeApp，
tempdir 跑 main）；断言只认 stdout 一行 JSON、退出码、台账、桩调用日志。
覆盖：
  1 CLI 静态契约：--launch/--cwd 成对（单传 bad_args rc2）；--switch-agent/--exit-command/
    --exit-keys 已移除（argparse 拒）。
  2 创建快乐路径：not_found → hc run --bg --keep --name --cwd -- zsh -l → 打 loop 标签
    → 等 shell → 裸 send 启动命令 → 等 agent 就绪 → 核身份 → 领养 → 主任务；
    result target=名字、target_address=解析 t 号、launched、workspace=adopted。
  2b 创建＋新鲜豁免：reset_skipped、切模型照跑、起始 skill 豁免、三次发送上限。
  3 复活快乐路径（不 run/不重写标签/终端级 turn 保留）；3b 复活新鲜豁免（turn>0 不看）；
    3c 复活 launch_required 同 id 重试。
  4 占位呈报（无标签/标签不符）；4c 重建 idle 占位者（rebuilt＋旧 t 号）；
    4d rebuild_busy（working/blocked/未自报拒杀）；4e --rebuild 误传 bad_args；
    4f rebuild 缺 launch 先报 launch_required；4g kill 后 name_taken 竞态部分状态；
    4h 标签符但前台身份不符→占位呈报＋--rebuild 可重建。
  5 finished→同名创建旧留审计；5b 创建 launch_required 同 id 重试；5c 非法会话名 bad_args；
    5d-5h launch_failed 各部分状态（发送失败/超时/终端死亡/身份不符/标签失败）；
    5i duplicate_release 优先；5j 复活 launch_failed 变体（发送失败/超时/死亡）。
"""
import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dispatch  # noqa: E402
from test_dispatch_reset import FakeApp  # noqa: E402  （第二桩全套件共用）
import os  # noqa: E402
os.environ["LOOP_EXTRACT_BACKEND"] = "pi"
# 测试隔离：学习默认开启（ADR-0012），既有套件统一关闭，不碰真实学习目录。
os.environ["LOOP_LEARNING"] = "off"

PROMPT = "请实现某个功能\n第二行说明"
ANCHOR = PROMPT.splitlines()[0][:24]
SCREEN = ANCHOR + "\n已完成该功能。"
EXT = {"needs_user": False, "questions": [],
       "conclusion": "", "blocked": "", "reply": SCREEN}
LAUNCH = "claude --dangerously-skip-permissions"
NAME = "claude-matt-loop"
DISPLAY = "LoopEng_测试任务"


class NamedFakeHc:
    """herdr 桩：按名字/t 号解析 get；run/label/kill/rm/send 状态机。
    send 启动命令（文本==launch_text）按 launch_effect 转移前台；其余文本 turn+1。
    turn 是终端级累计：启动命令不清零。"""

    def __init__(self, launch_text=LAUNCH):
        self.terminals = {}      # address -> desc
        self.sends = []          # (addr, text, if_idle)
        self.runs = []           # 完整 args
        self.label_calls = []    # (addr, [k=v...])
        self.kills = []
        self.rms = []
        self.gets = []           # get 调用地址日志（#46：断言赠送清理轮询）
        self.rm_error = False    # True＝rm 命令失败（即便 finished）
        self.launch_text = launch_text
        self.launch_effect = "agent"   # agent | stuck_shell | wrong_agent | die | dialog
        self.run_error = None          # 如 "name_taken"
        self.launch_send_error = False
        self.label_error = False
        self.kill_error = False
        self._next = 100

    def add(self, name=None, state="live", fg="agent", app="claude-code",
            st="idle", turn=5, labels=None, cwd="/proj", address=None):
        addr = address or ("t%d" % self._next)
        self._next += 1
        if fg == "shell":
            foreground = {"name": "zsh", "argv": ["/bin/zsh", "-l"]}
            argv = ["/bin/zsh", "-l"]
            app = None
            st = None
        elif fg == "wrong":
            foreground = {"name": "grok", "argv": ["grok"]}
            argv = ["/bin/zsh", "-l"]
            app = "grok"
        else:
            foreground = {"name": "2.1.325", "argv": ["claude", "--dangerously-skip-permissions"]}
            argv = ["/bin/zsh", "-l"]
        d = {"address": addr, "name": name, "state": state, "turn": turn,
             "cwd": cwd, "labels": dict(labels or {}), "argv": argv,
             "foreground": foreground,
             "status": ({"app": app, "state": st} if st else None)}
        self.terminals[addr] = d
        return d

    def resolve(self, addr):
        if addr in self.terminals:
            return self.terminals[addr]
        for d in self.terminals.values():
            if d["state"] == "live" and d["name"] == addr:
                return d
        return None

    def _get_json(self, d):
        return {"address": d["address"], "name": d["name"], "state": d["state"],
                "turn": d["turn"], "cwd": d["cwd"], "labels": dict(d["labels"]),
                "argv": d["argv"], "foreground": d["foreground"],
                "status": d["status"], "title": "test"}

    def __call__(self, hc_bin, args, timeout_s):
        cmd = args[0]
        if cmd == "get":
            self.gets.append(args[1])
            d = self.resolve(args[1])
            if d is None:
                raise dispatch.HcFailure("not_found", "no terminal %s" % args[1],
                                         [], 3, {"error": {"kind": "not_found"}})
            # #46 Bug2 旋钮：tab.close 异步结束终端——延迟 N 次查询才 finished
            if d.get("finish_countdown"):
                d["finish_countdown"] -= 1
                if d["finish_countdown"] <= 0:
                    d.pop("finish_countdown")
                    d["state"] = "finished"
            return self._get_json(d)
        if cmd == "run":
            self.runs.append(list(args))
            if self.run_error:
                raise dispatch.HcFailure(self.run_error, "run refused", [], 1,
                                         {"error": {"kind": self.run_error}})
            name = args[args.index("--name") + 1] if "--name" in args else None
            cwd = args[args.index("--cwd") + 1] if "--cwd" in args else "/proj"
            d = self.add(name=name, fg="shell", turn=0, cwd=cwd)
            return {"address": d["address"], "state": "live", "turn": 0}
        if cmd == "label":
            self.label_calls.append((args[1], list(args[2:])))
            if self.label_error:
                raise dispatch.HcFailure("label_error", "label refused", [], 1, {})
            d = self.resolve(args[1])
            for kv in args[2:]:
                k, _, v = kv.partition("=")
                if v:
                    d["labels"][k] = v
                else:
                    d["labels"].pop(k, None)
            return {"address": args[1]}
        if cmd == "send":
            addr, text = args[-2], args[-1]
            d = self.resolve(addr)
            if d is None:
                raise dispatch.HcFailure("not_found", "no terminal", [], 3, {})
            if text == self.launch_text:
                if self.launch_send_error:
                    raise dispatch.HcFailure("send_error", "launch refused", [], 1, {})
                self.sends.append((d["address"], text, "--if" in args))
                if self.launch_effect == "agent":
                    d["foreground"] = {"name": "2.1.325", "argv": ["claude", ""]}
                    d["status"] = {"app": "claude-code", "state": "idle"}
                elif self.launch_effect == "wrong_agent":
                    d["foreground"] = {"name": "grok", "argv": ["grok"]}
                    d["status"] = {"app": "grok", "state": "idle"}
                elif self.launch_effect == "die":
                    d["state"] = "finished"
                elif self.launch_effect == "dialog":
                    # #46 Bug3 旋钮：agent 起来了但卡登录/授权弹框（blocked），等就绪必失败
                    d["foreground"] = {"name": "2.1.325", "argv": ["claude", ""]}
                    d["status"] = {"app": "claude-code", "state": "blocked",
                                   "kind": "login", "msg": "等待登录授权"}
                # stuck_shell：前台保持 zsh、status 保持 None
                return {"turn": d["turn"]}
            self.sends.append((d["address"], text, "--if" in args))
            baseline = d["turn"]
            d["turn"] = baseline + 1
            if d["status"]:
                d["status"]["state"] = "idle"
            return {"turn": baseline}
        if cmd == "kill":
            self.kills.append(args[1])
            if self.kill_error:
                raise dispatch.HcFailure("kill_error", "kill refused", [], 1, {})
            d = self.resolve(args[1])
            d["state"] = "finished"
            return {"address": d["address"], "state": "finished"}
        if cmd == "rm":
            self.rms.append(args[1])
            d = self.terminals.get(args[1])
            if self.rm_error or (d is not None and d["state"] == "live"):
                # 真实 hc 语义（#46 Bug2）：rm live 终端退出码 1
                raise dispatch.HcFailure("not_finished", "rm refused: terminal live",
                                         [], 1, {"error": {"kind": "not_finished"}})
            if d is None:
                raise dispatch.HcFailure("not_found", "no terminal %s" % args[1],
                                         [], 3, {"error": {"kind": "not_found"}})
            self.terminals.pop(args[1], None)
            return {"address": args[1]}
        if cmd == "read":
            return {"text": SCREEN, "complete": True, "how": "current", "why": ""}
        if cmd == "wait":
            d = self.resolve(args[1])
            st = d["status"]["state"] if d["status"] else None
            return {"reason": "status", "status": st, "turn": d["turn"]}
        raise AssertionError("unexpected hc cmd: " + cmd)


def run_case(fake, app, rid, target=NAME, launch=None, cwd=None, rebuild=False,
             expect_proc=None, reset=None, model=None, prompt=PROMPT,
             pre_ledger=False, wall_clock="5", td=None, display_name="auto",
             space="main"):
    """跑一轮 dispatch；返回 (rc, result, ledger, tmpdir)。td 可传入复用（同 id 重试）。
    display_name="auto"：带 --launch（新鲜路径）时自动补 DISPLAY（ADR-0010 必传），
    既有路径不补；传 None 显式不带（测缺参），传字符串显式指定。
    space 默认 "main"（--space 必传，#43；FakeApp 默认空间名），传 None 显式不带（测 argparse 拒）。"""
    own_td = td is None
    if own_td:
        td = tempfile.mkdtemp()
    pf = Path(td) / "prompt.md"
    pf.write_text(prompt, encoding="utf-8")
    ld = Path(td) / ".loop"
    if pre_ledger:
        (ld / "runs").mkdir(parents=True, exist_ok=True)
        (ld / "runs" / (rid + ".json")).write_text("{}", encoding="utf-8")
    argv = ["--target", target, "--release-id", rid,
            "--prompt-file", str(pf), "--loop-dir", str(ld),
            "--wall-clock", wall_clock, "--check-in", "1",
            "--settle-seconds", "0.01", "--settle-checks", "2",
            "--adopt-timeout", "0.05", "--adopt-poll", "0.001"]
    if launch is not None:
        argv += ["--launch", launch]
    if cwd is not None:
        argv += ["--cwd", cwd]
    if display_name == "auto":
        display_name = DISPLAY if launch is not None else None
    if display_name is not None:
        argv += ["--display-name", display_name]
    if rebuild:
        argv += ["--rebuild"]
    if expect_proc is not None:
        argv += ["--expect-proc", expect_proc]
    if reset is not None:
        argv += ["--reset", reset]
    if model is not None:
        argv += ["--target-model", model]
    if space is not None:
        argv += ["--space", space]
    dispatch.hc_json = fake
    dispatch.app_json = app
    dispatch.extract = lambda pi_bin, args, raw_path: dict(EXT)
    dispatch.check_structure_and_tools = lambda: None
    dispatch.shutil.which = lambda name: "/fake/" + name
    dispatch.time.sleep = lambda s: None
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = dispatch.main(argv)
    out = buf.getvalue().strip()
    result = json.loads(out)
    led_path = ld / "runs" / (rid + ".json")
    ledger = json.loads(led_path.read_text(encoding="utf-8")) if led_path.exists() else None
    if led_path.exists():
        rp = ld / "runs" / (rid + ".result.json")
        assert rp.read_text(encoding="utf-8").strip() == out, "result 文件与 stdout 不一致"
    return rc, result, ledger, td


def main():
    # 1 CLI 静态契约
    base = ["--target", NAME, "--release-id", "r1", "--prompt-file", "p.txt", "--space", "main"]
    a = dispatch.parse_args(base + ["--launch", LAUNCH, "--cwd", "/proj", "--rebuild"])
    assert a.launch == LAUNCH and a.cwd == "/proj" and a.rebuild is True
    for removed in (("--switch-agent", LAUNCH), ("--exit-command", "/exit"),
                    ("--exit-keys", "ctrl+d")):
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                dispatch.parse_args(base + list(removed))
            raise AssertionError("%s 应已被移除" % removed[0])
        except SystemExit as e:
            assert e.code == 2, e.code
    # --launch/--cwd 单传：bad_args rc2、零发送、id 不消耗
    for extra in (["--launch", LAUNCH], ["--cwd", "/proj"]):
        fake = NamedFakeHc()
        app = FakeApp(hc=fake)
        with tempfile.TemporaryDirectory() as td:
            pf = Path(td) / "p.md"
            pf.write_text(PROMPT, encoding="utf-8")
            argv = ["--target", NAME, "--release-id", "static-pair",
                    "--prompt-file", str(pf), "--loop-dir", str(Path(td) / ".loop"),
                    "--space", "main", *extra]
            dispatch.hc_json = fake
            dispatch.app_json = app
            dispatch.check_structure_and_tools = lambda: None
            dispatch.shutil.which = lambda name: "/fake/" + name
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = dispatch.main(argv)
            res = json.loads(buf.getvalue().strip())
            assert rc == 2 and res["error"]["kind"] == "bad_args", res
            assert fake.sends == [] and fake.runs == []
            assert not (Path(td) / ".loop" / "runs" / "static-pair.json").exists()
    print("1 CLI statics: --launch/--cwd paired, switch flags removed, bad_args rc2 ok")

    # 2 创建快乐路径：not_found → run → label → 等 shell → send 启动 → 等 agent → 领养 → 主任务
    fake = NamedFakeHc()
    app = FakeApp(hc=fake)
    rc, res, led, td = run_case(fake, app, "create-happy-1",
                                launch=LAUNCH, cwd="/proj", expect_proc="claude")
    assert rc == 0 and res["ok"], res
    # run 形状：--bg --keep --name --cwd -- zsh -l（单形态 shell 包装）
    assert len(fake.runs) == 1, fake.runs
    run_args = fake.runs[0]
    assert run_args[0] == "run" and "--bg" in run_args and "--keep" in run_args
    assert "--no-parent" in run_args, "命名会话独立于调用方终端（#46 Bug1）"
    assert run_args[run_args.index("--name") + 1] == NAME
    assert run_args[run_args.index("--cwd") + 1] == "/proj"
    assert run_args[-3:] == ["--", "zsh", "-l"], run_args
    # 标签：loop=1＋loop-agent=expect-proc
    assert fake.label_calls and fake.label_calls[0][1] == [
        "loop=1", "loop-agent=claude"], fake.label_calls
    # 发送：启动命令裸 send（shell 不报 idle、不带 --if）＋主任务 --if idle，共两条
    new_addr = [d["address"] for d in fake.terminals.values() if d["name"] == NAME][0]
    assert fake.sends == [(new_addr, LAUNCH, False), (new_addr, PROMPT, True)], fake.sends
    # 领养：status 探测 → ls 核 pane → keep → ls 复核；绝不用裸 herdr-app
    # 显示名落地（ADR-0010，harness 自动注入 DISPLAY）：ls 定位 tab → tab.rename → ls 复核名
    assert [c[0] for c in app.calls] == ["status", "ls", "do", "ls", "ls", "do", "ls"], app.calls
    assert app.calls[2][1] == "terminal.keep"
    assert json.loads(app.calls[2][2]) == {"terminal": new_addr}
    assert app.calls[5][1] == "tab.rename"
    assert all(c for c in app.calls), "不许裸 herdr-app 探针"
    # result 契约：target 恒传入名字、target_address 解析 t 号、launched、workspace
    assert res["target"] == NAME and res["target_address"] == new_addr, res
    assert res["launched"] is True and res["workspace"] == "adopted", res
    assert led["target_address"] == new_addr and led["launched"] is True
    assert led["launch"] == LAUNCH and led["cwd"] == "/proj"
    assert led["phase"] == "done"
    print("2 create happy path: run->label->shell->launch->agent->adopt->main, result fields ok")

    # 2c #40 领养复核轮询：keep 后 pane 延迟落地（pane_delay=3）→ 轮询等到→adopted。
    #    单次 ls（旧行为）会跑在落地前误判 no_pane；轮询须反复 ls 直到 pane 出现。
    fake = NamedFakeHc()
    app = FakeApp(hc=fake, pane_delay=3)
    rc, res, led, td = run_case(fake, app, "adopt-delay-1",
                                launch=LAUNCH, cwd="/proj", expect_proc="claude")
    assert rc == 0 and res["ok"], res
    assert res["workspace"] == "adopted" and led["phase"] == "done", res
    keep_idx = next(i for i, c in enumerate(app.calls)
                    if c[:2] == ["do", "terminal.keep"])
    post_keep_ls = [c for c in app.calls[keep_idx + 1:] if c[0] == "ls"]
    assert len(post_keep_ls) >= 3, app.calls
    print("2c #40 adopt poll: delayed pane (pane_delay=3) -> adopted via polling ok")

    # 2d #40 窗口耗尽：keep 真失败（keep_no_pane，pane 永不落地）→ 轮询窗口耗尽→
    #    adopt_failed/no_pane，部分状态不变（终端已建 launched、workspace failed）。
    fake = NamedFakeHc()
    app = FakeApp(hc=fake, keep_no_pane=True)
    rc, res, led, td = run_case(fake, app, "adopt-no-pane-1",
                                launch=LAUNCH, cwd="/proj", expect_proc="claude")
    assert rc == 1 and res["error"]["kind"] == "adopt_failed", res
    assert res["adopt_reason"] == "no_pane", res
    assert res["workspace"] == "failed" and res["launched"] is True, res
    assert led["phase"] == "error:adopt_failed"
    print("2d #40 adopt poll: keep no_pane -> window exhausted -> adopt_failed/no_pane ok")

    # 2b 创建＋新鲜豁免：--reset clear 被跳过（reset_skipped）、切模型照跑、起始 skill 豁免
    fake = NamedFakeHc()
    app = FakeApp(hc=fake)
    rc, res, led, td = run_case(fake, app, "create-fresh-1", launch=LAUNCH, cwd="/proj",
                                expect_proc="claude", reset="clear", model="opus",
                                prompt="/wayfinder #7")
    assert rc == 0 and res["ok"], res
    new_addr = [d["address"] for d in fake.terminals.values() if d["name"] == NAME][0]
    assert fake.sends == [(new_addr, LAUNCH, False), (new_addr, "/model opus", True),
                          (new_addr, "/wayfinder #7", True)], fake.sends
    assert res["reset_skipped"] == "fresh_launch" and res["reset"] == "clear", res
    assert led["reset_skipped"] == "fresh_launch" and "reset_send" not in led
    assert len(fake.sends) == 3, "一轮至多发送成功三次（启动＋切模型＋主任务）"
    print("2b create fresh exemption: reset skipped, model runs, start-skill exempt, 3-send cap ok")

    # 3 复活快乐路径：live＋空闲 shell＋loop 标签符 → 原地重发启动命令；不 run、不重写标签、
    #   名字/cwd/终端级历史不动（turn 保留）；result 无 launched（终端不是本轮建的）
    fake = NamedFakeHc()
    old = fake.add(name=NAME, fg="shell", turn=7,
                   labels={"loop": "1", "loop-agent": "claude"}, address="t50")
    app = FakeApp(hc=fake, panes=("t50",))
    rc, res, led, td = run_case(fake, app, "revive-happy-1",
                                launch=LAUNCH, cwd="/proj", expect_proc="claude")
    assert rc == 0 and res["ok"], res
    assert fake.runs == [] and fake.label_calls == [] and fake.kills == [], "复活不 run/不重写标签/不 kill"
    assert fake.sends == [("t50", LAUNCH, False), ("t50", PROMPT, True)], fake.sends
    assert res["target"] == NAME and res["target_address"] == "t50", res
    assert "launched" not in res and res["turn"] == 8, res
    assert old["cwd"] == "/proj" and old["turn"] == 8
    assert led["phase"] == "done"
    print("3 revive happy path: in-place launch send, no run/label/kill, terminal turn kept ok")

    # 3b 复活＋新鲜豁免：turn=7（终端级累计）但会话空 → 起始 skill 视同 turn 0 豁免、
    #    --reset clear 跳过、切模型照跑
    fake = NamedFakeHc()
    fake.add(name=NAME, fg="shell", turn=7,
             labels={"loop": "1", "loop-agent": "claude"}, address="t50")
    app = FakeApp(hc=fake, panes=("t50",))
    rc, res, led, td = run_case(fake, app, "revive-fresh-1", launch=LAUNCH, cwd="/proj",
                                expect_proc="claude", reset="clear", model="opus",
                                prompt="/implement #38")
    assert rc == 0 and res["ok"], res
    assert fake.sends == [("t50", LAUNCH, False), ("t50", "/model opus", True),
                          ("t50", "/implement #38", True)], fake.sends
    assert res["reset_skipped"] == "fresh_launch", res
    print("3b revive fresh exemption: turn>0 ignored, reset skipped, model runs ok")

    # 3c 复活缺 --launch：launch_required rc2、零发送、id 不消耗；补参同 id 重试成功
    fake = NamedFakeHc()
    fake.add(name=NAME, fg="shell", turn=7,
             labels={"loop": "1", "loop-agent": "claude"}, address="t50")
    app = FakeApp(hc=fake, panes=("t50",))
    td = tempfile.mkdtemp()
    rc, res, led, _ = run_case(fake, app, "revive-req-1", expect_proc="claude", td=td)
    assert rc == 2 and res["error"]["kind"] == "launch_required", res
    assert fake.sends == [] and led is None, "零发送、id 不消耗"
    rc, res, led, _ = run_case(fake, app, "revive-req-1", launch=LAUNCH, cwd="/proj",
                               expect_proc="claude", td=td)
    assert rc == 0 and res["ok"], res
    assert fake.sends == [("t50", LAUNCH, False), ("t50", PROMPT, True)], fake.sends
    print("3c revive launch_required: rc2 id unconsumed, same-id retry with params ok")

    # 4 占位：live＋无 loop 标签 → session_occupied 停轮呈报（地址/前台/turn/状态），不建不 kill
    fake = NamedFakeHc()
    fake.add(name=NAME, fg="agent", app="grok", st="idle", turn=3,
             labels={}, address="t60")
    fake.terminals["t60"]["foreground"] = {"name": "grok", "argv": ["grok"]}
    app = FakeApp(hc=fake, panes=("t60",))
    rc, res, led, td = run_case(fake, app, "occ-nolabel-1", expect_proc="claude")
    assert rc == 1 and res["error"]["kind"] == "session_occupied", res
    msg = res["error"]["message"]
    assert "t60" in msg and "grok" in msg and "turn=3" in msg and "idle" in msg, msg
    assert fake.sends == [] and fake.runs == [] and fake.kills == [] and led is None
    print("4 occupied (no loop label): session_occupied reports address/fg/turn/state, no side effects ok")

    # 4b 占位：loop 标签符不符（loop-agent=grok ≠ expect-proc claude）→ session_occupied
    fake = NamedFakeHc()
    fake.add(name=NAME, fg="agent", turn=3,
             labels={"loop": "1", "loop-agent": "grok"}, address="t60")
    app = FakeApp(hc=fake, panes=("t60",))
    rc, res, led, td = run_case(fake, app, "occ-label-1", expect_proc="claude")
    assert rc == 1 and res["error"]["kind"] == "session_occupied", res
    assert fake.sends == [] and fake.runs == [] and fake.kills == []
    print("4b occupied (label mismatch): session_occupied ok")

    # 4c 强制重建 idle 占位者：kill 旧→同名创建→打标签→启动→领养→主任务；
    #    result rebuilt:true＋旧 t 号；台账 rebuild phase
    fake = NamedFakeHc()
    fake.add(name=NAME, fg="agent", app="grok", st="idle", turn=3,
             labels={}, address="t60")
    app = FakeApp(hc=fake)
    rc, res, led, td = run_case(fake, app, "rebuild-ok-1", launch=LAUNCH, cwd="/proj",
                                expect_proc="claude", rebuild=True)
    assert rc == 0 and res["ok"], res
    assert fake.kills == [NAME], fake.kills
    assert len(fake.runs) == 1 and fake.runs[0][fake.runs[0].index("--name") + 1] == NAME
    new_addr = [d["address"] for d in fake.terminals.values()
                if d["name"] == NAME and d["state"] == "live"][0]
    assert res["rebuilt"] is True and res["rebuilt_old_target"] == "t60", res
    assert res["target_address"] == new_addr and res["launched"] is True, res
    assert res["target"] == NAME and res["workspace"] == "adopted"
    assert led["rebuilt"] is True and led["rebuilt_old_target"] == "t60"
    assert fake.sends[-1] == (new_addr, PROMPT, True), fake.sends
    print("4c rebuild idle occupant: kill->create->launch->adopt->main, rebuilt+old t reported ok")

    # 4d rebuild 拒杀 working/blocked/未自报：rebuild_busy，零 kill、id 不消耗
    for st in ("working", "blocked", None):
        fake = NamedFakeHc()
        d = fake.add(name=NAME, fg="agent", app="grok", st=st or "idle", turn=3,
                     labels={}, address="t60")
        if st is None:
            d["status"] = None
            d["foreground"] = {"name": "zsh", "argv": ["/bin/zsh", "-l"]}
        app = FakeApp(hc=fake, panes=("t60",))
        rc, res, led, td = run_case(fake, app, "rebuild-busy-%s" % st,
                                    launch=LAUNCH, cwd="/proj", rebuild=True)
        assert rc == 1 and res["error"]["kind"] == "rebuild_busy", (st, res)
        assert fake.kills == [] and fake.sends == [] and led is None, st
    print("4d rebuild working/blocked/unreported: rebuild_busy, never kill, id unconsumed ok")

    # 4e --rebuild 误传（非占位分流）：bad_args rc2、零发送、id 不消耗
    for scenario in ("existing", "create", "revive"):
        fake = NamedFakeHc()
        if scenario == "existing":
            fake.add(name=NAME, fg="agent", turn=5,
                     labels={"loop": "1", "loop-agent": "claude"}, address="t50")
        elif scenario == "revive":
            fake.add(name=NAME, fg="shell", turn=7,
                     labels={"loop": "1", "loop-agent": "claude"}, address="t50")
        app = FakeApp(hc=fake, panes=("t50",))
        rc, res, led, td = run_case(fake, app, "rebuild-misuse-%s" % scenario,
                                    launch=LAUNCH, cwd="/proj", expect_proc="claude",
                                    rebuild=True)
        assert rc == 2 and res["error"]["kind"] == "bad_args", (scenario, res)
        assert fake.sends == [] and fake.kills == [] and fake.runs == [] and led is None
    print("4e --rebuild misuse on existing/create/revive: bad_args rc2, id unconsumed ok")

    # 4f rebuild 缺 --launch：launch_required 先于 rebuild_busy（预检顺序）
    fake = NamedFakeHc()
    fake.add(name=NAME, fg="agent", app="grok", st="working", turn=3,
             labels={}, address="t60")
    app = FakeApp(hc=fake, panes=("t60",))
    rc, res, led, td = run_case(fake, app, "rebuild-nolaunch-1", rebuild=True)
    assert rc == 2 and res["error"]["kind"] == "launch_required", res
    assert fake.kills == [] and led is None
    print("4f rebuild without --launch: launch_required before rebuild_busy ok")

    # 4g kill 后创建失败（name_taken 竞态）：launch_failed 带部分状态（旧会话已死、新会话未成）
    fake = NamedFakeHc()
    fake.add(name=NAME, fg="agent", app="grok", st="idle", turn=3,
             labels={}, address="t60")
    fake.run_error = "name_taken"
    app = FakeApp(hc=fake)
    rc, res, led, td = run_case(fake, app, "rebuild-race-1", launch=LAUNCH, cwd="/proj",
                                rebuild=True)
    assert rc == 1 and res["error"]["kind"] == "launch_failed", res
    assert "name_taken" in res["error"]["message"], res
    assert fake.kills == [NAME] and len(fake.runs) == 1
    assert res["rebuilt"] is True and res["rebuilt_old_target"] == "t60", res
    assert "launched" not in res, "新会话未成"
    assert led["phase"] == "error:launch_failed" and led["rebuilt"] is True
    print("4g kill-then-create name_taken race: launch_failed with rebuilt partial state ok")

    # 4h 占位：loop 标签符但前台是别的 agent（身份不符）→ session_occupied 呈报；
    #    --rebuild 可重建（kill idle 占位者→同名创建），不被误拒 bad_args
    fake = NamedFakeHc()
    d = fake.add(name=NAME, fg="agent", turn=3,
                 labels={"loop": "1", "loop-agent": "claude"}, address="t60")
    d["foreground"] = {"name": "grok", "argv": ["grok"]}
    d["status"] = {"app": "grok", "state": "idle"}
    app = FakeApp(hc=fake, panes=("t60",))
    rc, res, led, td = run_case(fake, app, "occ-fg-1", expect_proc="claude")
    assert rc == 1 and res["error"]["kind"] == "session_occupied", res
    assert "t60" in res["error"]["message"] and "grok" in res["error"]["message"]
    assert fake.kills == [] and fake.sends == [] and led is None
    rc, res, led, td = run_case(fake, app, "occ-fg-rebuild", expect_proc="claude",
                                launch=LAUNCH, cwd="/proj", rebuild=True)
    assert rc == 0 and res["ok"], res
    assert fake.kills == [NAME] and res["rebuilt"] is True and res["launched"] is True
    print("4h occupied (labeled, foreign foreground): reported; --rebuild works ok")

    # 5 finished→同名创建：旧条目按 t 号留审计，名字解析到新 live
    fake = NamedFakeHc()
    fake.add(name=NAME, state="finished", fg="shell", turn=18, address="t40")
    app = FakeApp(hc=fake)
    rc, res, led, td = run_case(fake, app, "finished-create-1",
                                launch=LAUNCH, cwd="/proj", expect_proc="claude")
    assert rc == 0 and res["ok"], res
    assert fake.terminals["t40"]["state"] == "finished", "旧条目留审计"
    new_addr = res["target_address"]
    assert new_addr != "t40" and res["launched"] is True
    assert fake.sends == [(new_addr, LAUNCH, False), (new_addr, PROMPT, True)]
    print("5 finished -> same-name create: old entry kept for audit, name resolves to new ok")

    # 5b 创建缺 --launch：launch_required rc2、id 不消耗；补参同 id 重试
    fake = NamedFakeHc()
    app = FakeApp(hc=fake)
    td = tempfile.mkdtemp()
    rc, res, led, _ = run_case(fake, app, "create-req-1", td=td)
    assert rc == 2 and res["error"]["kind"] == "launch_required", res
    assert fake.runs == [] and led is None
    rc, res, led, _ = run_case(fake, app, "create-req-1", launch=LAUNCH, cwd="/proj", td=td)
    assert rc == 0 and res["ok"] and res["launched"] is True, res
    print("5b create launch_required: rc2 id unconsumed, same-id retry ok")

    # 5c 非法会话名创建：bad_args rc2（t 号保留、大写非法），id 不消耗
    for bad in ("t99", "Bad_Name"):
        fake = NamedFakeHc()
        app = FakeApp(hc=fake)
        rc, res, led, td = run_case(fake, app, "badname-%s" % bad, target=bad,
                                    launch=LAUNCH, cwd="/proj")
        assert rc == 2 and res["error"]["kind"] == "bad_args", (bad, res)
        assert fake.runs == [] and led is None
    print("5c invalid session name for create: bad_args rc2, id unconsumed ok")

    # 5d 启动命令发送失败：launch_failed，终端已建（launched:true）、启动未发
    fake = NamedFakeHc()
    fake.launch_send_error = True
    app = FakeApp(hc=fake)
    rc, res, led, td = run_case(fake, app, "launch-senderr-1",
                                launch=LAUNCH, cwd="/proj", expect_proc="claude")
    assert rc == 1 and res["error"]["kind"] == "launch_failed", res
    assert res["launched"] is True and "launch_sent" not in res, res
    assert led["phase"] == "error:launch_failed"
    print("5d launch send failure: launch_failed with launched partial state ok")

    # 5e 等 agent 就绪超时（前台 stuck shell）：launch_failed，启动已发
    fake = NamedFakeHc()
    fake.launch_effect = "stuck_shell"
    app = FakeApp(hc=fake)
    rc, res, led, td = run_case(fake, app, "launch-stuck-1", launch=LAUNCH, cwd="/proj",
                                expect_proc="claude", wall_clock="0.3")
    assert rc == 1 and res["error"]["kind"] == "launch_failed", res
    assert res["launched"] is True and res["launch_sent"] is True, res
    print("5e agent-ready timeout: launch_failed, launch_sent partial state ok")

    # 5f agent 启动期间终端死亡：launch_failed
    fake = NamedFakeHc()
    fake.launch_effect = "die"
    app = FakeApp(hc=fake)
    rc, res, led, td = run_case(fake, app, "launch-die-1",
                                launch=LAUNCH, cwd="/proj", expect_proc="claude")
    assert rc == 1 and res["error"]["kind"] == "launch_failed", res
    assert res["launch_sent"] is True
    print("5f terminal dies during agent start: launch_failed ok")

    # 5g 新 agent 身份不符：identity_mismatch（创建与复活同）
    for scenario in ("create", "revive"):
        fake = NamedFakeHc()
        fake.launch_effect = "wrong_agent"
        if scenario == "revive":
            fake.add(name=NAME, fg="shell", turn=7,
                     labels={"loop": "1", "loop-agent": "claude"}, address="t50")
        app = FakeApp(hc=fake, panes=("t50",))
        rc, res, led, td = run_case(fake, app, "launch-wrong-%s" % scenario,
                                    launch=LAUNCH, cwd="/proj", expect_proc="claude")
        assert rc == 1 and res["error"]["kind"] == "identity_mismatch", (scenario, res)
        assert res["launch_sent"] is True
    print("5g new agent identity mismatch (create/revive): identity_mismatch ok")

    # 5h 打标签失败：launch_failed，终端已建
    fake = NamedFakeHc()
    fake.label_error = True
    app = FakeApp(hc=fake)
    rc, res, led, td = run_case(fake, app, "launch-labelerr-1",
                                launch=LAUNCH, cwd="/proj", expect_proc="claude")
    assert rc == 1 and res["error"]["kind"] == "launch_failed", res
    assert res["launched"] is True and "launch_sent" not in res
    print("5h label failure: launch_failed with launched partial state ok")

    # 5i duplicate_release 优先于一切新 kind（含 launch_required）
    fake = NamedFakeHc()
    app = FakeApp(hc=fake)
    rc, res, led, td = run_case(fake, app, "dup-priority-1", pre_ledger=True)
    assert rc == 1 and res["error"]["kind"] == "duplicate_release", res
    assert fake.runs == [] and fake.sends == []
    print("5i duplicate_release takes precedence over launch_required ok")

    # 5j 复活失败变体：launch_failed 部分状态（发送失败/等就绪超时/终端死亡）；
    #    复活不建终端，无 launched 字段
    for effect in ("send_error", "stuck_shell", "die"):
        fake = NamedFakeHc()
        fake.add(name=NAME, fg="shell", turn=7,
                 labels={"loop": "1", "loop-agent": "claude"}, address="t50")
        if effect == "send_error":
            fake.launch_send_error = True
        else:
            fake.launch_effect = effect
        app = FakeApp(hc=fake, panes=("t50",))
        wc = "0.3" if effect == "stuck_shell" else "5"
        rc, res, led, td = run_case(fake, app, "revive-fail-%s" % effect,
                                    launch=LAUNCH, cwd="/proj", expect_proc="claude",
                                    wall_clock=wc)
        assert rc == 1 and res["error"]["kind"] == "launch_failed", (effect, res)
        assert "launched" not in res, "复活不建终端"
        if effect != "send_error":
            assert res["launch_sent"] is True, effect
        assert led["phase"] == "error:launch_failed"
    print("5j revive launch_failed variants: send error / timeout / death partial state ok")

    print("ALL GET-OR-CREATE TESTS PASSED")


if __name__ == "__main__":
    main()