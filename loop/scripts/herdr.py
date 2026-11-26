#!/usr/bin/env python3
"""herdr.py — herdr 领域网关（G1，ADR-0018）。

对外只暴露 `Herdr(deps)`：命令字面量、返回解析、落地轮询复核与错误映射全收在
本模块内。seam 不动——生产走 `Deps.hc_json` / `Deps.app_json`，测试仍用
`FakeHerdr` 的命令级入口（ADR-0018 对 ADR-0016 的修订）。

依赖方向：dispatch → herdr → errors；本模块不 import 分发脚本，也不收
ledger / ctx / path 参数（决定 7）。网关只返回领域事实、失败抛 `HerdrFailure`；
外部 kind 的映射（launch_failed / adopt_failed / keep_error / app_closed 等）
全留管线（决定 9）。
"""
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

# 与分发脚本同目录；直跑/经 importlib 加载时都能导入兄弟模块。
sys.path.insert(0, str(Path(__file__).resolve().parent))
from errors import DispatchError  # noqa: E402

# 命令护栏（与 dispatch.py 的 SEND_GUARD_S / QUERY_GUARD_S 同值；网关不 import
# 分发脚本，避免反向依赖）。语义见 dispatch.py 常量注释。
SEND_GUARD_S = 120.0
QUERY_GUARD_S = 30.0
WAIT_GUARD_MARGIN_S = 30.0
READ_LINES = 1000
# keep / 赠送清理的默认窗口与轮询间隔（与 --adopt-timeout/--adopt-poll 默认值同）。
POLL_TIMEOUT_S = 30.0
POLL_INTERVAL_S = 2.0

# 持久 shell 的进程名（与 dispatch.fg_is_shell 同判据，网关不 import 分发脚本）。
SHELL_NAMES = frozenset({"sh", "bash", "zsh", "nu", "fish", "dash", "ksh", "tcsh", "csh"})


# ---------------------------------------------------------------- 失败模型


class HerdrFailure(DispatchError):
    """网关失败：kind / returncode / hints 与底层进程层失败逐字一致。

    op：网关操作名；source：hc / app；native_kind：hc 原生 error kind（穿透时
    与 kind 相同，进程层失败为 None）；stage：领域阶段名，供管线映射外部 kind；
    partial_facts：失败时已确认的部分事实（成功返回事实的镜像）。
    """

    def __init__(self, kind, message, hints=None, returncode=1, *, op, source,
                 native_kind=None, stage=None, detail=None, partial_facts=None):
        super().__init__(kind, message, hints, returncode)
        self.op = op
        self.source = source
        self.native_kind = native_kind
        self.stage = stage
        self.detail = detail
        self.partial_facts = dict(partial_facts or {})


class NotFound(HerdrFailure):
    """目标不存在（hc 原生 not_found 穿透）。调用方据此分支，不返回 None。"""


class StatusMismatch(HerdrFailure):
    """状态或复核结果不符（--if idle 被拒、select/rename 复核不符）。"""


class Blocked(HerdrFailure):
    """目标停在弹框态（hc 原生 blocked 穿透）。"""


_NATIVE_SUBCLASSES = {
    "not_found": NotFound,
    "status_mismatch": StatusMismatch,
    "blocked": Blocked,
}

# 进程层/输出层失败没有 hc 原生 error kind；native_kind 留空，kind 仍逐字保留。
_PROCESS_KINDS = frozenset({"missing_tool", "tool_timeout", "hc_output",
                            "hc_failed", "app_failed", "app_output"})


# ---------------------------------------------------------------- 读模型


