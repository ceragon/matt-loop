#!/usr/bin/env python3
"""pipeline.py — 一轮管线：`run(request, deps) → Outcome`（P2，ADR-0020）。

从分发脚本整体搬入的一体编排：静态闸与分流、台账、前置与主任务发送、等待、
回读与抽取。`dispatch.py` 只剩 CLI adapter（parse_args、main、Deps 默认适配器、
交付与 webhook、抽取后端）；本模块不 import 分发脚本，外部世界经鸭子类型的
`Deps` 注入。依赖方向：dispatch → pipeline → {herdr, turn, learning, errors}。

`RunState` 收口三套并行状态（局部标量、facts/ctx、台账），对管线只暴露三个
薄包装方法（ADR-0020 决定 12）；台账文件本身由兄弟模块 `Journal` 独占
（ADR-0022 决定 1）：
  - phase(name, **fields)：委托 Journal.phase（写台账 phase 与字段并原子落盘）；
    name=None 表示只落盘、不改 phase（基线里若干「只补字段」写点的原样保留）。
    台账未开（预检期）时不写——预检期领养沿用基线 `ledger=None` 语义。
  - fact(key, value, *, ledger=True)：写 facts（即 ctx）；ledger=True 时立即镜像进
    台账并原子落盘（ADR-0022 决定 5：基线约 5 处延后落盘改为立即落盘，只改中间态）。
  - project(ok)：把 facts 与轮次标量投影成交付载荷（成功 19 键 / error 21 键）；
    纯函数式投影，留在 RunState（不进 Journal）。

台账未开时不做通用缓冲（ADR-0022 决定 2 订正）：facts 本身就是预检期缓冲；
首版台账只取 target_address 与 workspace 两个键。`run` 永不外抛 DispatchError：
顶层统一捕获取 `DispatchError` 转 error Outcome 并写台账 error 终态；非预期异常
照旧冒泡；中途本地 IO 的 OSError 顶层兜底映射 io_failed（ADR-0024）随迁不改。
"""
import enum
import hashlib
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# 与分发脚本同目录；直跑/经 importlib 加载时都能导入兄弟模块。
sys.path.insert(0, str(Path(__file__).resolve().parent))
import learning  # noqa: E402
from errors import DispatchError  # noqa: E402
from herdr import Herdr, HerdrFailure, NotFound  # noqa: E402
from journal import Journal  # noqa: E402
from turn import (  # noqa: E402
    closest_fragment, cut_turn, find_session_start_trigger, fold_hits,
    generate_short_task_name, normalize_paste, utf16_units, verbatim_present,
    verify_extraction, verify_reply,
)

# hc 任务名规则
TASK_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,31}$")
RESERVED_ID_RE = re.compile(r"^t\d+$")
# herdr 会话名规则（hc run --name：1–32 位 a-z 0-9 - _）；t 号是保留地址、不能作名字。
SESSION_NAME_RE = re.compile(r"^[a-z0-9_-]{1,32}$")
# 显示名机械校验（ADR-0010）：非空、1–64 字符、禁换行与控制字符（C0＋DEL）。不校验式样。
DISPLAY_NAME_MAX = 64
DISPLAY_NAME_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")

# 迁移前 hc_json 只把「非零退出」包成独立进程层失败类（现已并入 HerdrFailure）；进程/
# 输出层失败（run_cmd 的 missing_tool/tool_timeout 与 hc_json 的 hc_output）原样穿透。
# 网关把这些也收进 HerdrFailure，这里按 kind 恢复逐字穿透（G2，ADR-0018 决定 9）。
_HC_PASSTHROUGH_KINDS = frozenset({"missing_tool", "tool_timeout", "hc_output"})
# 回读范围（ADR-0013）：台账 params 记录与网关同值。
READ_LINES = 1000
# 前置落定用短轮询而非 hc wait（/clear 可能不进 working）；working 时每 RESET_POLL_S 秒查一次。
RESET_POLL_S = 2.0
# 领养复核轮询默认窗口（#40）；实际值由 Request.timing 传入。
ADOPT_TIMEOUT_S = 30.0
ADOPT_POLL_S = 2.0
# 会话起始 skill 命令行形态闸：归一化后 UTF-16 码元上限（ADR-0017 留余量取 700）。
SPLIT_MAX_UTF16 = 700


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def has_internal_newline(text):
    """内部换行＝剥掉尾部换行（含连续多个）后仍含换行（尾换行豁免，ADR-0017 定案 A）。"""
    return "\n" in text.rstrip("\n")


def validate_task_name(name: str) -> None:
    if not TASK_NAME_RE.match(name) or RESERVED_ID_RE.match(name):
        raise DispatchError("bad_task_name", f"任务名不合法：{name}", ["使用自动生成的短名称"])


# ---------------------------------------------------------------- 入口类型


@dataclass(frozen=True)
class Timing:
    """七个时间旋钮（ADR-0020 决定 1）；argparse 默认值在 main 薄壳里。"""

    wall_clock: float
    check_in: float
    settle_seconds: float
    settle_checks: int
    adopt_timeout: float
    adopt_poll: float
    extract_timeout: float


@dataclass(frozen=True)
class Request:
    """一轮的请求：20 个 flag 的类型化值（ADR-0020 决定 1）；argparse 不进管线。

    `reset`/`target_model`/`space` 等字段语义同 CLI flag；七个时间旋钮收在 Timing。
    """

    target: str
    release_id: str
    prompt_file: str
    reset: str
    target_model: str | None
    launch: str | None
    cwd: str | None
    display_name: str | None
    space: str
    rebuild: bool
    expect_proc: str | None
    loop_dir: str
    pi_model: str | None
    timing: Timing


@dataclass(frozen=True)
class Outcome:
    """run 的交付面（ADR-0020 决定 2）；由 main 交给 emit_result。"""

    returncode: int
    payload: dict
    result_path: Path | None
    notify_path: Path | None


@dataclass(frozen=True)
class Extraction:
    """抽取结果（ADR-0020 决定 13）：五字段＋干净回复全文＋next_skill 格式标记。

    取代 `args.learning_injection`/`args.learning_next_skill_invalid` 暗渡；
    `fields` 是既有结果载荷里的 needs_user/questions/conclusion/blocked/next_skill。
    """

    fields: dict
    reply: str
    next_skill_invalid: bool


# ---------------------------------------------------------------- RunState

# 结果载荷里的 ctx 键段：一张有序表 21 键（ADR-0022 决定 4）。
# `launch_sent` 与 `adopt_reason` 仅 error 带出；成功投影过滤它们后是 19 键，
# 且键序恰为 error 键序的子序列——输出键序与现状逐字一致（快照逐字比对）。
_ERROR_ONLY_FACT_KEYS = frozenset({"launch_sent", "adopt_reason"})
_FACT_KEYS = (
    "target_address", "launched", "launch_sent", "rebuilt", "rebuilt_old_target",
    "workspace", "adopt_reason", "space_created", "pane_adopted",
    "gift_cleanup_warning", "reset_skipped", "display_name", "display_name_warning",
    "space", "space_selected", "space_landed", "space_target_warning",
    "space_restore", "space_restore_warning", "next_skill_feedback", "learning_warning",
)
# 台账缺键哨兵：`complete=False` 是合法值，判据是「键存在」而非真值（ADR-0022 决定 4）。
_MISSING = object()


