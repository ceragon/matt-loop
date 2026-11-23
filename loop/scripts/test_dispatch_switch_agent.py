#!/usr/bin/env python3
"""test_dispatch_switch_agent.py — 换 kind 前置流程测试（mock hc，不打真实 agent）。

跑法：python3.12 loop/scripts/test_dispatch_switch_agent.py
覆盖（ADR-0007）：
   1 shell 包装同地址换（--exit-command）：退旧→回 shell→send 启动命令→主任务；地址不变、无 target_new。
   2 shell 包装同地址换（--exit-keys）：退出走 hc keys；sends 只有启动命令＋主任务。
   3 shell 包装空闲 shell（前台已是 shell、无 agent）：跳过退出，直接 send 启动命令。
   4 直接起 agent 终端：kill＋run 新终端；target_new=新地址、target=原地址、switch_form=direct。
   5 互斥：--switch-agent ＋ --reset → bad_args（rc2，id 不消耗）。
   6 互斥：--switch-agent ＋ --target-model → bad_args（rc2）。
   7 --exit-command/--exit-keys 不带 --switch-agent → bad_args（rc2）。
   8 --exit-command ＋ --exit-keys 同传 → argparse 互斥报错（rc2）。
   9 shell 包装有 agent 在跑但没传退出旗标 → switch_exit_required。
  10 shell 包装退旧后终端 finished（地址丢失）→ switch_recovery_failed，不静默换地址。
  11 新 agent 身份不符 --expect-proc → identity_mismatch。
  12 会话起始 skill＋换 kind：纪律预检跳过（新会话 turn 0），照跑。
  13 向后兼容：不传 --switch-agent，只发主任务一条，结果无换 kind 字段。
  14 duplicate_release 优先于换 kind。
"""
import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dispatch  # noqa: E402
import os  # noqa: E402
os.environ["LOOP_EXTRACT_BACKEND"] = "pi"

PROMPT = "请实现某个功能\n第二行说明"
ANCHOR = PROMPT.splitlines()[0][:24]
SCREEN = ANCHOR + "\n已完成该功能。"
EXT = {"needs_user": False, "questions": [],
       "conclusion": "", "blocked": "", "reply": SCREEN}
LAUNCH = "claude --dangerously-skip-permissions"