@dataclass(frozen=True)
class Session:
    """一次 hc get / run / label / kill 的领域投影；raw 供台账逐字记录。"""

    address: str | None
    state: str | None
    status: dict | None
    turn: int | None
    foreground: dict | None
    labels: dict
    raw: dict

    @classmethod
    def from_raw(cls, raw):
        raw = raw if isinstance(raw, dict) else {}
        status = raw.get("status") if isinstance(raw.get("status"), dict) else None
        foreground = (raw.get("foreground")
                      if isinstance(raw.get("foreground"), dict) else None)
        labels = raw.get("labels") if isinstance(raw.get("labels"), dict) else {}
        turn = raw.get("turn")
        if not isinstance(turn, int) or isinstance(turn, bool):
            turn = None
        return cls(address=raw.get("address"), state=raw.get("state"),
                   status=status, turn=turn, foreground=foreground,
                   labels=dict(labels), raw=raw)

    @property
    def live(self):
        return self.state == "live"

    @property
    def finished(self):
        return self.state == "finished"

    @property
    def status_state(self):
        return self.status.get("state") if self.status else None

    def is_shell(self):
        return _fg_is_shell(self.foreground or {})

    def identity_ok(self, expect):
        """身份判据（ADR-0009）：只认 foreground.argv[0] 与 status.app。
        expect 为空恒真。"""
        if not expect:
            return True
        fg = self.foreground or {}
        st = self.status or {}
        argv = fg.get("argv") or []
        first = argv[0] if isinstance(argv, list) and argv else ""
        return any(expect in c for c in (first, st.get("app") or ""))


@dataclass(frozen=True)
class Placement:
    """终端在 app 树里的落点：空间 / 标签页 / pane。pane 为空不算 placement。"""

    address: str | None
    pane: str | None
    space: dict
    tab: dict
    raw: dict

    @property
    def space_id(self):
        return self.space.get("id")

    @property
    def space_name(self):
        return self.space.get("name")

    @property
    def tab_id(self):
        return self.tab.get("id")

    @property
    def tab_name(self):
        return self.tab.get("name")


@dataclass(frozen=True)
class Layout:
    """一次 ls 的整棵 app 树解析（四份遍历归一）；raw 供台账逐字记录。"""

    raw: dict
    _entries: tuple = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        object.__setattr__(self, "_entries", tuple(_iter_entries(self.raw)))

    def placement(self, address):
        """终端条目可见且 pane 非空时给落点；pane:null 与条目隐藏都返回 None。"""
        for space, tab, entry in self._entries:
            if entry.get("terminal") == address and entry.get("pane"):
                return Placement(address=address, pane=entry.get("pane"),
                                 space=space, tab=tab, raw=entry)
        return None


@dataclass(frozen=True)
class Receipt:
    """send/resend 的回执：resent 表示本操作是否执行了补发；send 在「被拒且复核
    为 done」时给出 first_refusal（resend 回执不重复该事实）。"""

    turn: int | None
    resent: bool
    first_refusal: dict | None
    raw: dict


@dataclass(frozen=True)
class Change:
    """一次 hc wait 的唤醒事实（快照；不解释等待策略）。"""

    terminal: str | None
    ended: bool | None
    reason: str | None
    status: str | None
    turn: int | None
    children: list
    raw: dict

    @classmethod
    def from_raw(cls, raw):
        raw = raw if isinstance(raw, dict) else {}
        return cls(terminal=raw.get("terminal"), ended=raw.get("ended"),
                   reason=raw.get("reason"), status=raw.get("status"),
                   turn=raw.get("turn"), children=list(raw.get("children") or []),
                   raw=raw)


@dataclass(frozen=True)
class Readback:
    """一次 hc read 的文本与完整原始结果（台账要记 complete/how/why）。"""

    text: str
    raw: dict


def _iter_entries(raw):
    """按空间→标签页→终端条目遍历 app 树（LAYOUT 的唯一遍历实现）。"""
    for space in (raw or {}).get("spaces") or []:
        if not isinstance(space, dict):
            continue
        for tab in space.get("tabs") or []:
            if not isinstance(tab, dict):
                continue
            for entry in tab.get("terminals") or []:
                if isinstance(entry, dict):
                    yield space, tab, entry


def _fg_is_shell(fg):
    argv = fg.get("argv") or []
    first = argv[0] if isinstance(argv, list) and argv else ""
    return (_proc_base(fg.get("name")) in SHELL_NAMES
            or _proc_base(first) in SHELL_NAMES)


def _proc_base(name):
    return (name or "").rsplit("/", 1)[-1].lstrip("-")


def _find_tab(raw, address):
    """ls 里终端所在、且有 id 的标签页条目；定位不到返回 None。"""
    for _space, tab, entry in _iter_entries(raw):
        if entry.get("terminal") == address and tab.get("id"):
            return tab
    return None