class RunState:
    """一轮共享状态＋台账/上下文写入的三个薄包装方法（ADR-0020 决定 12）。

    台账文件由 `Journal` 独占（ADR-0022 决定 1）；三个方法仍是管线唯一调用点：
    phase 委托、fact 写 ctx 并立即镜像落盘、project 留在这里做纯投影。
    """

    def __init__(self, request, deps):
        self.request = request
        self.deps = deps
        self.facts = {}          # ctx：本轮部分状态（成功与 error 都带适用字段）
        self.ledger_path = Path(request.loop_dir) / "runs" / f"{request.release_id}.json"
        # 墙钟经 Deps 注入（ADR-0022 决定 7）；只有 Journal.fail 自盖 failed_at。
        self.journal = Journal(self.ledger_path, now=deps.now)
        self.result_path = None
        self.notify_path = None
        self.original_target = request.target
        self.task_name = None
        self.backend = None
        self.started = None
        self.reset_sent = False
        self.model_sent = False
        self.learning_on = False
        self.error = None        # 顶层捕获的 DispatchError，供 project(False) 取
        # 成功载荷里的轮次标量（run 在 finish 前填）
        self.final_turn = None
        self.final_status = None
        self.complete = None
        self.complete_reason = None
        self.anchor_found = None
        self.elapsed = None
        self.chars = None
        self.record_path = None
        self.raw_path = None
        self.turn_path = None
        self.prompt_sha = None
        self.rewritten = None
        self.ext_fields = {}

    @property
    def ledger_opened(self):
        """首版台账成功落盘＝放行 id 已消耗（ADR-0024 两态 hints 判据）。"""
        return self.journal.opened

    def phase(self, name=None, **fields):
        """委托 Journal.phase：写台账 phase 与字段并原子落盘；name=None 只补字段。

        台账未开（预检期）时不写——与基线各调用点的 `if ledger is not None`
        守卫同义（预检期领养确实会走到这里）。Journal.phase 自身在 open 前调用
        是编程错误（Journal 单测钉死）。
        """
        if not self.journal.opened:
            return
        self.journal.phase(name, **fields)

    def fact(self, key, value, *, ledger=True):
        """写 facts（ctx）；ledger=True 时立即镜像进台账并原子落盘。

        ADR-0022 决定 5：基线约 5 处延后落盘统一改立即落盘（只改中间态，终态与
        phase 次序不变）。台账未开时只进 facts（Journal.fact 未开不写）。
        """
        self.facts[key] = value
        if ledger:
            self.journal.fact(key, value)

    def project(self, ok):
        """把 facts 与轮次标量投影成交付载荷（成功/error 两份固定头部）。

        键序与现状逐字一致（快照逐字比对）；error 额外经 Journal.get 读台账的
        run_record/complete/complete_reason（缺键哨兵区分 `complete=False`）。
        """
        return self._success_payload() if ok else self._error_payload()

    def _success_payload(self):
        request = self.request
        payload = {
            "ok": True,
            "phase": "done",
            "release_id": request.release_id,
            "task_name": self.task_name,
            "target": self.original_target,
            "reset": request.reset,
            "turn": self.final_turn,
            "status": self.final_status,
            "complete": self.complete,
            "complete_reason": self.complete_reason,
            "anchor_found": self.anchor_found,
            "elapsed_s": self.elapsed,
            "chars": self.chars,
            "run_record": str(self.record_path),
            "raw_record": str(self.raw_path),
            "turn_record": str(self.turn_path),
            "prompt_sha256": self.prompt_sha,
            "rewritten": self.rewritten,
            "extract_backend": self.backend,
            **self.ext_fields,
        }
        # target_model 仅传了才出现：语义是「本轮请求切换到的名字」，非「已验证生效」。
        if request.target_model:
            payload["target_model"] = request.target_model
        for k in _FACT_KEYS:
            if k in _ERROR_ONLY_FACT_KEYS:
                continue
            if k in self.facts:
                payload[k] = self.facts[k]
        return payload

    def _error_payload(self):
        e = self.error
        request = self.request
        out = {
            "ok": False,
            "release_id": request.release_id,
            "target": self.original_target,
            "error": {"kind": e.kind, "message": e.message, "hints": e.hints},
        }
        if self.task_name is not None:
            out["task_name"] = self.task_name
        if self.backend is not None:
            out["extract_backend"] = self.backend
        if request.reset != "none":
            out["reset"] = request.reset
            out["error"]["reset_sent"] = self.reset_sent
        if request.target_model:
            out["target_model"] = request.target_model
            out["error"]["model_sent"] = self.model_sent
        for k in _FACT_KEYS:
            if k in self.facts:
                out[k] = self.facts[k]
        if self.started is not None:
            out["error"]["elapsed_s"] = round(self.deps.monotonic() - self.started, 1)
        run_record = self.journal.get("run_record")
        if run_record:
            out["error"]["run_record"] = run_record
        # 完整性标记（ADR-0019）：回读后发生的错误（如抽取失败）不丢折叠标记。
        # 判据是「键存在」：complete=False 是合法值（哨兵区分缺键）。
        complete = self.journal.get("complete", _MISSING)
        if complete is not _MISSING:
            out["complete"] = complete
            out["complete_reason"] = self.journal.get("complete_reason")
        return out


# ---------------------------------------------------------------- 静态闸/分流


def check_structure_and_tools(deps):
    script_dir = Path(__file__).resolve().parent
    extract_md = script_dir.parent / "prompts" / "extract.md"
    if not extract_md.is_file():
        raise DispatchError("missing_structure", f"找不到 {extract_md}", [])
    for tool in ("hc", "pi", "uv"):
        if not deps.locate(tool):
            raise DispatchError("missing_tool", f"找不到 {tool}",
                                ["确认 PATH；脚本也会探 ~/.local/bin、homebrew、nvm node bin"])


class Route(enum.Enum):
    """五路分流结果（ADR-0020 决定 4）；is_fresh 取代可变的局部 fresh。"""

    create = "create"
    revive = "revive"
    rebuild = "rebuild"
    existing = "existing"
    occupied = "occupied"

    @property
    def is_fresh(self):
        return self in (Route.create, Route.revive, Route.rebuild)


def _label_ok(session, expect_proc):
    """loop 标签收编凭据（ADR-0009）：loop=1 且 loop-agent=--expect-proc（未传则只认 loop）。"""
    labels = session.labels
    return (labels.get("loop") == "1"
            and (not expect_proc or labels.get("loop-agent") == expect_proc))


def classify(session, request):
    """五路分流纯函数（ADR-0020 决定 4）：create／revive／rebuild／existing／occupied。

    session 为 None（not_found）或 state=="finished"→create；状态既非 live 也非
    finished 时抛 not_idle（原生语义不变）。live 按 loop 标签、前台 shell/agent
    身份判定；occupied 且 --rebuild → rebuild（升格并入）；--rebuild 误传其他
    分流 → bad_args。无 I/O、不碰 ctx；request 只读 expect_proc 与 rebuild。
    """
    if session is None or session.state == "finished":
        route = Route.create
    elif session.state != "live":
        raise DispatchError("not_idle", "目标终端已结束，不发送", [])
    elif not _label_ok(session, request.expect_proc):
        route = Route.occupied
    elif session.is_shell() and session.status_state is None:
        route = Route.revive      # 空闲 shell＋标签符→原地复活
    elif not session.is_shell() and session.identity_ok(request.expect_proc):
        route = Route.existing    # 前台 agent 身份符＋标签符→既有路径
    else:
        route = Route.occupied    # 别的 agent/程序（身份不符），或 shell 异常态
    if route is Route.occupied and request.rebuild:
        route = Route.rebuild
    if request.rebuild and route is not Route.rebuild:
        raise DispatchError(
            "bad_args", "--rebuild 只在 session_occupied 占位分支生效，其余分流误传",
            ["去掉 --rebuild；强制重建仅用于用户显式授权 kill 占位者后同名重建"],
            returncode=2)
    return route


def gate_route(route, session, request):
    """分流后闸纯函数（ADR-0020 决定 4）：原次序逐字——

    占位报 session_occupied（四种后缀文案在此构造）→ 新鲜路径 launch_required →
    display_name_required → 非法会话名（create/rebuild）→ rebuild_busy。
    只读输入、只抛既有 DispatchError；不写 ctx/台账。
    """
    if route is Route.occupied:
        fg = session.foreground or {}
        argv = fg.get("argv") or []
        fg_desc = (argv[0] if isinstance(argv, list) and argv else "") \
            or fg.get("name") or "未知"
        labels = session.labels
        label_ok = _label_ok(session, request.expect_proc)
        suffix = ("（无 loop 标签，拒收编）" if labels.get("loop") != "1"
                  else "（loop 标签与 --expect-proc 不符）" if not label_ok
                  else "（前台 shell 但 status 非 null，异常态）"
                  if session.is_shell()
                  else "（前台与 --expect-proc 不符）")
        raise DispatchError(
            "session_occupied",
            f"会话名 {request.target} 被占用：{session.address or request.target} "
            f"前台={fg_desc} turn={session.turn} 状态={session.status_state or '未自报'}"
            + suffix,
            ["呈报占位者给用户；确需重建须经用户显式授权后带 --rebuild（只杀 idle/done）",
             "或换 --target 到另一命名会话；不建不 kill、零发送、id 不消耗"],
        )
    if not route.is_fresh:
        return
    why = {"create": "目标会话不存在或已死，创建",
           "revive": "目标是空闲 shell，复活",
           "rebuild": "强制重建"}[route.value]
    if not request.launch:
        raise DispatchError(
            "launch_required",
            f"{why}需要 --launch（＋--cwd）",
            ["补 --launch <shell 启动命令> --cwd <目录> 后用同一放行 id 重试（零发送、id 不消耗）",
             "创建参数（会话名、启动命令、cwd、显示名）须随放行一并贴出，见 SKILL.md"],
            returncode=2)
    if request.display_name is None:
        raise DispatchError(
            "display_name_required",
            f"{why}需要 --display-name",
            ["补 --display-name <显示名> 后用同一放行 id 重试（零发送、id 不消耗）",
             "显示名须随放行一并贴出，见 SKILL.md"],
            returncode=2)
    if route in (Route.create, Route.rebuild) and (
            not SESSION_NAME_RE.match(request.target)
            or RESERVED_ID_RE.match(request.target)):
        raise DispatchError(
            "bad_args",
            f"--target {request.target} 不是合法会话名（1–32 位 a-z0-9-_），无法创建",
            ["--target 推荐传会话名（<kind>-<repo> 或 bot slug）；t 号是保留地址、不能作名字"],
            returncode=2)
    if route is Route.rebuild and session.status_state not in ("idle", "done"):
        raise DispatchError(
            "rebuild_busy",
            f"占位者状态是 {session.status_state or '未自报'}，不是 idle/done，拒杀",
            ["等占位者落定或请用户到屏幕前处理；kill 只在 idle/done 时执行"],
        )