class SwitchFakeHc:
    """模拟换 kind 的终端状态转移。form=shell（持久 zsh 包装）或 direct（直接起 agy）。
    send 按文本内容识别退出命令/启动命令/主任务并转移前台；kill→finished；run→新终端 t100。"""

    def __init__(self, form="shell", exit_text=None, launch_text=LAUNCH,
                 new_kind="claude", old_turn=5, idle_shell=False,
                 shell_finish=False, identity_ok=True):
        self.form = form
        self.exit_text = exit_text
        self.launch_text = launch_text
        self.new_kind = new_kind
        self.sends = []          # (addr, text, if_idle)
        self.keys = []           # (addr, key)
        self.kills = []
        self.runs = []
        self.shell_finish = shell_finish
        self.identity_ok = identity_ok
        self.new_addr = "t100"
        self.new = None
        if form == "shell":
            fg = {"name": "zsh", "argv": ["/bin/zsh", "-l"]} if idle_shell \
                else {"name": "node", "argv": ["pi", ""]}
            self.old = {"argv": ["/bin/zsh", "-l"], "fg": fg,
                        "app": None if idle_shell else "pi",
                        "turn": 0 if idle_shell else old_turn,
                        "state": "live", "st": "idle", "cwd": "/proj"}
        else:
            self.old = {"argv": ["agy", "--dangerously-skip-permissions"],
                        "fg": {"name": "agy", "argv": ["agy"]}, "app": "agy",
                        "turn": old_turn, "state": "live", "st": "idle", "cwd": "/proj"}

    def _desc(self, addr):
        if addr == self.new_addr and self.new is not None:
            return self.new
        return self.old

    def _get(self, addr):
        d = self._desc(addr)
        status = {"state": d["st"]}
        if d["app"]:
            status["app"] = d["app"]
        return {"state": d["state"], "turn": d["turn"], "cwd": d["cwd"],
                "argv": d["argv"], "foreground": d["fg"], "status": status, "title": "test"}

    def _to_shell(self, d):
        d["fg"] = {"name": "zsh", "argv": ["/bin/zsh", "-l"]}
        d["app"] = None
        d["st"] = "idle"
        if self.shell_finish:
            d["state"] = "finished"

    def __call__(self, hc_bin, args, timeout_s):
        cmd = args[0]
        if cmd == "get":
            return self._get(args[1])
        if cmd == "send":
            addr, text = args[-2], args[-1]
            self.sends.append((addr, text, "--if" in args))
            d = self._desc(addr)
            if self.form == "shell":
                if self.exit_text is not None and text == self.exit_text:
                    self._to_shell(d)
                    return {"turn": d["turn"]}
                if text == self.launch_text:
                    nk = self.new_kind if self.identity_ok else "wrongagent"
                    d["fg"] = {"name": "node", "argv": [nk, ""]}
                    d["app"] = nk
                    d["turn"] = 0
                    d["st"] = "idle"
                    return {"turn": 0}
            baseline = d["turn"]
            d["turn"] += 1
            return {"turn": baseline}
        if cmd == "keys":
            addr, key = args[1], args[2]
            self.keys.append((addr, key))
            d = self._desc(addr)
            if self.form == "shell":
                self._to_shell(d)
            return {"status": "idle", "turn": d["turn"]}
        if cmd == "kill":
            self.kills.append(args[1])
            self._desc(args[1])["state"] = "finished"
            return {"address": args[1], "state": "finished"}
        if cmd == "run":
            self.runs.append(list(args))
            nk = self.new_kind if self.identity_ok else "wrongagent"
            self.new = {"argv": ["sh", "-c", self.launch_text],
                        "fg": {"name": "node", "argv": [nk, ""]}, "app": nk,
                        "turn": 0, "state": "live", "st": "idle", "cwd": "/proj"}
            return {"address": self.new_addr, "agent": nk, "state": "live", "turn": 0}
        if cmd == "read":
            return {"text": SCREEN, "complete": True, "how": "current", "why": ""}
        if cmd == "wait":
            d = self._desc(args[1])
            return {"reason": "status", "status": d["st"], "turn": d["turn"]}
        raise AssertionError("unexpected hc cmd: " + cmd)


def run_case(fake, rid, switch=LAUNCH, exit_command=None, exit_keys=None,
             expect_proc="claude", reset=None, model=None, prompt=PROMPT,
             pre_ledger=False, target="t99"):
    with tempfile.TemporaryDirectory() as td:
        pf = Path(td) / "prompt.md"
        pf.write_text(prompt, encoding="utf-8")
        ld = Path(td) / ".loop"
        if pre_ledger:
            (ld / "runs").mkdir(parents=True)
            (ld / "runs" / (rid + ".json")).write_text("{}", encoding="utf-8")
        argv = ["--target", target, "--release-id", rid,
                "--prompt-file", str(pf), "--loop-dir", str(ld),
                "--wall-clock", "5", "--check-in", "1",
                "--settle-seconds", "0.01", "--settle-checks", "2"]
        if switch is not None:
            argv += ["--switch-agent", switch]
        if exit_command is not None:
            argv += ["--exit-command", exit_command]
        if exit_keys is not None:
            argv += ["--exit-keys", exit_keys]
        if expect_proc is not None:
            argv += ["--expect-proc", expect_proc]
        if reset is not None:
            argv += ["--reset", reset]
        if model is not None:
            argv += ["--target-model", model]
        dispatch.hc_json = fake
        dispatch.extract = lambda pi_bin, args, raw_path: dict(EXT)
        dispatch.check_structure_and_tools = lambda: None
        dispatch.shutil.which = lambda name: "/fake/" + name
        dispatch.time.sleep = lambda s: None
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = dispatch.main(argv)
        out = buf.getvalue().strip()
        result = json.loads(out) if out else None
        led_path = ld / "runs" / (rid + ".json")
        ledger = json.loads(led_path.read_text(encoding="utf-8")) if led_path.exists() else None
        return rc, result, ledger