def _find_space(raw, space_id):
    for space in (raw or {}).get("spaces") or []:
        if isinstance(space, dict) and space.get("id") == space_id:
            return space
    return None


# ---------------------------------------------------------------- 焦点上下文管理器


class Focus:
    """`with herdr.focus(space) as f:` 的记号：进入 select 并核对，退出恒还原。

    结局写在 `f.restore`：restored / user_switched / not_needed / failed；
    failed 时 `f.warning` 带 reason（restore_error / restore_mismatch）与 detail。
    用户已切走不抢回，异常退出也还原（ADR-0018 决定 6）。
    """

    def __init__(self, gateway, space):
        self.gateway = gateway
        self.space = space
        self.original = None
        self.restore = None
        self.warning = None
        self._entered = False

    def __enter__(self):
        self.original = self.gateway.selected_space()
        if self.original == self.space:
            self._entered = True
            return self
        try:
            actual = self.gateway._select_and_verify(self.space, op="focus")
        except BaseException:
            # 进入路径任何失败（含 select 成功后的复核读失败）都先按护栏还原再抛：
            # `__exit__` 不会运行，不还原就把用户留在目标空间，违背「恒还原」。
            self.gateway._restore_space(self.original, self.space)
            raise
        if actual != self.space:
            # 不符先还原再抛（best effort；还原结局不覆盖进入失败本身）。
            self.gateway._restore_space(self.original, self.space)
            raise StatusMismatch(
                "status_mismatch",
                "space.select 复核选中=%r ≠ 目标 %r" % (actual, self.space),
                [], op="focus", source="app", stage="focus_select_mismatch",
                partial_facts={"actual": actual, "target": self.space})
        self._entered = True
        return self

    def __exit__(self, exc_type, exc, tb):
        if not self._entered:
            return False
        self.restore, self.warning = self.gateway._restore_space(
            self.original, self.space)
        return False


# ---------------------------------------------------------------- 网关