@dataclass(frozen=True)
class Prestep:
    """一次前置发送（ADR-0020 决定 6）：kind="reset"/"model"，command 是字面命令。"""

    kind: str
    command: str


@dataclass(frozen=True)
class PrestepPlan:
    """前置纯函数的产出：次序固定的 specs，加「重置被新鲜会话豁免」的 fact。"""

    specs: tuple[Prestep, ...]
    reset_skipped: str | None


def prestep_specs(request, route):
    """前置次序纯函数（ADR-0020 决定 6）：重置 → 切模型。

    新鲜会话（create/revive/rebuild）不产出重置 spec，并记
    reset_skipped="fresh_launch"；切模型前置照跑。无 I/O、不碰台账。
    """
    specs = []
    reset_skipped = None
    if request.reset != "none":
        if route.is_fresh:
            reset_skipped = "fresh_launch"
        else:
            specs.append(Prestep("reset", "/" + request.reset))
    if request.target_model:
        specs.append(Prestep("model", "/model " + request.target_model))
    return PrestepPlan(tuple(specs), reset_skipped)


# ---------------------------------------------------------------- 空间匹配


def parse_space(space, spaces):
    """--space 三态解析（ADR-0011，#70 归一）：唯一匹配实现，领养与学习共用。

    三态：/ 或 ~ 开头→路径（expanduser＋normpath 归一后按 space.folder 匹配：
    唯一→复用 id；零个→给出 new_space 提案 {name:目录名, folder:路径, focus:False}，
    是否执行 space.new 由调用方决定；多个→space_ambiguous）；sp 前缀→id 直配
    （不存在→space_not_found）；其余→名（唯一匹配；重名→bad_args；不存在→
    space_not_found，名不自动建空间）。命中空间缺 id→space_not_found（不静默退化）。

    返回 {"target_id": 解析出的 id 或 None, "new_space": space.new 参数或 None,
    "error": None 或 {"reason", "message", "hint"}}：reason 为 space_ambiguous／
    space_not_found／bad_args（名重名），hint 是附加改正指引（可为 None）。
    纯函数：不做 I/O、不建空间、不 select、不抛错；「建不建空间」与错误映射
    （adopt_failed 还是 bad_args、retry 前缀）是调用方策略，不进本函数。
    """
    result = {"target_id": None, "new_space": None, "error": None}
    if space.startswith(("/", "~")):
        path = os.path.normpath(str(Path(space).expanduser()))
        matches = [sp for sp in spaces if sp.get("folder") == path]
        if len(matches) > 1:
            result["error"] = {
                "reason": "space_ambiguous",
                "message": f"--space 路径 {path} 按 folder 命中 {len(matches)} 个空间，无法定夺",
                "hint": "改传空间 id（sp…）或唯一名消除歧义"}
        elif matches:
            sid = matches[0].get("id")
            if sid:
                result["target_id"] = sid
            else:
                result["error"] = {
                    "reason": "space_not_found",
                    "message": f"--space 路径 {path} 命中的空间缺 id，无法定位",
                    "hint": None}
        else:
            result["new_space"] = {"name": os.path.basename(path),
                                   "folder": path, "focus": False}
    elif space.startswith("sp"):
        if any(sp.get("id") == space for sp in spaces):
            result["target_id"] = space
        else:
            result["error"] = {"reason": "space_not_found",
                               "message": f"--space id {space} 在 ls 里找不到",
                               "hint": None}
    else:
        matches = [sp for sp in spaces if sp.get("name") == space]
        if not matches:
            result["error"] = {
                "reason": "space_not_found",
                "message": f"--space 名 {space} 在 ls 里找不到",
                "hint": "核对目标空间名/id/路径；名不自动建空间，传路径零匹配才建"}
        elif len(matches) > 1:
            result["error"] = {
                "reason": "bad_args",
                "message": f"--space {space} 重名命中 {len(matches)} 个空间，无法定夺",
                "hint": None}
        else:
            sid = matches[0].get("id")
            if sid:
                result["target_id"] = sid
            else:
                result["error"] = {
                    "reason": "space_not_found",
                    "message": f"--space {space} 命中的空间缺 id，无法定位",
                    "hint": None}
    return result


@dataclass(frozen=True)
class SpacePlan:
    """--space 三态匹配的策略结果（ADR-0020 决定 7）：reuse／create／error。"""

    kind: str
    space_id: str | None = None
    name: str | None = None
    folder: str | None = None
    reason: str | None = None
    message: str | None = None
    hint: str | None = None


def plan_space(space, layout):
    """parse_space 之上的薄包装（ADR-0011 匹配规则唯一实现，ADR-0020 决定 7）。

    只把三态结果映射成 reuse(space_id)／create(name, folder)／error(reason,
    message, hint)；不重写匹配规则、不建空间、不 select、不抛错。
    """
    parsed = parse_space(space, (layout.raw or {}).get("spaces") or [])
    if parsed["error"] is not None:
        err = parsed["error"]
        return SpacePlan(kind="error", reason=err["reason"],
                         message=err["message"], hint=err["hint"])
    if parsed["new_space"] is not None:
        new = parsed["new_space"]
        return SpacePlan(kind="create", name=new["name"], folder=new["folder"])
    return SpacePlan(kind="reuse", space_id=parsed["target_id"])


def resolve_space_id_for_learning(herdr, space):
    """--space 三态解析的只读投影（ADR-0012 学习用，#70 归一）：不建空间、不 select、不抛错。

    匹配与领养共用 parse_space（ADR-0011 规则唯一实现）；本函数只加只读策略：
    error／new_space 提案（路径零匹配）／ls 失败一律返回 None——学习侧据此禁用
    本轮 space scoped 学习并告警（id 缺失禁用该轮学习，ADR-0012）。
    绝不触发 space.new（那是领养路径的显式 opt-in）。读整棵 app 树经网关 `layout()`。
    """
    try:
        layout = herdr.layout()
    except DispatchError:
        return None
    plan = plan_space(space, layout)
    return plan.space_id if plan.kind == "reuse" else None


# ---------------------------------------------------------------- 会话/等待


def identity_ok(terminal, expect_proc):
    """身份判据（ADR-0009）：只认 foreground.argv[0] 与 status.app（OSC 7501 自报）；
    foreground.name 不可靠（claude 实测为版本号），不作判据。未传 expect_proc 恒真。"""
    if not expect_proc:
        return True
    fg = terminal.get("foreground") or {}
    st = terminal.get("status") or {}
    argv = fg.get("argv") or []
    first = argv[0] if isinstance(argv, list) and argv else ""
    return any(expect_proc in c for c in (first, st.get("app") or ""))


def check_identity(terminal, request):
    if not identity_ok(terminal, request.expect_proc):
        raise DispatchError(
            "identity_mismatch",
            "目标前台进程不含预期子串（--expect-proc）",
            ["核对 hc get 的 foreground.argv[0] 与 status.app；不要向身份不符的终端发送"],
        )


def dialog_error(session):
    st = session.status or {}
    kind = st.get("kind")
    msg = st.get("msg") or ""
    return DispatchError(
        "dialog_blocked",
        "目标会话停在弹框等人决定" + (f"（kind={kind}）" if kind else "") + (f"：{msg}" if msg else ""),
        ["不按键、不代答；请用户到目标会话屏幕前处理，处理后用新放行 id 重新发起"],
    )