def main():
    # 1 shell 包装同地址换（--exit-command）
    fake = SwitchFakeHc(form="shell", exit_text="/exit")
    rc, res, led = run_case(fake, "sw-shell-cmd-1", exit_command="/exit")
    assert rc == 0 and res["ok"], res
    assert fake.sends == [("t99", "/exit", True), ("t99", LAUNCH, False),
                          ("t99", PROMPT, True)], fake.sends
    assert res["target"] == "t99" and "target_new" not in res, res
    assert res["switch_form"] == "shell" and res["switch_agent"] == LAUNCH
    assert led["switch_form"] == "shell" and led.get("switch_done_at"), led
    assert fake.kills == [] and fake.runs == [], "同地址换不应 kill/run"
    print("1 shell-wrapper same-address (--exit-command): exit->shell->launch->main, address kept ok")

    # 2 shell 包装同地址换（--exit-keys）
    fake = SwitchFakeHc(form="shell")
    rc, res, led = run_case(fake, "sw-shell-keys-1", exit_keys="ctrl+d")
    assert rc == 0 and res["ok"], res
    assert fake.keys == [("t99", "ctrl+d")], fake.keys
    assert fake.sends == [("t99", LAUNCH, False), ("t99", PROMPT, True)], fake.sends
    assert res["switch_form"] == "shell" and "target_new" not in res
    print("2 shell-wrapper same-address (--exit-keys): exit via hc keys, launch+main sends ok")

    # 3 shell 包装空闲 shell（无 agent）：跳过退出
    fake = SwitchFakeHc(form="shell", idle_shell=True)
    rc, res, led = run_case(fake, "sw-shell-idle-1")
    assert rc == 0 and res["ok"], res
    assert fake.sends == [("t99", LAUNCH, False), ("t99", PROMPT, True)], fake.sends
    assert fake.keys == [] and res["switch_form"] == "shell"
    print("3 shell-wrapper idle shell (no agent): skip exit, launch+main ok")

    # 4 直接起 agent 终端：kill＋run 新地址
    fake = SwitchFakeHc(form="direct")
    rc, res, led = run_case(fake, "sw-direct-1")
    assert rc == 0 and res["ok"], res
    assert fake.kills == ["t99"], fake.kills
    assert len(fake.runs) == 1 and fake.runs[0][-3:] == ["sh", "-c", LAUNCH], fake.runs
    assert fake.sends == [("t100", PROMPT, True)], fake.sends
    assert res["target"] == "t99" and res["target_new"] == "t100", res
    assert res["switch_form"] == "direct" and led["target_new"] == "t100"
    print("4 direct terminal: kill+run new address, target_new reported, main to new ok")

    # 5 互斥：--switch-agent ＋ --reset
    fake = SwitchFakeHc(form="shell", exit_text="/exit")
    rc, res, led = run_case(fake, "sw-mutex-reset", exit_command="/exit", reset="clear")
    assert rc == 2 and not res["ok"], res
    assert res["error"]["kind"] == "bad_args", res
    assert fake.sends == [] and led is None, "静态旗标错误应在写台账前，id 不消耗"
    print("5 mutex --switch-agent + --reset: bad_args rc2, id unconsumed ok")

    # 6 互斥：--switch-agent ＋ --target-model
    fake = SwitchFakeHc(form="shell", exit_text="/exit")
    rc, res, led = run_case(fake, "sw-mutex-model", exit_command="/exit", model="sonnet")
    assert rc == 2 and not res["ok"], res
    assert res["error"]["kind"] == "bad_args", res
    assert led is None
    print("6 mutex --switch-agent + --target-model: bad_args rc2 ok")

    # 7 退出旗标不带 --switch-agent
    fake = SwitchFakeHc(form="direct")
    rc, res, led = run_case(fake, "sw-exit-no-switch", switch=None,
                            exit_command="/exit", expect_proc="agy")
    assert rc == 2 and not res["ok"], res
    assert res["error"]["kind"] == "bad_args", res
    assert led is None
    print("7 exit flags without --switch-agent: bad_args rc2 ok")

    # 8 --exit-command ＋ --exit-keys 同传：argparse 互斥
    fake = SwitchFakeHc(form="shell")
    with tempfile.TemporaryDirectory() as td:
        pf = Path(td) / "p.md"
        pf.write_text(PROMPT, encoding="utf-8")
        argv = ["--target", "t99", "--release-id", "sw-exit-mutex",
                "--prompt-file", str(pf), "--switch-agent", LAUNCH,
                "--exit-command", "/exit", "--exit-keys", "ctrl+d"]
        dispatch.hc_json = fake
        buf = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            try:
                dispatch.parse_args(argv)
                raise AssertionError("argparse should reject exit-command+exit-keys")
            except SystemExit as e:
                assert e.code == 2, e.code
    print("8 --exit-command + --exit-keys: argparse mutual exclusion rc2 ok")

    # 9 shell 包装有 agent 但没传退出旗标
    fake = SwitchFakeHc(form="shell")
    rc, res, led = run_case(fake, "sw-no-exit")
    assert rc == 1 and not res["ok"], res
    assert res["error"]["kind"] == "switch_exit_required", res
    assert fake.sends == [], fake.sends
    print("9 shell-wrapper agent running, no exit flag: switch_exit_required ok")

    # 10 shell 包装退旧后终端 finished（地址丢失）
    fake = SwitchFakeHc(form="shell", exit_text="/exit", shell_finish=True)
    rc, res, led = run_case(fake, "sw-recovery-fail", exit_command="/exit")
    assert rc == 1 and not res["ok"], res
    assert res["error"]["kind"] == "switch_recovery_failed", res
    assert "target_new" not in res, "绝不静默换地址"
    assert fake.runs == [], "shell 分支失败不应自动 run 新终端"
    print("10 shell-wrapper terminal finished after exit: switch_recovery_failed, no silent new address ok")

    # 11 新 agent 身份不符
    fake = SwitchFakeHc(form="shell", exit_text="/exit", identity_ok=False)
    rc, res, led = run_case(fake, "sw-identity", exit_command="/exit", expect_proc="claude")
    assert rc == 1 and not res["ok"], res
    assert res["error"]["kind"] == "identity_mismatch", res
    print("11 new agent identity mismatch --expect-proc: identity_mismatch ok")

    # 12 会话起始 skill＋换 kind：纪律预检跳过
    fake = SwitchFakeHc(form="shell", exit_text="/exit", old_turn=5)
    rc, res, led = run_case(fake, "sw-discipline", exit_command="/exit",
                            prompt="/wayfinder #7\n请绘图")
    assert rc == 0 and res["ok"], res
    assert fake.sends[-1] == ("t99", "/wayfinder #7\n请绘图", True), fake.sends
    print("12 start-skill + switch: discipline skipped (fresh turn 0), runs ok")

    # 13 向后兼容：不传 --switch-agent
    fake = SwitchFakeHc(form="direct")
    rc, res, led = run_case(fake, "sw-compat", switch=None, expect_proc="agy")
    assert rc == 0 and res["ok"], res
    assert fake.sends == [("t99", PROMPT, True)], fake.sends
    assert "switch_agent" not in res and "switch_form" not in res and "target_new" not in res
    assert fake.kills == [] and fake.runs == []
    print("13 backward-compat (no --switch-agent): single main send, no switch fields ok")

    # 14 duplicate_release 优先于换 kind
    fake = SwitchFakeHc(form="shell", exit_text="/exit")
    rc, res, led = run_case(fake, "sw-dup", exit_command="/exit", pre_ledger=True)
    assert rc == 1 and not res["ok"], res
    assert res["error"]["kind"] == "duplicate_release", res
    assert fake.sends == []
    print("14 duplicate_release takes precedence over switch ok")

    print("all switch-agent tests passed")


if __name__ == "__main__":
    main()
