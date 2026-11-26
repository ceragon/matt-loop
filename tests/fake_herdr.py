"""fake_herdr.py — 一个有主的假世界（T3，ADR-0016 决定 5；ADR-0018 修订后入口为稳定资产）。

FakeHerdr 持有终端、空间、标签页、窗格四类领域状态，对外只暴露 .hc_json 与
.app_json 两个命令层入口，与 Deps 的两个命令层字段一一对应。领域状态只经
场景方法构造、经入口读写；返回前过契约一致性检查（contract.check）。

场景靠方法构造；故障与延迟共用「命令、模式、参数」三元组旋钮（命令名必须来自
真实命令词汇表，拼错在声明阶段抛错）；外部干扰是命名世界事件方法。
构造器里不再有一排布尔。

fake 说 dict、不说 argv/stdout；错误映射由 test_command_parse.py 守住。
"""
import copy
import json
from pathlib import Path

import contract
from harness import dispatch

# 命令词汇表（故障键守卫用）：fixtures 里的实测最小集。
_FIXTURES = Path(__file__).resolve().parent / "fixtures"
HC_COMMANDS = tuple(json.loads((_FIXTURES / "hc_commands.json").read_text(encoding="utf-8")))
_APP_VOCAB = json.loads((_FIXTURES / "app_commands.json").read_text(encoding="utf-8"))
APP_COMMANDS = tuple(_APP_VOCAB["subcommands"]) + tuple(_APP_VOCAB["do_commands"])
VOCABULARY = frozenset(HC_COMMANDS) | frozenset(APP_COMMANDS)

_CREATED_MS = 1795599201904   # 脱敏样例里的固定时间戳（不读真实时钟）