def wait_for_result(deps, herdr, request, baseline, deadline):
    """hc wait 循环：状态变化即醒、到点 check-in、墙钟兜底。
    返回落定复核通过后的 Session。"""
    while True:
        remaining = deadline - deps.monotonic()
        if remaining <= 0:
            raise DispatchError(
                "timeout",
                "墙钟超时：目标会话仍在工作",
                ["原文未取回；可稍后用 hc read 查看，或经用户放行后另起一轮"],
            )
        t = min(request.timing.check_in, remaining)
        try:
            w = herdr.wait_change(request.target, t)
        except HerdrFailure as e:
            if e.kind in _HC_PASSTHROUGH_KINDS:
                raise
            raise DispatchError("hc_failed", f"hc wait 失败：{e.message}", e.hints)
        if w.reason == "exit":
            raise DispatchError(
                "target_exited",
                f"目标程序退出（exit_code={w.raw.get('exit_code')} signal={w.raw.get('signal')}）",
                ["确认目标会话状态后另起一轮"],
            )
        if w.reason == "not_started":
            raise DispatchError(
                "not_started",
                "提示词已发出，但目标没有进入 working",
                ["确认提示词是否真的提交给了目标程序；本轮不再发送"],
            )
        if w.reason == "status":
            state = w.status
            if state == "blocked":
                # 未包装的原生 kind 穿透（ADR-0018 决定 2/9）：get 失败按原 kind 抛出。
                raise dialog_error(herdr.session(request.target))
            if state == "error":
                raise DispatchError("target_error", "目标自报 error", ["到目标会话屏幕前查看"])
            if state in ("idle", "done") and (w.turn or 0) > baseline:
                outcome = settle(deps, herdr, request, w.turn)
                if outcome is not None:
                    return outcome
                continue
            # working、或 turn 未过基线：继续等
            continue
        # timeout（check-in）及其他 reason：用 hc get 兜底核对一次
        g = herdr.session(request.target)
        if g.state != "live":
            raise DispatchError("target_exited", "目标终端已结束", ["hc get 查看 reason"])
        state = g.status_state
        if state == "blocked":
            raise dialog_error(g)
        if state == "error":
            raise DispatchError("target_error", "目标自报 error", ["到目标会话屏幕前查看"])
        if state in ("idle", "done") and (g.turn or 0) > baseline:
            outcome = settle(deps, herdr, request, g.turn)
            if outcome is not None:
                return outcome


def settle(deps, herdr, request, wait_turn):
    """落定复核：静置 settle_seconds 后 hc get，连续 settle_checks 次
    状态仍 idle/done 且 turn 不变才算落定。回到 working 等则返回 None 继续等。"""
    for _ in range(request.timing.settle_checks):
        deps.sleep(request.timing.settle_seconds)
        g = herdr.session(request.target)
        if g.state != "live":
            raise DispatchError("target_exited", "落定复核时目标终端已结束", [])
        state = g.status_state
        if state == "blocked":
            raise dialog_error(g)
        if state == "error":
            raise DispatchError("target_error", "落定复核时目标自报 error", [])
        if state not in ("idle", "done") or (g.turn or 0) != wait_turn:
            return None
    return g


def do_send(state, herdr, request, text):
    """--if idle 首发；被拒（status_mismatch）且 hc get 复核为 done 才补发一次。
    返回 (send, resent)。前置与主任务共用同一套发送护栏（裁定 6）：
    永不 --force、不碰 hc keys；被拒时零字节进入，不算重发。
    首发与补发是两个网关操作（ADR-0018 决定 5/8 补记）：被拒且复核 done 时，
    本层在两次复核之间落账 phase=resending＋first_refusal（与基线逐字一致），
    再调 herdr.resend；外部 kind/文案/hints 映射仍在本调用点。"""
    try:
        receipt = herdr.send(request.target, text, guard="idle")
    except HerdrFailure as e:
        if e.stage == "send":
            if e.kind in _HC_PASSTHROUGH_KINDS:
                raise
            kind = "send_refused" if e.kind == "blocked" else "hc_failed"
            raise DispatchError(kind, f"hc send 失败：{e.message}",
                                e.hints + ["未再发送；台账已记录本次尝试"])
        if e.stage == "refused_not_done":
            if e.kind != "status_mismatch":
                raise       # 复核 get 失败：原生 kind 穿透
            raise DispatchError("send_refused", e.message, e.hints)
        raise
    if receipt.first_refusal is None:
        return receipt.raw, False
    # 被拒且复核 done：先记账后补发（基线在两次 get 复核之间写盘）。
    state.phase("resending",
                first_refusal={"kind": receipt.first_refusal["kind"],
                               "returncode": receipt.first_refusal["returncode"]})
    try:
        resent = herdr.resend(request.target, text)
    except HerdrFailure as e:
        if e.stage == "refused_before_resend":
            if e.kind != "status_mismatch":
                raise       # 第二次复核 get 失败：原生 kind 穿透
            raise DispatchError("send_refused", e.message, e.hints)
        if e.stage == "resend":
            if e.kind in _HC_PASSTHROUGH_KINDS:
                raise
            raise DispatchError("send_refused", f"done 补发仍失败：{e.message}",
                                e.hints + ["本轮不再发送；台账已记录两次尝试"])
        raise
    return resent.raw, True


def reset_settle(deps, herdr, request):
    """前置落定复核：idle/done 且屏幕连续 settle_checks 次静置不变。
    /clear 是本地命令、可能不进 working/不增 turn，故用屏幕内容稳定而非 turn 判据。
    屏幕仍在变或离开 idle/done 返回 False；blocked/error/退出照常抛错。"""
    prev = None
    for _ in range(request.timing.settle_checks):
        deps.sleep(request.timing.settle_seconds)
        g = herdr.session(request.target)
        if g.state != "live":
            raise DispatchError("target_exited", "前置落定复核时目标终端已结束", [])
        state = g.status_state
        if state == "blocked":
            raise dialog_error(g)
        if state == "error":
            raise DispatchError("target_error", "前置落定复核时目标自报 error", [])
        if state not in ("idle", "done"):
            return False
        screen = herdr.read(request.target)
        if prev is not None and screen != prev:
            return False
        prev = screen
    return True


def wait_for_reset(deps, herdr, request, deadline):
    """等前置（/new /clear /compact /model）落定：短轮询 hc get 至 idle/done，再屏幕静置复核。
    不要求 turn 增长（/clear、/model 可能不增 turn）。前置不抽取、不落 run_record。"""
    while True:
        if deps.monotonic() >= deadline:
            raise DispatchError(
                "timeout",
                "前置墙钟超时：目标仍未落定",
                ["主任务未发送；本轮已停止，可经用户放行后另起一轮"],
            )
        g = herdr.session(request.target)
        if g.state != "live":
            raise DispatchError("target_exited", "前置期间目标终端已结束", ["hc get 查看 reason"])
        state = g.status_state
        if state == "blocked":
            raise dialog_error(g)
        if state == "error":
            raise DispatchError("target_error", "前置期间目标自报 error", ["到目标会话屏幕前查看"])
        if state in ("idle", "done"):
            if reset_settle(deps, herdr, request):
                return
            continue  # 屏幕仍在变（/clear 重绘 / /compact 收尾）：重新复核
        deps.sleep(RESET_POLL_S)  # working：短轮询


def wait_for_shell_ready(deps, herdr, target, deadline):
    """创建后等 shell 就绪：短轮询 hc get 至 state live＋前台是 shell。
    刚 run 完名字可能短暂未解析（not_found 继续等）；终端结束/超时→launch_failed。"""
    while True:
        if deps.monotonic() >= deadline:
            raise DispatchError("launch_failed", "等新终端 shell 就绪超时",
                                ["主任务未发送；本轮已停止，可经用户放行后另起一轮"])
        try:
            g = herdr.session(target)
        except NotFound:
            deps.sleep(RESET_POLL_S)
            continue
        except HerdrFailure as e:
            if e.kind in _HC_PASSTHROUGH_KINDS:
                raise
            raise DispatchError("launch_failed", f"等 shell 就绪时 hc get 失败：{e.message}", e.hints)
        if g.state != "live":
            raise DispatchError("launch_failed", "新终端 shell 就绪前已结束",
                                ["hc get 查看 reason；经用户放行后另起一轮"])
        if g.is_shell():
            return g
        deps.sleep(RESET_POLL_S)


def wait_for_agent_ready(deps, herdr, target, deadline):
    """等 agent 就绪：前台不再是 shell 且状态 idle/done。超时/终端结束→launch_failed；
    弹框/error 照常抛（既有 kind 不动）。返回就绪时的 Session。"""
    while True:
        if deps.monotonic() >= deadline:
            raise DispatchError("launch_failed", "等 agent 就绪超时（启动命令已发）",
                                ["主任务未发送；本轮已停止，可经用户放行后另起一轮"])
        try:
            g = herdr.session(target)
        except NotFound:
            # 名字只解析 live 终端：启动期间终端死亡即名字失解析。
            raise DispatchError("launch_failed", "agent 启动期间终端已结束（名字失解析）",
                                ["hc get 按 t 号查看 reason；经用户放行后另起一轮"])
        except HerdrFailure as e:
            if e.kind in _HC_PASSTHROUGH_KINDS:
                raise
            raise DispatchError("launch_failed", f"等 agent 就绪时 hc get 失败：{e.message}", e.hints)
        if g.state != "live":
            raise DispatchError("launch_failed", "agent 启动期间终端已结束",
                                ["hc get 查看 reason；经用户放行后另起一轮"])
        state = g.status_state
        if state == "blocked":
            raise dialog_error(g)
        if state == "error":
            raise DispatchError("target_error", "新 agent 自报 error", ["到目标会话屏幕前查看"])
        if not g.is_shell() and state in ("idle", "done"):
            return g
        deps.sleep(RESET_POLL_S)