class Herdr:
    """herdr 领域网关：会话簇（hc）＋空间簇（herdr-app）＋预检。"""

    def __init__(self, deps):
        self.deps = deps

    # ---- 预检（缺失直抛 missing_tool，不经 HerdrFailure） ----

    def require_hc(self):
        hc_bin = self.deps.locate("hc")
        if not hc_bin:
            raise DispatchError(
                "missing_tool", "找不到 hc",
                ["确认 PATH；脚本也会探 ~/.local/bin、homebrew、nvm node bin"])
        self.deps.hc_bin = hc_bin
        return hc_bin

    def require_app(self):
        app_bin = self.deps.locate("herdr-app")
        if not app_bin:
            raise DispatchError(
                "missing_tool", "PATH 与常见目录里都找不到 herdr-app",
                ["恒可见不变量要求 herdr-app；装上后重试"])
        self.deps.app_bin = app_bin
        return app_bin

    # ---- 会话簇 ----

    def session(self, target):
        """hc get；找不到抛 NotFound，不返回 None。"""
        return self._get(target, op="session")

    def create_session(self, name, cwd):
        raw = self._hc(
            ["run", "--bg", "--keep", "--no-parent", "--name", name,
             "--cwd", cwd, "--", "zsh", "-l"],
            SEND_GUARD_S, op="create_session")
        session = Session.from_raw(raw)
        if not session.address:
            raise HerdrFailure(
                "missing_address", "hc run 未返回新终端地址", [str(raw)[:200]],
                op="create_session", source="hc", stage="missing_address",
                partial_facts={"raw": raw})
        return session

    def label_session(self, target, labels):
        """labels 是 `k=v` 序列（与分发脚本既有调用同形：`["loop=1", …]`）。"""
        return Session.from_raw(
            self._hc(["label", target, *labels], QUERY_GUARD_S, op="label_session"))

    def send(self, target, text, guard="idle"):
        """首发 --if idle；被拒且 hc get 复核为 done 时只返回拒绝回执，不在本操作内补发。

        guard=None/False 表示裸发送（启动命令等命令文本）。回执：成功时
        first_refusal=None；被拒且复核为 done 时 first_refusal 非空、raw 为空，
        调用方落账 phase=resending 后再调 resend（台账 phase 边界，见 ADR-0018 补记）。
        """
        if not guard:
            raw = self._hc(["send", "--", target, text], SEND_GUARD_S, op="send")
            return Receipt(turn=raw.get("turn"), resent=False,
                           first_refusal=None, raw=raw)
        try:
            raw = self._hc(["send", "--if", "idle", "--", target, text],
                           SEND_GUARD_S, op="send", stage="send")
            return Receipt(turn=raw.get("turn"), resent=False,
                           first_refusal=None, raw=raw)
        except HerdrFailure as first:
            if first.kind != "status_mismatch":
                raise
            refusal = {"kind": first.kind, "returncode": first.returncode,
                       "message": first.message}
            g = self._get(target, op="send", stage="refused_not_done",
                          partial={"first_refusal": refusal})
            if g.status_state != "done":
                raise StatusMismatch(
                    "status_mismatch",
                    "hc send --if idle 被拒，复核状态是 %s 而非 done，不补发"
                    % (g.status_state or "未自报"),
                    list(first.hints), first.returncode, op="send", source="hc",
                    native_kind=first.native_kind, stage="refused_not_done",
                    detail=first.detail,
                    partial_facts={"first_refusal": refusal,
                                   "status": g.status_state}) from first
            return Receipt(turn=None, resent=False, first_refusal=refusal, raw={})

    def resend(self, target, text):
        """补发：第二次 hc get 复核（须 live 且 idle/done）后裸 send。

        调用方在 send 与 resend 之间落账 phase=resending 与 first_refusal；
        本操作只做复核＋发送，不碰台账（ADR-0018 决定 7/8 补记）。
        """
        g = self._get(target, op="send", stage="refused_before_resend")
        if not g.live or g.status_state not in ("idle", "done"):
            raise StatusMismatch(
                "status_mismatch",
                "补发前复核状态是 %s，不补发" % (g.status_state or "未自报"),
                ["目标可能已重新开始工作或停在弹框；本轮未再发送"],
                op="send", source="hc", stage="refused_before_resend",
                partial_facts={"status": g.status_state})
        raw = self._hc(["send", "--", target, text], SEND_GUARD_S,
                       op="send", stage="resend")
        return Receipt(turn=raw.get("turn"), resent=True,
                       first_refusal=None, raw=raw)

    def wait_change(self, target, timeout):
        """hc wait 原语；等目标 agent 行为的循环留在管线（决定 11）。"""
        raw = self._hc(["wait", target, "--timeout", "%.1f" % timeout],
                       timeout + WAIT_GUARD_MARGIN_S, op="wait_change")
        return Change.from_raw(raw)

    def read(self, target, lines=READ_LINES):
        """回读文本；缺 text 键按输出层失败抛 HerdrFailure（不静默返回 None）。"""
        return self.readback(target, lines).text

    def readback(self, target, lines=READ_LINES):
        """回读的文本＋完整原始结果（台账记 complete/how/why 用）。"""
        raw = self._hc(["read", target, "--lines", str(lines)],
                       QUERY_GUARD_S, op="read")
        text = raw.get("text") if isinstance(raw, dict) else None
        if not isinstance(text, str):
            raise HerdrFailure(
                "hc_output", "hc read 未返回 text 字段", [str(raw)[:300]],
                op="read", source="hc", stage="missing_text", detail=raw,
                partial_facts={"raw": raw})
        return Readback(text=text, raw=raw)

    def kill(self, target):
        return Session.from_raw(
            self._hc(["kill", target], SEND_GUARD_S, op="kill"))

    # ---- 空间簇 ----

    def layout(self):
        return Layout(self._app(["ls"], QUERY_GUARD_S, op="layout"))

    def selected_space(self):
        return self._app(["status"], QUERY_GUARD_S, op="selected_space").get("space")

    def create_space(self, name, folder):
        payload = {"name": name, "folder": folder, "focus": False}
        raw = self._app(
            ["do", "space.new", json.dumps(payload, ensure_ascii=False)],
            QUERY_GUARD_S, op="create_space")
        space = raw.get("space") if isinstance(raw, dict) else None
        if not space:
            raise HerdrFailure(
                "space_output", "space.new 未返回新空间 id", [str(raw)[:200]],
                op="create_space", source="app", stage="missing_space",
                detail=raw, partial_facts={"raw": raw})
        return space

    def focus(self, space):
        return Focus(self, space)

    def keep(self, address, within=POLL_TIMEOUT_S, poll=POLL_INTERVAL_S):
        """terminal.keep ＋落地轮询复核；窗口耗尽抛 stage=no_pane。"""
        self._app(["do", "terminal.keep", json.dumps({"terminal": address})],
                  QUERY_GUARD_S, op="keep", stage="keep_command",
                  partial={"address": address})
        deadline = self.deps.monotonic() + within
        while True:
            try:
                layout = self.layout()
            except HerdrFailure as e:
                raise self._refail(e, op="keep", stage="keep_poll",
                                   partial={"address": address}) from e
            placement = layout.placement(address)
            if placement is not None:
                return placement
            if self.deps.monotonic() >= deadline:
                # kind 用网关内部名，不产出外部词（no_pane 是 adopt_reason，归管线映射）；
                # 管线按 stage 映射到外部 no_pane。
                raise HerdrFailure(
                    "no_placement", "keep 后轮询窗口耗尽仍无 pane（后台落地失败）", [],
                    op="keep", source="app", stage="no_pane",
                    partial_facts={"address": address})
            self.deps.sleep(poll)

    def rename_tab(self, address, name):
        """ls 定位 tab → tab.rename → ls 复核 tab.name；stage 区分三种失败。"""
        try:
            tab = _find_tab(self.layout().raw, address)
        except HerdrFailure as e:
            raise self._refail(e, op="rename_tab", stage="rename_error",
                               partial={"address": address}) from e
        if tab is None:
            raise HerdrFailure(
                "no_tab", "ls 里定位不到终端 %s 所在 tab" % address, [],
                op="rename_tab", source="app", stage="no_tab",
                partial_facts={"address": address})
        try:
            self._app(["do", "tab.rename", json.dumps(
                {"id": tab["id"], "name": name}, ensure_ascii=False)],
                QUERY_GUARD_S, op="rename_tab", stage="rename_error",
                partial={"address": address, "tab_id": tab["id"]})
            actual = _find_tab(self.layout().raw, address)
            actual_name = actual.get("name") if actual else None
        except HerdrFailure as e:
            raise self._refail(e, op="rename_tab", stage="rename_error",
                               partial={"address": address,
                                        "tab_id": tab["id"]}) from e
        if actual_name != name:
            raise StatusMismatch(
                "status_mismatch",
                "复核 tab.name=%r ≠ 显示名 %r" % (actual_name, name), [],
                op="rename_tab", source="app", stage="rename_mismatch",
                partial_facts={"address": address, "tab_id": tab["id"],
                               "actual": actual_name})
        return tab["id"]

    def discard_shell_tabs(self, space, keep_address,
                           within=POLL_TIMEOUT_S, poll=POLL_INTERVAL_S):
        """关赠送标签页 → 轮询 hc get 等 finished → rm；返回降级 warnings。

        warnings 的 reason 四种：tab_close_error / finish_timeout /
        finish_poll_error / rm_error（与既有 gift_cleanup_warning 同词汇）。
        单个 tab 失败不阻断其余 tab 的清理。
        """
        warnings = []
        raw = self.layout().raw
        space_entry = _find_space(raw, space)
        if space_entry is None:
            return warnings
        for tab in space_entry.get("tabs") or []:
            if not isinstance(tab, dict):
                continue
            addresses = [entry.get("terminal")
                         for entry in tab.get("terminals") or []
                         if isinstance(entry, dict)]
            if keep_address in addresses:
                continue
            # 与分发脚本既有清理同语义：app 树里的标签页恒有 id 键，直接取；
            # 不为「缺 id」另造 warning（那不是 tab.close 失败）。
            tab_id = tab["id"]
            try:
                self._app(["do", "tab.close",
                           json.dumps({"id": tab_id, "confirmed": True})],
                          QUERY_GUARD_S, op="discard_shell_tabs",
                          stage="tab_close",
                          partial={"space": space, "tab_id": tab_id})
            except HerdrFailure as e:
                warnings.append({"reason": "tab_close_error",
                                 "detail": "关闭赠送 shell tab 失败：%s" % e.message})
                continue
            for address in addresses:
                if not address:
                    continue
                outcome = self._await_finished(address, within, poll)
                if outcome == "gone":
                    continue
                try:
                    self._hc(["rm", address], QUERY_GUARD_S,
                             op="discard_shell_tabs", stage="rm",
                             partial={"address": address})
                except NotFound:
                    continue    # get 与 rm 之间条目被收走：视同清理完成
                except HerdrFailure as e:
                    reason = {"timeout": "finish_timeout",
                              "poll_error": "finish_poll_error"}.get(
                                  outcome, "rm_error")
                    warnings.append({
                        "reason": reason,
                        "detail": "hc rm 赠送 shell 终端失败：%s" % e.message})
        return warnings

    # ---- 内部：命令封装与失败映射 ----

    def _hc(self, argv, timeout_s, *, op, stage=None, partial=None):
        try:
            return self.deps.hc_json(argv, timeout_s)
        except DispatchError as e:
            raise self._failure(e, op=op, source="hc", stage=stage,
                               partial=partial) from e

    def _app(self, argv, timeout_s, *, op, stage=None, partial=None):
        try:
            return self.deps.app_json(argv, timeout_s)
        except DispatchError as e:
            raise self._failure(e, op=op, source="app", stage=stage,
                               partial=partial) from e

    def _get(self, target, *, op, stage=None, partial=None):
        raw = self._hc(["get", target], QUERY_GUARD_S, op=op, stage=stage,
                       partial=partial)
        return Session.from_raw(raw)

    def _select_and_verify(self, space, *, op):
        """space.select ＋只读 status 复核；命令成功不算数。"""
        self._app(["do", "space.select", json.dumps({"id": space})],
                  QUERY_GUARD_S, op=op, stage="space_select",
                  partial={"target": space})
        try:
            return self.selected_space()
        except HerdrFailure as e:
            raise self._refail(e, op=op, stage="space_verify",
                               partial={"target": space}) from e

    def _restore_space(self, original, target):
        """还原选中；返回 (结局, warning)。用户已切走不抢回，失败降级 warning。"""
        try:
            current = self.selected_space()
        except HerdrFailure as e:
            return "failed", {"reason": "restore_error", "detail": e.message}
        if current != target:
            return "user_switched", None
        if original is None or original == target:
            return "not_needed", None
        try:
            current2 = self._select_and_verify(original, op="focus")
        except HerdrFailure as e:
            return "failed", {"reason": "restore_error", "detail": e.message}
        if current2 == original:
            return "restored", None
        return "failed", {
            "reason": "restore_mismatch", "actual": current2,
            "detail": "还原复核选中=%r ≠ 原选中 %r" % (current2, original)}

    def _await_finished(self, address, within, poll):
        """轮询 hc get 等终端 finished；返回 finished / gone / timeout / poll_error。"""
        deadline = self.deps.monotonic() + within
        last_error = None
        while True:
            try:
                session = self._get(address, op="discard_shell_tabs",
                                    stage="finish_poll",
                                    partial={"address": address})
            except NotFound:
                return "gone"
            except HerdrFailure as e:
                session, last_error = None, e
            else:
                last_error = None
            if session is not None and session.finished:
                return "finished"
            if self.deps.monotonic() >= deadline:
                return "poll_error" if last_error is not None else "timeout"
            self.deps.sleep(poll)

    def _failure(self, exc, *, op, source, stage=None, partial=None):
        fields = dict(partial or {})
        cls = _NATIVE_SUBCLASSES.get(exc.kind, HerdrFailure)
        return cls(
            exc.kind, exc.message, list(getattr(exc, "hints", []) or []),
            getattr(exc, "returncode", 1),
            op=op, source=source,
            native_kind=(exc.kind if source == "hc"
                         and exc.kind not in _PROCESS_KINDS else None),
            stage=stage, detail=getattr(exc, "detail", None),
            partial_facts=fields)

    def _refail(self, exc, *, op=None, stage=None, partial=None):
        """改标 op/stage 的再抛（保留 kind/native_kind/returncode/hints/detail）。"""
        fields = dict(exc.partial_facts)
        fields.update(partial or {})
        return type(exc)(
            exc.kind, exc.message, list(exc.hints), exc.returncode,
            op=op or exc.op, source=exc.source, native_kind=exc.native_kind,
            stage=stage, detail=exc.detail, partial_facts=fields)