class FakeHerdr:
    def __init__(self):
        # 领域状态：终端（hc 权威事实）、空间/标签页/窗格（herdr-app 可见性树）。
        self._terminals = {}          # address -> Terminal dict（完整形状）
        self._spaces = []             # [{id,name,folder,tabs:[{id,name,terminals:[{terminal,pane}]}]}]
        self._selected = None         # 当前选中空间 id
        self.sends = []              # (addr, text, if_idle) 发送日志
        self.hc_calls = []           # 每次 hc 调用的完整 args
        self.app_calls = []          # 每次 app 调用的完整 args
        self._screens = {}           # address -> read 屏幕文本
        self._screen_seq = {}        # address -> 待消费屏幕序列（read 逐次取一条）
        self._launch = None          # (启动命令文本, 效果)
        self._fails = []             # 故障旋钮声明（命令, 模式, 参数）
        self._delays = []            # 延迟旋钮声明（同形）
        self._pending_panes = {}     # address -> 落地倒计时（每次 ls 减一）
        self._finish_countdown = {}  # address -> finished 倒计时（每次 get 减一）
        self._stale_status = None    # {"remaining": N, "value": 旧选中}
        self._closed = False         # 事件：app 没开着
        self._hide_tab_ids = False   # 事件：ls 不暴露标签页 id
        self._land_elsewhere = False # 事件：pane 落进非选中空间
        self._switch_on = None       # 事件：某条命令后用户切走空间
        self._next_addr = 100
        self._next_space = 1
        self._next_tab = 1

    # ---- 场景构造方法 ----

    def add_terminal(self, address=None, *, name=None, state="live", fg="agent",
                     app="claude-code", status="idle", turn=5, labels=None,
                     cwd="/proj", title="test"):
        """造一个终端：既有（live agent）/已死（finished）/空闲 shell（fg=shell）。
        fg=wrong 造身份不符的前台。返回地址。"""
        addr = address or ("t%d" % self._next_addr)
        self._next_addr += 1
        d = {"address": addr, "argv": ["/bin/zsh", "-l"], "cols": 80,
             "created_ms": _CREATED_MS, "cwd": cwd, "die_with_parent": False,
             "exit_code": None, "finished_ms": None, "identity": "ghostty",
             "keep": True, "labels": dict(labels or {}), "name": name,
             "notify": True, "parent": None, "pid": 22409, "reason": None,
             "reported": state == "live" and fg != "shell", "rm": False,
             "rows": 24, "screen": "exact", "signal": None, "state": state,
             "status": None, "status_ms": _CREATED_MS, "template": None,
             "title": title, "turn": turn}
        if state == "finished":
            d["exit_code"] = 0
            d["reason"] = "exited"
            d["finished_ms"] = _CREATED_MS + 1000
            d["title"] = ""
        elif fg == "shell":
            d["foreground"] = {"name": "zsh", "argv": ["/bin/zsh", "-l"],
                               "cwd": cwd, "pid": 22409}
        elif fg == "wrong":
            d["foreground"] = {"name": "grok", "argv": ["grok"], "pid": 22410}
            d["status"] = {"app": "grok", "state": status}
        else:
            d["foreground"] = {"name": "2.1.331",
                               "argv": ["claude", "--dangerously-skip-permissions"],
                               "cwd": cwd, "pid": 22410}
            d["status"] = {"app": app, "state": status}
        self._terminals[addr] = d
        return addr

    def set_status(self, address, state, **extra):
        """场景方法：改终端自报状态（idle/working/done/blocked/error）。"""
        d = self._terminals[address]
        if d["status"] is None:
            d["status"] = {"app": "claude-code"}
        d["status"]["state"] = state
        d["status"].update(extra)

    def screen(self, address, text):
        """场景方法：摆 read 回读的屏幕文本。"""
        self._screens[address] = text

    def screen_sequence(self, address, texts):
        """场景方法：摆一串屏幕文本，read 逐次消费一条；序列耗尽后重复最后一条。
        使「先变后静置」可在 CLI 缝构造（前置落定复核：屏幕一直变→未落定，静置→落定）。
        返回形状不变（仍是一条 ReadResult），契约自检照过。"""
        if not texts:
            raise ValueError("屏幕序列不能为空")
        self._screen_seq[address] = list(texts)

    def will_launch(self, text, effect="agent"):
        """场景方法：shell 收到启动命令文本后的世界反应。
        effect：agent（起来）/stuck_shell（启动中：命令已发、前台仍是 shell、
        agent 未就绪）/wrong_agent（起错）/die（终端死）/dialog（卡登录授权弹框）。"""
        self._launch = (text, effect)

    def add_space(self, id=None, *, name=None, folder=None):
        """造一个空间；无选中空间时第一个空间被显示（真实 app 语义）。返回 id。"""
        sp_id = id or ("sp%d" % self._next_space)
        self._next_space += 1
        self._spaces.append({"id": sp_id, "name": name, "folder": folder, "tabs": []})
        if self._selected is None:
            self._selected = sp_id
        return sp_id

    def add_tab(self, space_id, *, id=None, name=None, terminals=(), panes=True):
        """在空间里造一个标签页，挂终端条目；panes=False 造无窗格条目。返回标签页 id。"""
        tb_id = id or ("tb%d" % self._next_tab)
        self._next_tab += 1
        sp = next(s for s in self._spaces if s["id"] == space_id)
        sp["tabs"].append({
            "id": tb_id, "name": name,
            "terminals": [{"terminal": a, "pane": ("p-" + a) if panes else None}
                          for a in terminals]})
        return tb_id

    # ---- 旋钮：故障与延迟共用「命令、模式、参数」三元组 ----

    # noop（命令成功但效果不落地）只对写命令有意义；只读命令声明即报错。
    _NOOP_OK = frozenset({"send", "label", "kill", "rm", "terminal.keep",
                          "space.select", "space.new", "tab.close", "tab.rename"})
    # 延迟组合白名单：land_after＝效果在第 N 次观察时落地；vanish＝条目直接被收走。
    _DELAY_OK = frozenset({("terminal.keep", "land_after"),
                           ("tab.close", "land_after"), ("tab.close", "vanish"),
                           ("space.select", "land_after")})

    def fail(self, command, mode="error", *, kind=None, nth=None, message=None):
        """故障旋钮：某条命令按模式失败。error＝按 kind 抛错（nth 定位第几次，
        1 起，缺省每次）；noop＝命令成功但效果不落地。命令名拼错当场抛错。"""
        self._guard_key(command)
        if mode not in ("error", "noop"):
            raise ValueError("未知故障模式：%r（只有 error/noop）" % mode)
        if mode == "noop" and command not in self._NOOP_OK:
            raise ValueError("命令 %r 无 noop 语义（只读命令效果即返回）" % command)
        self._fails.append({"command": command, "mode": mode, "kind": kind,
                            "nth": nth, "message": message, "n": 0})

    def delay(self, command, mode, *, after=1):
        """延迟旋钮：某条命令的效果慢下来。land_after＝第 after 次观察才落地
        （keep 数 ls、tab.close 数 get、space.select 数 status）；vanish＝条目被收走。"""
        self._guard_key(command)
        if (command, mode) not in self._DELAY_OK:
            raise ValueError("不适用的延迟组合：(%r, %r)" % (command, mode))
        self._delays.append({"command": command, "mode": mode, "after": after})

    # 假世界实现的命令面（dispatch 实际调用的 8 个 hc 子命令＋3 个 app 子命令＋5 个 do）。
    _SUPPORTED = frozenset({"get", "run", "label", "send", "wait", "read", "kill", "rm",
                            "status", "ls", "space.new", "space.select",
                            "terminal.keep", "tab.close", "tab.rename"})

    def _guard_key(self, command):
        # 故障键守卫（ADR-0018 决定 12）：命令名必须来自真实命令词汇表，
        # 拼错在声明阶段报错，不静默不触发。
        if command not in VOCABULARY:
            raise ValueError("故障键命令名 %r 不在真实命令词汇表（tests/fixtures）" % command)
        # 真实存在但假世界未实现的命令同样在声明阶段拒绝：旋钮挂上去也不会生效。
        if command not in self._SUPPORTED:
            raise ValueError("假世界未实现命令 %r，旋钮无法生效" % command)

    def _fault(self, command, side):
        """error 模式当场抛错；返回本次调用是否 noop。"""
        noop = False
        for k in self._fails:
            if k["command"] != command:
                continue
            k["n"] += 1
            if k["nth"] is not None and k["n"] != k["nth"]:
                continue
            if k["mode"] == "error":
                msg = k["message"] or ("%s refused（故障旋钮）" % command)
                if side == "hc":
                    raise self._hc_fail(k["kind"] or "unavailable", msg)
                raise self._app_fail(msg)
            noop = True
        return noop

    def _delay_after(self, command, mode):
        for d in self._delays:
            if d["command"] == command and d["mode"] == mode:
                return d["after"]
        return None

    # ---- 外部干扰：命名世界事件（方法触发，不是旋钮里的字符串） ----

    def app_closed(self):
        """herdr-app 没开着：一切 app 命令失败（错误信封为假设形状，README 标注）。"""
        self._closed = True

    def user_switches_space(self, *, on=None):
        """用户切走选中空间。on=None 立即切；on=<命令名> 挂在那条命令完成之后
        （还原竞态护栏的触发条件：落地窗口内用户接管）。"""
        if on is None:
            self._switch_space()
        else:
            self._guard_key(on)
            self._switch_on = on

    def pane_lands_elsewhere(self):
        """下一次 keep 的 pane 落进非选中空间（异步落地前选中被切走）。"""
        self._land_elsewhere = True

    def hide_tab_ids(self):
        """ls 不暴露标签页 id（显示名定位不到的场景）。"""
        self._hide_tab_ids = True

    def session_killed_externally(self, address):
        """会话被外部杀掉：finished、reason=killed、无正常退出码，名字失解析。"""
        self._finish(self._terminals[address], reason="killed", exit_code=None)

    def _switch_space(self):
        other = next((sp for sp in self._spaces if sp["id"] != self._selected), None)
        if other is not None:
            self._selected = other["id"]

    # ---- 内部 ----

    def _resolve(self, addr):
        if addr in self._terminals:
            return self._terminals[addr]
        for d in self._terminals.values():
            if d["state"] == "live" and d["name"] == addr:
                return d
        return None

    def _hc_fail(self, kind, message, returncode=1):
        return dispatch.HcFailure(kind, message, [], returncode,
                                  {"error": {"kind": kind, "message": message}})

    def _require(self, addr):
        """按地址或 live 名字解析；解析不到即 not_found（实测退出码 3）。"""
        d = self._resolve(addr)
        if d is None:
            raise self._hc_fail("not_found", "no terminal %s" % addr, 3)
        return d

    def _terminal_json(self, d):
        return copy.deepcopy(d)

    # ---- 命令层入口一：hc ----

    def hc_json(self, hc_bin, args, timeout_s):
        self.hc_calls.append(list(args))
        cmd = args[0]
        noop = self._fault(cmd, "hc")
        if cmd == "get":
            d = self._require(args[1])
            self._tick_finish(d)
            ret = self._terminal_json(d)
        elif cmd == "send":
            ret = self._send(args, noop)
        elif cmd == "run":
            name = args[args.index("--name") + 1] if "--name" in args else None
            cwd = args[args.index("--cwd") + 1] if "--cwd" in args else "/proj"
            if name is not None and any(
                    d["state"] == "live" and d["name"] == name
                    for d in self._terminals.values()):
                raise self._hc_fail("name_taken", "name already taken: %s" % name)
            addr = self.add_terminal(name=name, fg="shell", turn=0, cwd=cwd)
            ret = self._terminal_json(self._terminals[addr])
            if noop:
                self._terminals.pop(addr)
        elif cmd == "label":
            d = self._require(args[1])
            if not noop:
                for kv in args[2:]:
                    k, _, v = kv.partition("=")
                    if v:
                        d["labels"][k] = v
                    else:
                        d["labels"].pop(k, None)
            ret = self._terminal_json(d)
        elif cmd == "kill":
            d = self._require(args[1])
            if not noop:
                self._finish(d, reason="killed", exit_code=None)
            ret = self._terminal_json(d)
        elif cmd == "rm":
            d = self._terminals.get(args[1])
            if d is None:
                raise self._hc_fail("not_found", "no terminal %s" % args[1], 3)
            if d["state"] == "live":
                # 真实 hc 语义（schema）：rm live 终端拒绝，kind=live
                raise self._hc_fail("live", "rm refused: terminal live")
            if not noop:
                self._terminals.pop(args[1])
            ret = {"removed": args[1]}
        elif cmd == "wait":
            d = self._require(args[1])
            st = d["status"]["state"] if d["status"] else None
            ret = {"terminal": d["address"], "ended": d["state"] != "live",
                   "reason": "status", "status": st, "turn": d["turn"],
                   "children": []}
        elif cmd == "read":
            d = self._require(args[1])
            seq = self._screen_seq.get(d["address"])
            if seq is not None:
                text = seq.pop(0) if len(seq) > 1 else seq[0]
            else:
                text = self._screens.get(d["address"], "")
            ret = {"text": text, "complete": True, "how": "screen", "why": None}
        else:
            raise AssertionError("unexpected hc cmd: " + cmd)
        contract.check("hc." + cmd, ret)
        return ret

    def _send(self, args, noop=False):
        addr, text = args[-2], args[-1]
        if_idle = "--if" in args
        d = self._require(addr)
        if d["state"] != "live":
            raise self._hc_fail("finished", "terminal finished: %s" % addr)
        st = d["status"]["state"] if d["status"] else None
        if if_idle and st != "idle":
            raise self._hc_fail("status_mismatch",
                                "send --if idle refused: state=%s" % st)
        self.sends.append((d["address"], text, if_idle))
        if noop:
            return {"turn": d["turn"]}
        if self._launch is not None and text == self._launch[0]:
            self._apply_launch(d, self._launch[1])
            return {"turn": d["turn"]}
        baseline = d["turn"]
        d["turn"] = baseline + 1
        if d["status"] is not None:
            d["status"]["state"] = "idle"
        return {"turn": baseline}

    def _finish(self, d, reason="exited", exit_code=0):
        d["state"] = "finished"
        d["reason"] = reason
        d["exit_code"] = exit_code
        d["finished_ms"] = _CREATED_MS + 4000
        d.pop("foreground", None)
        d["status"] = None

    def _tick_finish(self, d):
        """tab.close 异步结束终端（#46 Bug2）：第 N 次 get 才 finished。"""
        n = self._finish_countdown.get(d["address"])
        if n is None:
            return
        n -= 1
        if n <= 0:
            del self._finish_countdown[d["address"]]
            self._finish(d)
        else:
            self._finish_countdown[d["address"]] = n

    def _apply_launch(self, d, effect):
        if effect == "agent":
            d["foreground"] = {"name": "2.1.331", "argv": ["claude", ""]}
            d["status"] = {"app": "claude-code", "state": "idle"}
        elif effect == "wrong_agent":
            d["foreground"] = {"name": "grok", "argv": ["grok"]}
            d["status"] = {"app": "grok", "state": "idle"}
        elif effect == "die":
            self._finish(d, exit_code=1)
        elif effect == "dialog":
            d["foreground"] = {"name": "2.1.331", "argv": ["claude", ""]}
            d["status"] = {"app": "claude-code", "state": "blocked",
                           "kind": "auth", "msg": "等待登录授权"}
        # stuck_shell：前台保持 shell、status 保持 None

    # ---- 命令层入口二：herdr-app ----

    def _app_fail(self, message):
        # 真实 app_json 把一切非零退出统一映射 app_failed（错误信封 error 是字符串）
        return dispatch.AppFailure("app_failed", "herdr-app 退出码 1：" + message,
                                   [], 1, {"error": message})

    def _ls(self):
        spaces = []
        for sp in self._spaces:
            tabs = []
            for tb in sp["tabs"]:
                entries = []
                for t in tb["terminals"]:
                    d = self._terminals.get(t["terminal"])
                    entries.append({
                        "terminal": t["terminal"], "pane": t["pane"],
                        "status": (d["status"]["state"]
                                   if d and d["status"] else None),
                        "app": d["status"].get("app") if d and d["status"] else None,
                        "name": d["name"] if d else None,
                        "sub_agents": []})
                tabs.append({"id": None if self._hide_tab_ids else tb["id"],
                             "name": tb["name"],
                             "active": True, "settled": False, "terminals": entries})
            spaces.append({"id": sp["id"], "name": sp["name"], "folder": sp["folder"],
                           "machine": None, "active": True, "tabs": tabs})
        return {"spaces": spaces, "recent": []}

    def app_json(self, app_bin, args, timeout_s):
        self.app_calls.append(list(args))
        if self._closed:
            raise self._app_fail("not running")
        knob_key = args[1] if args[0] == "do" else args[0]
        noop = self._fault(knob_key, "app")
        if args[0] == "status":
            space = self._selected
            if self._stale_status is not None:
                # select 已生效但复核抖动：第 N 次 status 才报新选中
                self._stale_status["remaining"] -= 1
                if self._stale_status["remaining"] <= 0:
                    self._stale_status = None
                else:
                    space = self._stale_status["value"]
            ret = {"zoom": 1.0, "font_size": 14.0, "ui_font_size": 13.0,
                   "space": space, "tab": None, "focused_pane": None,
                   "layout": "main", "agent": None,
                   "floats": {}, "overlays": [], "errors": []}
            key = "app.status"
        elif args[0] == "ls":
            self._tick_panes()
            ret = self._ls()
            key = "app.ls"
        elif args[0] == "do":
            name, payload = args[1], json.loads(args[2])
            ret = self._do(name, payload, noop)
            key = "app.do." + name
        else:
            raise AssertionError("unexpected app cmd: " + repr(args))
        if self._switch_on == knob_key:
            self._switch_on = None
            self._switch_space()
        contract.check(key, ret)
        return ret

    def _tick_panes(self):
        """keep 异步落地（#40）：第 N 次 ls 才把 pane 挂上。"""
        for addr, n in list(self._pending_panes.items()):
            n -= 1
            if n <= 0:
                del self._pending_panes[addr]
                for sp in self._spaces:
                    for tb in sp["tabs"]:
                        for t in tb["terminals"]:
                            if t["terminal"] == addr and t["pane"] is None:
                                t["pane"] = "p-" + addr
            else:
                self._pending_panes[addr] = n

    def _do(self, name, payload, noop=False):
        if name == "terminal.keep":
            addr = payload["terminal"]
            if self._spaces and not noop:
                # keep 落进当前选中空间（#43 实证语义），不是恒第一个；
                # pane_lands_elsewhere 事件模拟异步落地前选中被切走
                if self._land_elsewhere:
                    cur = next((sp for sp in self._spaces if sp["id"] != self._selected),
                               self._spaces[0])
                else:
                    cur = next((sp for sp in self._spaces if sp["id"] == self._selected),
                               self._spaces[0])
                after = self._delay_after("terminal.keep", "land_after")
                cur["tabs"].append({
                    "id": "tb-keep%d" % self._next_tab, "name": None,
                    "terminals": [{"terminal": addr,
                                   "pane": None if after else "p-" + addr}]})
                self._next_tab += 1
                if after:
                    self._pending_panes[addr] = after
            return {"pane": "p-kept", "terminal": addr}   # 假设形状（README 标注）
        if name == "space.select":
            sp = next((s for s in self._spaces if s["id"] == payload["id"]), None)
            if sp is None:
                raise self._app_fail("unknown space %s" % payload["id"])
            if not noop:
                after = self._delay_after("space.select", "land_after")
                if after:
                    self._stale_status = {"remaining": after, "value": self._selected}
                self._selected = sp["id"]
            return {}
        if name == "space.new":
            if noop:
                # 命令成功但空间没建：返回假设形状的 id，世界里查无此空间
                sp_id = "sp%d" % self._next_space
                self._next_space += 1
                return {"space": sp_id, "tab": "tb-gift", "terminal": "t-gift"}
            sp_id = self.add_space(name=payload.get("name"),
                                   folder=payload.get("folder"))
            gifted = self.add_terminal(fg="shell", turn=0)
            tb_id = self.add_tab(sp_id, terminals=[gifted])
            return {"space": sp_id, "tab": tb_id, "terminal": gifted}
        if name == "tab.close":
            if noop:
                return {}
            vanish = self._delay_after("tab.close", "vanish") is not None
            after = self._delay_after("tab.close", "land_after")
            for sp in self._spaces:
                for tb in list(sp["tabs"]):
                    if tb["id"] == payload["id"]:
                        for t in tb["terminals"]:
                            d = self._terminals.get(t["terminal"])
                            if d is None or d["state"] != "live":
                                continue
                            if vanish:
                                # 条目直接被收走（get/rm 均 not_found）
                                self._terminals.pop(t["terminal"])
                            elif after:
                                self._finish_countdown[t["terminal"]] = after
                            else:
                                self._finish(d)
                        sp["tabs"].remove(tb)
            return {}
        if name == "tab.rename":
            if not noop:
                for sp in self._spaces:
                    for tb in sp["tabs"]:
                        if tb["id"] == payload["id"]:
                            tb["name"] = payload["name"]
            return {}
        raise AssertionError("unexpected do command: " + name)