def send_launch_command(state, herdr, request):
    """启动前置发送（创建/复活共用）：shell 不是 agent、不报 idle，用裸 send（不带 --if）。
    启动命令是命令不是提示词：不抽取、不落 run_record，占前置发送槽。
    只管发送不等就绪（#46 Bug3）：创建路径在发送与等就绪之间插领养（恒可见）。"""
    state.phase("launch_sending")
    try:
        receipt = herdr.send(request.target, request.launch, guard=None)
    except HerdrFailure as e:
        if e.kind in _HC_PASSTHROUGH_KINDS:
            raise
        raise DispatchError("launch_failed", f"启动命令发送失败：{e.message}",
                            e.hints + ["主任务未发送；台账已记录本次尝试"])
    state.fact("launch_sent", True)
    state.phase("launch_waiting_agent", launch_sent=True, launch_send=receipt.raw)


def wait_launch_agent(state, herdr, request):
    """等 agent 就绪＋核身份（创建/复活共用）。创建路径在领养之后跑（#46 Bug3）：
    卡弹框/超时失败时会话已领养可见，就绪失败另报状态、不影响可见性。"""
    state.phase("launch_waiting_agent")
    g = wait_for_agent_ready(state.deps, herdr, request.target,
                             state.deps.monotonic() + request.timing.wall_clock)
    check_identity(g.raw, request)
    state.phase("launch_done", launch_done_at=now_iso())


def do_create(state, herdr, request):
    """创建前置（ADR-0009，单形态 shell 包装）：hc run --bg --keep --no-parent --name
    --cwd -- zsh -l→打 loop=1＋loop-agent=<expect-proc> 标签→等 shell 就绪→send 启动命令。
    等 agent 就绪＋核身份拆在 wait_launch_agent（#46 Bug3：主流程在两者之间插领养，
    卡弹框也先可见）。名字唯一性由 herdr 仲裁（name_taken 竞态映射 launch_failed）。
    任一步失败整轮 launch_failed，ctx/台账带部分状态（终端已建/启动已发如实报）。"""
    state.phase("launch_running")
    try:
        # --no-parent（#46 Bug1）：命名会话无父链、独立于调用方终端——调用方背景
        # 终端退出/被杀不连带杀会话（die_with_parent 默认 true 会让会话活不过一轮），跨轮持久。
        created = herdr.create_session(request.target, request.cwd)
    except HerdrFailure as e:
        if e.stage == "missing_address":
            raise DispatchError("launch_failed", e.message, e.hints)
        if e.kind in _HC_PASSTHROUGH_KINDS:
            raise
        raise DispatchError("launch_failed", f"hc run 创建命名会话失败（{e.kind}）：{e.message}",
                            e.hints + ["同名 live 终端存在（name_taken）时不建不 kill；"
                                       "经用户放行后另起一轮或改用既有会话"])
    addr = created.address
    state.fact("target_address", addr)
    state.fact("launched", True)
    state.phase("launch_labeling", target_address=addr, launched=True)
    labels = ["loop=1"]
    if request.expect_proc:
        labels.append("loop-agent=" + request.expect_proc)
    try:
        herdr.label_session(request.target, labels)
    except HerdrFailure as e:
        if e.kind in _HC_PASSTHROUGH_KINDS:
            raise
        raise DispatchError("launch_failed", f"打 loop 标签失败：{e.message}",
                            e.hints + ["终端已建（launched:true）；无标签终端下轮会被拒收编"])
    state.phase("launch_waiting_shell")
    wait_for_shell_ready(state.deps, herdr, request.target,
                         state.deps.monotonic() + request.timing.wall_clock)
    send_launch_command(state, herdr, request)


def apply_display_name(state, herdr, address, display_name):
    """显示名落地（ADR-0010）：网关 `rename_tab`（ls 定位 tab id → tab.rename → ls 复核
    tab.name==显示名；命令成功不算数）。时序在领养复核 pane 落地后、前置/主任务发送前。
    失败降级告警不丢放行：rename 失败/复核不符/无 tab → ctx/台账记 display_name_warning
    （reason：rename_error/rename_mismatch/no_tab，带详情），主流程继续；pane 落地仍是
    硬闸（adopt_failed 语义不变）。result display_name 回显本轮传入值：落地步骤一跑就带
    （含 no_tab/ls 失败等未真正发出 rename 的降级情形，审计「请求了什么名」），成功与 error 都带。
    不写 summary 标签：机器可读靠自动跟随 tab 名的 where 标签。"""
    herdr.require_app()
    state.fact("display_name", display_name)
    state.phase("naming", display_name=display_name)
    warning = None
    try:
        herdr.rename_tab(address, display_name)
    except DispatchError as e:
        stage = getattr(e, "stage", None)
        if stage == "no_tab":
            warning = {"reason": "no_tab", "detail": e.message}
        elif stage == "rename_mismatch":
            warning = {"reason": "rename_mismatch",
                       "actual": e.partial_facts.get("actual"),
                       "detail": e.message}
        else:
            warning = {"reason": "rename_error", "detail": e.message}
    if warning is not None:
        state.fact("display_name_warning", warning)
        state.phase(None, display_name_warning=warning)
    else:
        state.phase("naming_done", naming_done_at=now_iso())


def adopt(state, herdr, address, precheck):
    """领养（ADR-0009 恒可见不变量）：status 探测→只读 ls 核 pane→缺则按 --space
    三态解析目标空间→（路径零匹配 space.new focus:false）→terminal.keep→带超时轮询
    ls 复核 pane 落地（#40）→space_created 时清理赠送 shell（tab.close confirmed:true
    →轮询等终端 finished→hc rm，#46 Bug2；清理失败降级 gift_cleanup_warning 不丢放行）。
    领养任一步失败抛 adopt_failed，ctx 记部分状态（workspace:"failed"＋
    adopt_reason＋space_created/pane_adopted 如实报）。
    空间定位（#43 grill 定稿，ADR-0011，space 必传）：只在需要 keep 时解析——
    三态匹配归一纯函数 parse_space（#70，唯一实现，规则见该函数）；本调用方策略：
    路径零匹配→执行 space.new{name:目录名,folder:路径,focus:false}，error 按 reason
    映射（名重名 bad_args，其余 adopt_failed）。目标≠当前 shown 才进 `focus`（select→
    status 复核→退出恒还原），不符即 select_error 停轮（keep 不发）；目标==shown 不 select。
    keep→复核 pane 及其所在空间（落错空间→space_target_warning 告警不丢放行）。
    pane 已在则提前返回不抢。机械步骤走网关（layout/focus/keep/discard_shell_tabs）；
    要不要领养/三态选哪个/零匹配建不建/盘点时机/失败映射等策略仍在本函数。
    precheck=True（既有路径补领养）：写台账前，失败零发送、id 不消耗、同 id 重试；
    precheck=False（创建路径）：写台账后，失败保留已建终端、id 消耗、新 id 重试修复。"""
    request = state.request
    space = request.space
    adopt_timeout = request.timing.adopt_timeout
    adopt_poll = request.timing.adopt_poll
    herdr.require_app()

    def fail(reason, message, hints):
        state.fact("workspace", "failed")
        state.fact("adopt_reason", reason)
        raise DispatchError("adopt_failed", message, hints)

    def gift_warning(reason, detail):
        # 赠送清理失败降级告警（#46 Bug2）：pane 已落地、恒可见满足，不丢已成功的领养。
        w = {"reason": reason, "detail": detail}
        state.fact("gift_cleanup_warning", w)
        state.phase(None, gift_cleanup_warning=w)

    def record_restore(cm):
        # f.restore 结局写进 ctx 与台账（现行落点：keep 后的 finally，成功失败都记）。
        warning = cm.warning
        if warning is not None and warning.get("reason") == "restore_mismatch":
            # 文案与基线逐字一致：网关 warning 不带「用户可能又切了」注脚。
            warning = dict(warning)
            warning["detail"] = (
                "还原复核选中=%r ≠ 原选中 %r（用户可能又切了）"
                % (warning.get("actual"), original_space))
        state.fact("space_restore", cm.restore)
        if warning is not None:
            state.fact("space_restore_warning", warning)
        if state.journal.opened:
            fields = {"space_restore": cm.restore}
            if warning is not None:
                fields["space_restore_warning"] = warning
            state.phase(None, **fields)

    def keep_and_verify():
        # terminal.keep＋落地轮询复核在网关 `keep` 内（命令成功不算数，窗口内反复
        # 只读 ls，pane 出现即成功；窗口耗尽报 no_pane）。本处只把领域失败映射回
        # 基线的外部 kind/文案。
        try:
            placement = herdr.keep(address, within=adopt_timeout, poll=adopt_poll)
        except DispatchError as e:
            stage = getattr(e, "stage", None)
            if stage == "keep_command":
                fail("keep_error", f"terminal.keep 失败：{e.message}", retry)
            if stage == "keep_poll":
                fail("app_closed", f"keep 后 herdr-app ls 失败：{e.message}", retry)
            if stage == "no_pane":
                fail("no_pane", e.message, retry)
            fail("keep_error", f"terminal.keep 失败：{e.message}", retry)
        # pane 已经本轮 keep 落地即如实报（#46：后续任一步失败——清理/等就绪——
        # 都不再掩盖「已可见」这一部分状态）。
        state.fact("pane_adopted", True)
        # 落地复核所在空间（#43）：pane 存在还不够，须在目标空间。keep 异步
        # 落地，窗口内选中被切走时 pane 会落进别的空间——如实告警不静默；
        # pane 已落地恒可见满足，不丢放行（多半是用户自己切走所致）。
        if space_target is not None:
            actual = placement.space_id
            state.fact("space_landed", actual)
            if actual != space_target:
                w = {"reason": "landed_elsewhere", "actual": actual,
                     "detail": f"pane 落进空间 {actual!r} ≠ 目标 {space_target!r}"
                               "（落地窗口内选中被切走）"}
                state.fact("space_target_warning", w)
        return placement

    retry = (["打开 herdr-app 后用同一放行 id 重试（零发送、id 未消耗）"] if precheck
             else ["已建终端保留；打开 herdr-app 后用新放行 id 重试，走既有路径补领养修复"])
    state.phase("adopting")
    try:
        original_space = herdr.selected_space()
    except DispatchError as e:
        fail("app_closed", f"herdr-app 没开或不可达，领养失败：{e.message}", retry)
    try:
        layout = herdr.layout()
    except DispatchError as e:
        fail("app_closed", f"herdr-app ls 失败，领养失败：{e.message}", retry)
    if layout.placement(address) is not None:
        state.fact("workspace", "adopted")
        state.phase("adopt_done", workspace="adopted")
        return
    # 空间定位三态解析（#43 grill 定稿，ADR-0011）：只在需要 keep 时解析（pane 已在
    # 上面已返回）。匹配归一纯函数 parse_space（#70，唯一实现）；本调用方策略：
    # 显式传路径 opt-in 自动建（零匹配时执行 space.new）；--space 必传，无「不传参
    # 静默按 cwd 猜」分支（多空间可共享同一 folder，静默 cwd 路由会拆散既有组织——
    # issue #43 关键约束）。
    plan = plan_space(space, layout)
    if plan.kind == "error":
        if plan.reason == "bad_args":
            # 名重名属参数错误：bad_args（退出码 2、id 不消耗），不走 fail 的部分状态。
            raise DispatchError(
                "bad_args", plan.message,
                ["改传空间 id（sp…）或路径；改正后用同一放行 id 重试（零发送、id 不消耗）"],
                returncode=2)
        fail(plan.reason, plan.message,
             retry + ([plan.hint] if plan.hint else []))
    space_target = plan.space_id
    # 路径零匹配→建空间（统一分支，废旧「零空间自动 space.new{name:会话名}」）：
    # name=目录名、folder=路径、focus:false 不抢视图；赠送 shell 清理绑 space_created。
    if plan.kind == "create":
        try:
            space_target = herdr.create_space(plan.name, plan.folder)
        except DispatchError as e:
            if getattr(e, "stage", None) == "missing_space":
                fail("keep_error", e.message, retry)
            fail("keep_error", f"space.new 失败：{e.message}", retry)
        state.fact("space_created", True)
        state.phase(None, space_created=True)
        # 建后复核当前 shown：零空间时新空间即被显示（真实 app 语义），有空间时
        # focus:false 保持原 shown——目标==shown 则下面不 select。
        try:
            original_space = herdr.selected_space()
        except DispatchError as e:
            fail("keep_error", f"space.new 后 status 复核失败：{e.message}", retry)
    # select 目标空间＋status 复核选中（命令成功不算数），不符即 select_error 停轮
    # （keep 不发，落点不会漂）。复核不符也先走还原护栏——select 可能已生效只是
    # 复核抖动，焦点不被留在目标空间。目标==当前 shown 不 select（不抢视图）。
    # 选中成功后 keep/复核包在 focus 里，失败路径也经 __exit__ 还原选中。
    state.fact("space_selected", space_target)
    state.phase(None, space_selected=space_target)
    selection_switched = space_target != original_space
    if selection_switched:
        state.phase("space_selecting")
    else:
        state.fact("space_restore", "not_needed")
        state.phase(None, space_restore="not_needed")
    focus_cm = None
    restore_recorded = False
    try:
        if selection_switched:
            # original 是本函数早先为策略/复核读到的事实（A/D 句），传给网关以免
            # 进入时再读一条 status；launch 前的台账写点（D 后、select 前）保持不变。
            focus_cm = herdr.focus(space_target, original=original_space)
            try:
                with focus_cm:
                    placement = keep_and_verify()
            except DispatchError as e:
                if e.kind == "adopt_failed":
                    raise       # keep/复核失败已按外部 kind 映射，原样上抛
                if getattr(e, "stage", None) == "focus_select_mismatch":
                    record_restore(focus_cm)
                    restore_recorded = True
                    fail("select_error", e.message + "，keep 未发", retry)
                fail("select_error", f"space.select 失败：{e.message}", retry)
        else:
            placement = keep_and_verify()
    finally:
        if (selection_switched and not restore_recorded
                and focus_cm is not None and focus_cm.restore is not None):
            record_restore(focus_cm)
    if state.facts.get("space_created"):
        # 清理赠送 shell（绑 space_created，#43 定稿）：从 keep 落地那次 ls 的落点
        # 定位终端所在 space（刚建、只有赠送 tab＋keep tab），其余 tab 即赠送 shell——
        # tab.close 结束终端，再 hc rm 清 finished 条目。复用同一棵 app 树，不再自读 ls。
        for w in herdr.discard_shell_tabs(
                placement.space_id, address, within=adopt_timeout,
                poll=adopt_poll, layout=placement.layout):
            gift_warning(w["reason"], w["detail"])
    state.fact("workspace", "adopted")
    state.phase("adopt_done", workspace="adopted")


# ---------------------------------------------------------------- 顶层兜底


def io_failed_error(e, ledger_opened):
    """中途本地 IO OSError → DispatchError("io_failed")（ADR-0024，#111）。
    顶层兜底只此一处：原子写函数与台账写入原样抛 OSError，冒至 run 顶层由这里
    映射，走与 DispatchError 完全相同的 error 出口。退出码恒 1（环境故障不是
    静态旗标错，不进退出码 2 家族）。message 带操作（异常类名）＋路径＋OSError
    原文；hints 按「台账是否已开」机械分两态：未开→放行 id 未消耗、环境修复后
    同 id 重试；已开→id 已消耗、部分状态如实带出、修复后新放行 id。"""
    if ledger_opened:
        hints = [
            "放行 id 已消耗（台账已开）；修复本机环境（磁盘空间/权限/目录）后用新放行 id 重新放行，"
            "同 id 重试会撞 duplicate_release",
            "部分状态随 error 如实带出；目标会话不杀、不重发",
        ]
    else:
        hints = [
            "放行 id 未消耗（台账未开）；修复本机环境（磁盘空间/权限/目录）后用同一放行 id 重试",
        ]
    return DispatchError("io_failed", f"本地 IO 失败（{type(e).__name__}）：{e}", hints)


# ---------------------------------------------------------------- 一轮


def run(request, deps):
    """跑完一轮，返回 Outcome；永不外抛 DispatchError（ADR-0020 决定 2）。

    只捕 DispatchError；非预期异常照旧冒泡。中途本地 IO 的 OSError 顶层兜底
    映射 io_failed（ADR-0024），走同一 error 出口。
    """
    state = RunState(request, deps)
    try:
        return _run(state)
    except DispatchError as e:
        return _error_outcome(state, e)
    except OSError as e:
        return _error_outcome(state, io_failed_error(e, state.ledger_opened))


def _error_outcome(state, e):
    """统一 error 出口：尽力写终态台账（Journal.fail 内部吞 OSError）、部分状态机械带出。"""
    state.journal.fail(e.kind, e.message)
    state.error = e
    return Outcome(e.returncode, state.project(False), state.result_path, state.notify_path)


def _run(state):
    """一轮的直线编排；失败以 DispatchError 上抛，由 run 顶层统一转 error Outcome。"""
    request = state.request
    deps = state.deps
    herdr = Herdr(deps)

    # ---- admit：静态闸（次序即契约，ADR-0020 决定 3） ----
    # 创建参数成对校验（ADR-0009）：--launch 与 --cwd 同时必填、无默认。
    if bool(request.launch) != bool(request.cwd):
        raise DispatchError("bad_args", "--launch 与 --cwd 须同时传（创建参数成对、无默认）",
                            ["补 --launch <shell 启动命令> --cwd <目录>，或两个都去掉"],
                            returncode=2)
    # 显示名机械校验（ADR-0010）：任何分流都校验。
    if request.display_name is not None:
        dn = request.display_name
        if not dn or len(dn) > DISPLAY_NAME_MAX or DISPLAY_NAME_CONTROL_RE.search(dn):
            raise DispatchError(
                "bad_args", "--display-name 非法（须非空、1–64 字符、禁换行与控制字符）",
                ["改正显示名后用同一放行 id 重试（零发送、id 不消耗）"],
                returncode=2)
    # --space 机械校验（#43 grill 定稿）：必传（argparse required）；空/纯空白 → bad_args；
    # 路径形态须是已存在目录。
    state.fact("space", request.space, ledger=False)   # 请求值回显（传了就带，成功与 error 都带）
    if not request.space.strip():
        raise DispatchError("bad_args", "--space 非法（须非空）",
                            ["传目标空间名/id/路径"], returncode=2)
    if request.space.startswith(("/", "~")):
        if not Path(request.space).expanduser().is_dir():
            raise DispatchError("bad_args",
                                f"--space 路径不是已存在目录：{request.space}",
                                ["传已存在的目录路径，或改传空间名/id"], returncode=2)
    check_structure_and_tools(deps)
    # 抽取后端启动预检（ADR-0005）：api 缺配置立刻报错，不跑完一轮才发现；
    # pi 备选才需要 find_tool(pi)，api 路径不依赖 node/pi。后端实现留在 dispatch
    # （CLI adapter），这里经 Deps 鸭子类型读环境。
    state.backend = deps.resolve_extract_backend()
    if state.backend == "api":
        deps.api_config()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", request.release_id):
        raise DispatchError("bad_release_id", "放行 id 只允许字母数字与 ._-，且不以符号开头", [])
    state.task_name = generate_short_task_name(request.release_id)
    validate_task_name(state.task_name)
    prompt_path = Path(request.prompt_file)
    try:
        raw = prompt_path.read_bytes()
    except OSError as e:
        raise DispatchError("prompt_unreadable", f"读不到提示词文件：{e}", [])
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise DispatchError("prompt_encoding", "提示词文件不是 UTF-8", [])
    nonempty = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not nonempty:
        raise DispatchError("prompt_empty", "提示词文件没有非空行", [])
    anchor = nonempty[0][:24]
    sha = hashlib.sha256(raw).hexdigest()

    runs = Path(request.loop_dir) / "runs"
    # raw_path = 回读原料；turn_path = 本轮原料；record_path = 抽取器裁剪后的干净全文。
    raw_path = runs / f"{request.release_id}.raw.md"
    turn_path = runs / f"{request.release_id}.turn.md"
    record_path = runs / f"{request.release_id}.md"
    state.ledger_path = runs / f"{request.release_id}.json"
    state.result_path = runs / f"{request.release_id}.result.json"
    state.notify_path = runs / f"{request.release_id}.notify.json"
    if state.ledger_path.exists() or raw_path.exists():
        raise DispatchError(
            "duplicate_release",
            f"放行 id {request.release_id} 已有台账或原文落盘，一个放行只跑一轮",
            ["查看台账确认上次结果；要重发须经用户重新放行、用新 id"],
        )
    # 静态形态闸（ADR-0017，#61）：会话起始 skill 命令行必须单独单行一轮。
    normalized = normalize_paste(text)
    split_hit = find_session_start_trigger(normalized)
    if split_hit is not None:
        units = utf16_units(normalized)
        multiline = has_internal_newline(normalized)
        if multiline or units > SPLIT_MAX_UTF16:
            skill, lineno = split_hit
            reasons = []
            if multiline:
                reasons.append("含内部换行（尾部换行不算）")
            if units > SPLIT_MAX_UTF16:
                reasons.append(f"归一化 UTF-16 码元 {units} > 闸值 {SPLIT_MAX_UTF16}")
            raise DispatchError(
                "split_required",
                f"提示词第 {lineno} 行调用会话起始 skill /{skill}，"
                + "、".join(reasons)
                + "：Claude Code 折叠超体积/多行粘贴致 slash 命令不展开，命令行必须单独单行一轮",
                ["改提示词文件为单行命令（只带短参数）后用同一放行 id 重试（零发送、id 不消耗）",
                 "长正文请用户重新放行、新 id 下一轮发；纪律与证据见 SKILL.md「会话起始 skill」（ADR-0017）"],
                returncode=2)
    deps.hc_bin = deps.locate("hc")
    deps.pi_bin = deps.locate("pi") if state.backend == "pi" else None
    if not deps.hc_bin:
        raise DispatchError("missing_tool", "PATH 与常见目录里都找不到 hc", [])
    if state.backend == "pi" and not deps.pi_bin:
        raise DispatchError("missing_tool", "PATH 与常见目录里都找不到 pi",
                            ["某些机器非交互 shell 的 PATH 里没有 node；"
                             "脚本已探 ~/.local/bin、homebrew、nvm node bin 仍未果"])

    # ---- locate/分流（ADR-0009 / ADR-0020 决定 4） ----
    try:
        g = herdr.session(request.target)
    except NotFound:
        g = None
    if g is not None and g.state == "live" and g.address:
        state.fact("target_address", g.address)
    route = classify(g, request)
    gate_route(route, g, request)
    fresh = route.is_fresh
    if route.is_fresh:
        if route is Route.revive:
            # 复活同样过预检 pane 闸（US26：app 没开时所有轮次停摆，无隐性例外）；
            # 启动机械本身不 run、不重写标签（Decision 5）。
            adopt(state, herdr, state.facts["target_address"], True)
    else:
        # 身份已在分流核过（identity_ok）。恒可见（ADR-0009）：既有路径每轮预检
        # （写台账前）核 pane，缺则补领养（park 等造成的可见性漂移自愈）；补不成→
        # adopt_failed，零发送、id 不消耗、同 id 重试。
        adopt(state, herdr, g.address or request.target, True)
        st = g.status_state
        if st not in ("idle", "done"):
            raise DispatchError(
                "not_idle",
                f"目标状态是 {st or '未自报'}，不是 idle/done，不发送",
                ["--if idle 只认 idle；等目标落定或请用户处理后再来"],
            )

    # ---- discipline（ADR-0004）：会话起始 skill 须在空会话或重置后运行 ----
    if request.reset == "none" and not fresh:
        hit = find_session_start_trigger(text)
        if hit is not None:
            turn = g.turn
            is_fresh = isinstance(turn, int) and not isinstance(turn, bool) and turn == 0
            if not is_fresh:
                skill, lineno = hit
                turn_desc = (
                    f"turn={turn}"
                    if isinstance(turn, int) and not isinstance(turn, bool)
                    else "turn 未知"
                )
                raise DispatchError(
                    "reset_required",
                    f"提示词第 {lineno} 行调用会话起始 skill /{skill}，"
                    f"目标当前 {turn_desc}（非全新空会话）：必须先重置目标会话，--reset=none 拒发",
                    ["补 --reset new/clear/compact（agy/pi 认 new，claude 认 clear，codex 认 new/clear）后用同一放行 id 重试",
                     "清单与边界见 SKILL.md「会话起始 skill」；工作流出处 /ask-matt"],
                    returncode=2)

    # ---- open_ledger：发送前再核文件未变，先记账后发送 ----
    # 复核读失败映射既有 prompt_unreadable（ADR-0024）：提示词中途消失保持既有语义。
    try:
        recheck = prompt_path.read_bytes()
    except OSError as e:
        raise DispatchError("prompt_unreadable", f"读不到提示词文件：{e}", [])
    if hashlib.sha256(recheck).hexdigest() != sha:
        raise DispatchError("prompt_changed", "提示词文件在预检期间被改动，不发送", [])
    # Journal.open 是消耗放行 id 的唯一动作：runs 目录在 open 里建、首版落盘。
    state.journal.open({
        "release_id": request.release_id,
        "task_name": state.task_name,
        "target": request.target,
        "prompt_path": str(prompt_path),
        "prompt_sha256": sha,
        "anchor": anchor,
        "reset": request.reset,
        "target_model": request.target_model,
        "launch": request.launch,
        "cwd": request.cwd,
        "display_name": request.display_name,
        "space": request.space,
        "rebuild": request.rebuild,
        "target_address": state.facts.get("target_address"),
        # 预检领养（既有/复活路径）在写台账前，无 phase 可记；结果随台账留痕。
        "workspace": state.facts.get("workspace"),
        "params": {
            "wall_clock": request.timing.wall_clock,
            "check_in": request.timing.check_in,
            "settle_seconds": request.timing.settle_seconds,
            "settle_checks": request.timing.settle_checks,
            "lines": READ_LINES,
            "extract_timeout": request.timing.extract_timeout,
            "extract_backend": state.backend,
        },
        "phase": "sending",
        "created_at": now_iso(),
    })

    # ---- bring_up：Route 三选一 ----
    if route in (Route.create, Route.rebuild):
        if route is Route.rebuild:
            # 强制重建（ADR-0009，用户显式授权）：只杀 idle/done 占位者（预检已核）。
            state.phase("rebuild_killing")
            try:
                herdr.kill(request.target)
            except HerdrFailure as e:
                if e.kind in _HC_PASSTHROUGH_KINDS:
                    raise
                raise DispatchError("launch_failed", f"重建 kill 占位者失败：{e.message}",
                                    e.hints + ["占位者未死；本轮停止，经用户放行后另起一轮"])
            state.fact("rebuilt", True)
            state.fact("rebuilt_old_target", state.facts.get("target_address"))
            state.phase(None, rebuilt=True,
                        rebuilt_old_target=state.facts["rebuilt_old_target"])
        do_create(state, herdr, request)
        adopt(state, herdr, state.facts["target_address"], False)
        wait_launch_agent(state, herdr, request)
    elif route is Route.revive:
        # 复活（ADR-0009）：不 run、不重写标签；原地重发启动命令→等 agent 就绪→核身份
        # （领养已在预检做过）。名字/cwd/终端级历史不动，会话是空的。
        send_launch_command(state, herdr, request)
        wait_launch_agent(state, herdr, request)

    # ---- name_tab（ADR-0010）：领养复核 pane 落地后、前置/主任务发送前 ----
    if request.display_name is not None:
        apply_display_name(state, herdr, state.facts.get("target_address"),
                           request.display_name)

    # ---- learning_prepare（ADR-0012）：开启时只读把 --space 解析成 space id ----
    state.learning_on = learning.enabled(deps.env)
    learning_store = None
    learning_space_id = None
    if state.learning_on:
        learning_store = learning.LearningStore()
        herdr.require_app()
        learning_space_id = resolve_space_id_for_learning(herdr, request.space)
        if learning_space_id is None:
            state.fact("learning_warning", {
                "reason": "space_id_missing",
                "detail": "--space 未能只读解析出唯一 space id，本轮序列/案例/few-shot 禁用"},
                ledger=False)

    # ---- send_prestep（ADR-0020 决定 6）：重置 → 切模型 ----
    plan = prestep_specs(request, route)
    if plan.reset_skipped is not None:
        state.fact("reset_skipped", plan.reset_skipped)
        state.phase(None, reset_skipped=plan.reset_skipped)
    for spec in plan.specs:
        if spec.kind == "reset":
            state.phase("reset_sending")
            pre_send, pre_resent = do_send(state, herdr, request, spec.command)
            state.reset_sent = True
            state.phase("reset_waiting", reset_send=pre_send, reset_resent=pre_resent)
            wait_for_reset(deps, herdr, request,
                           deps.monotonic() + request.timing.wall_clock)
            state.phase("reset_done", reset_done_at=now_iso())
        else:
            state.phase("model_sending")
            pre_send, pre_resent = do_send(state, herdr, request, spec.command)
            state.model_sent = True
            state.phase("model_waiting", model_send=pre_send, model_resent=pre_resent)
            wait_for_reset(deps, herdr, request,
                           deps.monotonic() + request.timing.wall_clock)
            state.phase("model_done", model_done_at=now_iso())

    # ---- send_main：墙钟从主任务发送成功起计，不重置 ----
    state.started = deps.monotonic()
    send, resent = do_send(state, herdr, request, text)
    baseline = send["turn"]
    state.phase("sent_waiting", sent_at=now_iso(), send=send, resent=resent)

    # ---- learning_settle（ADR-0012）：主任务发送成功后结算上一轮预测 ----
    if state.learning_on:
        lr = learning_store.settle_and_record(request.target, request.release_id,
                                              learning_space_id, nonempty[0])
        if lr.get("feedback") is not None:
            state.fact("next_skill_feedback", lr["feedback"], ledger=False)
        if lr.get("warning") is not None:
            state.fact("learning_warning", lr["warning"], ledger=False)

    # ---- await_result ----
    deadline = state.started + request.timing.wall_clock
    final = wait_for_result(deps, herdr, request, baseline, deadline)

    # ---- readback：回读＋断轮＋完整性检查 ----
    read = herdr.readback(request.target)
    screen = read.text
    # 锚点只作信息、不阻断（ADR-0013）。
    anchor_found = verbatim_present(anchor, screen)
    turn_text, echo_found, cut_line = cut_turn(screen, anchor)
    reasons = ["fold_markers"] if fold_hits(turn_text, text) else []
    complete = not reasons
    elapsed = round(deps.monotonic() - state.started, 1)
    raw_path.write_text(screen, encoding="utf-8", newline="")
    turn_path.write_text(turn_text, encoding="utf-8", newline="")
    state.phase(
        "read", read_at=now_iso(),
        turn=final.turn, status=final.status_state,
        elapsed_s=elapsed, chars=len(screen),
        echo_found=echo_found, turn_cut_line=cut_line, turn_chars=len(turn_text),
        hc_read={"complete": read.raw.get("complete"), "how": read.raw.get("how"),
                 "why": read.raw.get("why")},
        complete=complete, complete_reason=reasons, anchor_found=anchor_found,
        run_record=str(raw_path), raw_record=str(raw_path), turn_record=str(turn_path),
    )

    # ---- extract_verify：裁剪＋抽取＋逐字校验 ----
    state.phase("extracting")
    examples_used = 0
    sequence_used = False
    seq_snapshot = []
    injection = ""
    if state.learning_on:
        inj = learning_store.build_injection(learning_space_id, request.target)
        injection = inj.get("text", "")
        examples_used = inj.get("examples_used", 0)
        sequence_used = inj.get("sequence_used", False)
        seq_snapshot = inj.get("sequence", [])
        if inj.get("warning") is not None:
            state.fact("learning_warning", inj["warning"], ledger=False)
    extraction = deps.extract(request, turn_path, injection)
    reply = extraction.reply
    fields = extraction.fields
    # 逐字校验语料＝本轮原料（ADR-0013）：旧轮文本不参与通过判定。
    rewritten = verify_extraction(fields, turn_text, turn_path, raw_path, state.backend)
    bad_reply_lines = verify_reply(reply, turn_text)
    if bad_reply_lines:
        rewritten["reply"] = closest_fragment(bad_reply_lines[0], turn_text)
    record_path.write_text(reply, encoding="utf-8", newline="")
    state.fact("rewritten", rewritten)
    state.fact("run_record", str(record_path))
    state.fact("reply_chars", len(reply))
    state.fact("reply_bad_lines", len(bad_reply_lines))
    # 创建待观察预测（ADR-0012）：每轮成功抽取都创建（含空预测）。
    if state.learning_on:
        pr = learning_store.record_prediction(
            request.target, request.release_id, learning_space_id,
            fields.get("next_skill", ""), extraction.next_skill_invalid,
            reply, seq_snapshot, examples_used, sequence_used, complete=complete)
        if pr.get("warning") is not None:
            state.fact("learning_warning", pr["warning"], ledger=False)

    # ---- finish ----
    state.final_turn = final.turn
    state.final_status = final.status_state
    state.complete = complete
    state.complete_reason = reasons
    state.anchor_found = anchor_found
    state.elapsed = elapsed
    state.chars = len(screen)
    state.record_path = record_path
    state.raw_path = raw_path
    state.turn_path = turn_path
    state.prompt_sha = sha
    state.rewritten = rewritten
    state.ext_fields = fields
    state.phase("done", done_at=now_iso())
    return Outcome(0, state.project(True), state.result_path, state.notify_path)
