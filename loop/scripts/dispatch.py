#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""dispatch.py — loop skill 的分发脚本（ADR-0002，方案 C）。

运行方式：python3 scripts/dispatch.py …（脚本自带版本门：>=3.10 直跑；<3.10
自动经 uv run --no-python-downloads 用机器上已有的 3.10+ 解释器重跑，不下载。）

机械活：读放行提示词 → 核对目标 → hc send --if idle → hc wait 循环 →
hc read → 完整性检查 → 整屏原料落盘 .raw.md → 裁剪出干净全文 reply 落盘 .md
＋抽取选段字段（默认直连 API，pi 显式备选，ADR-0005）→ 合并成一行 JSON。
异常直接返回 error（退出码非零），不进抽取。只用 Python 标准库。

机械保证（不靠提示词）：
  - 原文照发：主任务文件内容一字不改地作为 hc send 的位置参数。
  - 重置前置（--reset new/clear/compact，默认 none）：放行一并授权前置＋主任务；
    先发字面命令、等落定（idle/done + 屏幕静置）再发主任务；前置按枚举校验、不抽取。
  - 切模型前置（--target-model <名字>，ADR-0006）：重置（如有）之后、主任务之前发
    字面命令 /model <名字>，等落定再发主任务；名字原样透传、不核实生效、不预读跳过、
    不按 agent 映射，与 --reset 独立可单独带；target_model 是请求值非验证值。
  - 命名会话 get-or-create（ADR-0009）：--target 推荐传会话名（一名一 live 终端，名字恒解析
    到最新 live）。预检 hc get 分流：not_found/finished→创建（hc run --bg --keep --name --cwd
    -- zsh -l 单形态 shell 包装→打 loop=1/loop-agent 标签→等 shell 就绪→裸 send --launch
    启动命令→等 agent 就绪→核身份）；live＋空闲 shell＋loop 标签符→复活（原地重发启动命令，
    名字/cwd/终端级历史不动）；live＋别的 agent/程序或无 loop 标签→session_occupied 停轮呈报，
    --rebuild 经用户显式授权只杀 idle/done 占位者后同名重建。--launch 字面透传 shell 命令、
    --cwd 显式给目录（二者成对必填、无默认、脚本零映射）；需创建/复活却没传→launch_required
    （退出码 2、零发送、id 不消耗、补参同 id 重试）。启动命令是命令不是提示词：不抽取、不落
    run_record，占前置发送槽。换 kind＝换 --target 到另一命名会话（--switch-agent 已移除）。
  - 恒可见（领养，ADR-0009）：命名会话恒有 herdr-app pane。创建路径就绪后、既有/复活路径每轮
    预检（写台账前）经单一 app_json 咽喉：herdr-app status 探测（绝不用裸 herdr-app，它会开窗）
    →只读 ls 核 pane→缺则 do terminal.keep→ls 复核落地；本机无 space 先 space.new(focus:false)
    →keep→复核→tab.close confirmed:true＋hc rm 清赠送 shell。进不了即 adopt_failed 整轮报错、
    不降级 headless：创建路径保留已建终端、id 消耗；预检路径零发送、id 不消耗。app 没开时所有
    轮次停摆（已接受的代价）。
  - 显示名层（ADR-0010）：--display-name 调用方显式传完整字符串，脚本不派生、不拼接、不理解、
    不校验式样，只机械校验（非空、1–64 字符、禁换行控制字符，非法 bad_args）＋落地。新鲜路径
    （创建/复活/重建）必传，缺则 display_name_required（与 launch_required 同构：退出码 2、
    零发送、id 不消耗、补参同 id 重试；两参同缺 launch_required 先报）；复用轮可选，传了就覆盖、
    不传不碰。落地时序：领养复核 pane 落地后、前置/主任务发送前，find_tab 定位 tab id→tab.rename
    →ls 复核 tab.name==显示名（命令成功不算数）；台账 naming phase。失败降级告警不丢放行：
    rename 失败/复核不符/无 tab→display_name_warning（rename_error/rename_mismatch/no_tab），
    主流程继续；pane 落地仍是硬闸（adopt_failed 不变）。result display_name 回显本轮传入值
    （落地步骤一跑就带，含 no_tab/ls 失败等降级情形，成功与 error 都带）。不写 summary 标签
    （机器可读靠自动跟随 tab 名的 where 标签）；space.new 的 name 维持会话名。
  - 一个放行至多发送成功三次（重置或启动前置＋切模型前置＋主任务，各一次；刚创建/复活的
    新鲜会话自动跳过重置，result 记 reset_skipped:"fresh_launch"，切模型前置照跑）：先写台账
    $TMPDIR/loop/runs/<放行id>.json 再发送；台账存在即拒绝。
  - 会话起始 skill 纪律（ADR-0004）：提示词行首出现 /wayfinder、/grill-with-docs、
    /implement、/implement-spec、/prototype 之一且 --reset=none 时，在 hc get ＋
    身份预检之后、写台账前校验：turn == 0（全新空会话）直接豁免放行；turn > 0
    或缺失时拒发（reset_required，退出码 2），放行 id 不消耗，补 --reset 同 id 可重试。
    分流判定创建/复活/重建的新鲜会话视同 turn 0 豁免、不看终端 turn（turn 是终端级
    累计计数，复活会话 turn>0 但会话是空的，ADR-0009 修正 ADR-0007）。
  - 首发只用 --if idle；被拒（status_mismatch）且 hc get 复核为 done 时，
    再核一次非 working/blocked 后不带 --if 补发一次（被拒时零字节进入，不算重发）。
    永不 --force，不碰 hc keys。
  - 发送前核对目标身份（--expect-proc，认 foreground.argv[0]/status.app，可选但强烈建议传；
    foreground.name 不可靠——claude 实测为版本号，不作判据，ADR-0009）。
  - 台账记录实际发出内容的 sha256，成功输出也带 prompt_sha256。
  - 完整性判据：折叠标记（▲▼）命中即 error，不进 pi；原文照常落盘。锚点（提示词
    回声首行前缀）只作信息 anchor_found、不阻断——检查在 turn>基线＋落定之后，
    长任务回声滚出当前屏是常态，回复仍在屏底。
  - 工具定位：hc/uv（及 backend=pi 时的 pi）先 shutil.which，PATH 找不到时自动探常见目录（~/.local/bin、
    homebrew、nvm node bin），后台 job 缺 PATH 也能起。
  - 本地记录归置：--loop-dir 默认 $TMPDIR/loop（tempfile.gettempdir()/loop），runs/ 放
    记录、releases/ 放放行提示词；随 OS 临时清理、不留持久审计，台账拒重发保护止于
    重启（放行 id 每次唯一）。PATH 由 find_tool 兜底，不需手写包装脚本。
  - 抽取字段逐字校验：搜不到原文的字段标 rewritten 并附最接近片段；
    全部搜不到则 error(extraction_rewritten)。reply 干净全文先整体、再逐行校验，
    未过逐字校验的行标 rewritten["reply"]。next_command 是推测字段（ADR-0008）：
    下一轮该发的完整斜杠指令，豁免逐字校验，缺失/非字符串按 ""，不整轮报错。
  - 抽取后端（ADR-0005）：LOOP_EXTRACT_BACKEND=api|pi，默认 api。api 走 OpenAI 兼容
    chat completions（LOOP_API_BASE/KEY/MODEL 全必填无默认，启动预检缺失即
    api_not_configured）；temp 0、max_tokens 默认 32768（LOOP_API_MAX_TOKENS 可覆盖）、
    超时复用 --extract-timeout、不重试不流式；代理与 webhook 同源（ADR-0003）；
    失败只记状态码/类名，不读响应体，密钥/URL 零回显。pi 备选路径照旧。

交付：stdout 一行 JSON，同一字符串同时写 $TMPDIR/loop/runs/<放行id>.result.json
（成功与 error 都写）。run_record 指 pi 裁剪后的干净全文 .md（回看对象），
raw_record 指整屏原料 .raw.md（审计）。台账记过程，结果文件记最终交付，
重发拒绝仍以台账为准。
可选 webhook 完成通知：env LOOP_WEBHOOK_URL＋LOOP_WEBHOOK_SECRET 都配了才发，
成功/报错都 POST 结果 JSON（鉴权走 Authorization: Bearer），发失败不连累整轮，
结果记 webhook: off/sent/failed，失败另记 webhook_error（HTTP 状态码或异常类名）
与 webhook_error_detail（脱敏诊断：异常类名、reason 类名、errno/errno_name、是否
超时、SSL 验证错误码、HTTP 状态码、代理是否配置/是否命中）；不收 str(e)/repr(e)/
字符串 reason/响应体，不含 URL、密钥、主机名、头内容。stderr 打印同一份诊断。
出网默认跟随本机系统代理：标准 http_proxy/https_proxy 显式配置时优先，否则读
系统代理（macOS），系统例外表或 no_proxy 命中则直连；只作用于 webhook POST，
不改证书校验、不关代理、不换网络策略。长任务用调用方自己的后台任务跑、靠完成通知收结果
（DS Bot 用 job 完成通知、Grok Bot 用 webhook，见 SKILL.md 交付清单），
不要用 hc exec 或短超时包装器同步等。

已裁定默认值（issue #16，2026-11-19）：墙钟 3600s（发送成功起计、不重置）、
check-in 180s、落定静置 10s × 2 次、pi 抽取护栏 1200s、hc read --lines 0。
"""

import argparse
import difflib
import errno
import hashlib
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# hc 任务名规则
TASK_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,31}$")
RESERVED_ID_RE = re.compile(r"^t\d+$")
# herdr 会话名规则（hc run --name：1–32 位 a-z 0-9 - _）；t 号是保留地址、不能作名字。
SESSION_NAME_RE = re.compile(r"^[a-z0-9_-]{1,32}$")
# 显示名机械校验（ADR-0010）：非空、1–64 字符、禁换行与控制字符（C0＋DEL）。不校验式样。
DISPLAY_NAME_MAX = 64
DISPLAY_NAME_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")

def generate_short_task_name(release_id: str) -> str:
    h = hashlib.sha256(release_id.encode("utf-8")).hexdigest()[:8]
    name = f"loop-{h}"
    return name[:32]

def validate_task_name(name: str) -> None:
    if not TASK_NAME_RE.match(name) or RESERVED_ID_RE.match(name):
        raise DispatchError("bad_task_name", f"任务名不合法：{name}", ["使用自动生成的短名称"])

def default_tool_dirs():
    """PATH 之外的常见工具目录：后台 job 常缺 PATH（pi 在 nvm node bin、hc 在 ~/.local/bin）。
    只用通用位置，不含机器专属路径。"""
    home = Path.home()
    dirs = [home / ".local/bin", Path("/opt/homebrew/bin"), Path("/usr/local/bin")]
    nvm = home / ".nvm/versions/node"
    if nvm.is_dir():
        dirs += sorted(nvm.glob("*/bin"))
    return dirs


def find_tool(name, probe_dirs=None):
    """先 shutil.which（尊重显式 PATH）；找不到再探常见目录。probe_dirs 可覆盖供测试。
    返回可执行文件路径或 None。"""
    p = shutil.which(name)
    if p:
        return p
    dirs = default_tool_dirs() if probe_dirs is None else probe_dirs
    for d in dirs:
        cand = Path(d) / name
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand)
    return None


def check_structure_and_tools():
    script_dir = Path(__file__).resolve().parent
    extract_md = script_dir.parent / "prompts" / "extract.md"
    if not extract_md.is_file():
        raise DispatchError("missing_structure", f"找不到 {extract_md}", [])
    for tool in ("hc", "pi", "uv"):
        if not find_tool(tool):
            raise DispatchError("missing_tool", f"找不到 {tool}",
                                ["确认 PATH；脚本也会探 ~/.local/bin、homebrew、nvm node bin"])


def handle_old_python():
    """自身解释器 <3.10：探 uv 重跑（uv run --no-python-downloads 挑已有 3.10+，
    不下载）；探不到 uv 或已重 exec 过则报 python_too_old 退出。调用方只需
    python3 直跑，uv 退居内部兜底，后台精简 PATH 也能起。"""
    uv = find_tool("uv")
    if uv and not os.environ.get("LOOP_UV_REEXEC"):
        os.environ["LOOP_UV_REEXEC"] = "1"
        try:
            os.execv(uv, [uv, "run", "--no-python-downloads",
                          str(Path(__file__).resolve()), *sys.argv[1:]])
        except OSError:
            pass  # execv 失败（如 uv 不可执行），落到下面报错
    print(json.dumps({
        "ok": False,
        "error": {
            "kind": "python_too_old",
            "message": "需要 Python >= 3.10，当前 %d.%d.%d" % sys.version_info[:3],
            "hints": ["装一个 3.10+ 解释器，或装 uv（脚本会自动经 "
                      "uv run --no-python-downloads 用已有 3.10+ 重跑）"],
        },
    }, ensure_ascii=False))
    sys.exit(1)


# 版本门：写成 3.9 也能解析的形式。低于 3.10 先尝试经 uv 自举重跑，不成再报错
# （3.9 的 Path.write_text 没有 newline 参数，会在落盘时半路崩）。
if sys.version_info < (3, 10):
    handle_old_python()

# 机械护栏（非等待策略，不属待拍板项）：
# hc send 会等人未写完的草稿（draft_wait 默认 60 秒），护栏须大于它。
SEND_GUARD_S = 120.0
# hc get / hc read 都是即时查询，30 秒足够宽。
QUERY_GUARD_S = 30.0
# hc wait 子进程护栏 = 本次 --timeout + 余量。
WAIT_GUARD_MARGIN_S = 30.0
# 裁定 A3：只取当前屏。对 grok，--lines > 0 时 hc 的 complete 恒 false，语义失真。
READ_LINES = 0
# 前置落定用短轮询而非 hc wait：/clear 是本地命令、可能不进 working，
# hc wait 会空等一个 check-in。working 时每 RESET_POLL_S 秒 hc get 查一次。
RESET_POLL_S = 2.0
# 领养复核轮询（#40）：keep 后台异步落地，单次 ls 会跑在落地前误判 no_pane，带超时轮询等落地。
ADOPT_TIMEOUT_S = 30.0
ADOPT_POLL_S = 2.0
# 可选 webhook 的 POST 超时：只是完成通知，短超时即可，发失败不连累整轮。
WEBHOOK_TIMEOUT_S = 10.0
# webhook 主出口重试退避（共 3 次：首发 + 2 次重试）；全败且用了代理时再直连兜底 1 次。
WEBHOOK_RETRY_DELAYS = (0.5, 1.0)

# summary 已移除（原文才是重点）：pi 若仍多给 summary，投影时直接丢弃，
# 不算坏行、不进 rewritten。
# next_command（ADR-0008）是唯一推测字段：下一轮该发的完整斜杠指令，
# 缺失/非字符串按 "" 投影，不整轮报错；不参与逐字校验。
EXTRACT_FIELDS = ("needs_user", "questions", "conclusion", "blocked", "reply", "next_command")

# 会话起始 skill（ADR-0004）：新会话必须以其中之一开场，调用时目标会话必须先重置。
# 清单出自 /ask-matt 工作流（默认调用方已装 Matt 全套 skill）；中途 skill
# （to-spec/to-tickets/tdd/code-review/retro 等）不在内，普通回复不触发。
SESSION_START_SKILLS = ("wayfinder", "grill-with-docs", "implement", "implement-spec", "prototype")
# 行首匹配：允许行前空白，命令后须不是词字符或连字符（/implementing、/implement-foo
# 不误中，/wayfinder#7 无空格带参算调用）；implement-spec 排在 implement 前，命中时报长名。
# 行文中提及不触发——讨论工作流的普通回复要放行；「调用独占一行」由 SKILL.md 纪律约定。
SESSION_START_RE = re.compile(
    r"^[ \t]*/(implement-spec|implement|wayfinder|grill-with-docs|prototype)(?![\w-])",
    re.MULTILINE)


def find_session_start_trigger(text):
    """行首斜杠命令命中会话起始 skill 时返回 (skill, 行号)，否则 None。"""
    m = SESSION_START_RE.search(text)
    if m is None:
        return None
    return m.group(1), text.count("\n", 0, m.start()) + 1


# 抽取后端（ADR-0005）：默认直连 API（OpenAI 兼容 chat completions），pi 为显式备选。
# API 参数全走 env、全必填、无内置默认；密钥与 URL 零回显。
EXTRACT_BACKENDS = ("api", "pi")
API_MAX_TOKENS_DEFAULT = 32768


def resolve_extract_backend():
    """LOOP_EXTRACT_BACKEND=api|pi，默认 api；非法值是参数类错误（退出码 2）。"""
    v = os.environ.get("LOOP_EXTRACT_BACKEND", "api").strip().lower()
    if v not in EXTRACT_BACKENDS:
        raise DispatchError("bad_extract_backend",
                            "LOOP_EXTRACT_BACKEND 值非法（只认 api|pi）", [], returncode=2)
    return v


def api_config():
    """读 API 三个 env＋可选 max_tokens；缺必填项报 api_not_configured
    （只列变量名，不带值），max_tokens 非法报 bad_api_max_tokens。启动预检用。"""
    names = ("LOOP_API_BASE", "LOOP_API_KEY", "LOOP_API_MODEL")
    vals = {n: os.environ.get(n, "").strip() for n in names}
    missing = [n for n in names if not vals[n]]
    if missing:
        raise DispatchError(
            "api_not_configured",
            "抽取后端=api 但 env 缺失：" + ", ".join(missing),
            ["配齐三个变量（见 webhook.env.example 抽取配置节）后重试",
             "或设 LOOP_EXTRACT_BACKEND=pi 走 pi 备选"],
            returncode=2)
    try:
        max_tokens = int(os.environ.get("LOOP_API_MAX_TOKENS", "").strip()
                         or API_MAX_TOKENS_DEFAULT)
        if max_tokens <= 0:
            raise ValueError
    except ValueError:
        raise DispatchError("bad_api_max_tokens",
                            "LOOP_API_MAX_TOKENS 须为正整数", [], returncode=2)
    return (vals["LOOP_API_BASE"].rstrip("/"), vals["LOOP_API_KEY"],
            vals["LOOP_API_MODEL"], max_tokens)


class DispatchError(Exception):
    def __init__(self, kind, message, hints=None, returncode=1):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.hints = hints or []
        self.returncode = returncode


class HcFailure(DispatchError):
    def __init__(self, kind, message, hints, returncode, detail):
        super().__init__(kind, message, hints)
        self.returncode = returncode
        self.detail = detail


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def try_json(s):
    try:
        return json.loads(s)
    except (ValueError, TypeError):
        return None


def run_cmd(argv, timeout_s):
    try:
        return subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
        )
    except FileNotFoundError:
        raise DispatchError(
            "missing_tool",
            f"找不到可执行文件：{argv[0]}",
            ["确认 PATH；某些机器非交互 shell 的 PATH 里没有 node，"
             "pi 起不来时先确认 PATH 并显式补上"],
        )
    except subprocess.TimeoutExpired:
        raise DispatchError(
            "tool_timeout",
            f"{argv[0]} 子进程超过护栏 {timeout_s:.0f} 秒未退出",
            ["目标或模型可能挂起；本轮已停止，未再发送任何内容"],
        )


def hc_json(hc_bin, args, timeout_s):
    """跑一条 hc 命令并解析 --json 输出；非零退出抛 HcFailure。"""
    # --json 必须紧跟子命令：send 用 `--` 保护以 - 开头的提示词文本，
    # `--` 之后的一切都被 clap 当位置参数，选项放后面会被吞掉。
    p = run_cmd([hc_bin, args[0], "--json", *args[1:]], timeout_s)
    if p.returncode != 0:
        detail = try_json(p.stdout) or try_json(p.stderr)
        kind = None
        if isinstance(detail, dict):
            kind = detail.get("kind") or (detail.get("error") or {}).get("kind")
        raise HcFailure(
            kind or "hc_failed",
            f"hc {' '.join(args)} 退出码 {p.returncode}"
            + (f"（{kind}）" if kind else ""),
            [s.strip()[:300] for s in (p.stderr, p.stdout) if s and s.strip()],
            p.returncode,
            detail,
        )
    data = try_json(p.stdout)
    if not isinstance(data, dict):
        raise DispatchError(
            "hc_output",
            f"hc {' '.join(args)} 的 stdout 不是 JSON 对象",
            [p.stdout.strip()[:300]],
        )
    return data


class AppFailure(DispatchError):
    """herdr-app 命令失败（镜像 HcFailure）。detail 是 app 的 JSON 错误对象（可能为 None）。"""

    def __init__(self, kind, message, hints, returncode, detail):
        super().__init__(kind, message, hints)
        self.returncode = returncode
        self.detail = detail


def app_json(app_bin, args, timeout_s):
    """跑一条 herdr-app 命令并解析 JSON 输出；非零退出抛 AppFailure。
    所有 herdr-app 调用经此单一咽喉（与 hc_json 同为可注入桩点）。
    herdr-app 的 ls/status/do 原生输出 JSON，不插 --json；do 的错误形状是
    {"error": "..."}＋退出码 1。绝不用裸 herdr-app 作探针（它会打开窗口）。"""
    p = run_cmd([app_bin, *args], timeout_s)
    if p.returncode != 0:
        detail = try_json(p.stdout) or try_json(p.stderr)
        err = None
        if isinstance(detail, dict):
            err = detail.get("error")
            if isinstance(err, dict):
                err = err.get("kind") or err.get("message")
        raise AppFailure(
            "app_failed",
            f"herdr-app {' '.join(args[:2])} 退出码 {p.returncode}"
            + (f"：{str(err)[:200]}" if err else ""),
            [s.strip()[:300] for s in (p.stderr, p.stdout) if s and s.strip()],
            p.returncode,
            detail,
        )
    data = try_json(p.stdout)
    if not isinstance(data, dict):
        raise DispatchError(
            "app_output",
            f"herdr-app {' '.join(args[:2])} 的 stdout 不是 JSON 对象",
            [p.stdout.strip()[:300]],
        )
    return data


def status_state(terminal):
    st = terminal.get("status")
    return st.get("state") if isinstance(st, dict) else None


def write_json_atomic(path, obj):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(path)


def _errno_label(obj, num):
    """给 errno 数字配一个名字。gaierror 先查 socket.EAI_*（DNS 负数/平台值，
    如 macOS 的 8 不是 POSIX 的 8）；其余先查 errno.errorcode。查不到就给 None。"""
    if not isinstance(num, int):
        return None
    if not isinstance(obj, socket.gaierror):
        name = errno.errorcode.get(num)
        if name:
            return name
    names = sorted(n for n in dir(socket)
                   if n.startswith("EAI_") and getattr(socket, n) == num)
    return names[0] if names else None


def _proxy_bypass(host):
    """系统例外表或显式 no_proxy 任一命中即直连。检查失败按未命中处理。"""
    if not host:
        return False
    fns = [getattr(urllib.request, "proxy_bypass_macosx_sysconf", None),
           getattr(urllib.request, "proxy_bypass_environment", None)]
    if all(fn is None for fn in fns):
        fns = [getattr(urllib.request, "proxy_bypass", None)]
    for fn in fns:
        if fn is None:
            continue
        try:
            if fn(host):
                return True
        except Exception:
            continue
    return False


def webhook_proxy_resolution(url):
    """决定 webhook 这一发怎么出网，返回 (proxies, source)。

    默认跟随本机系统代理：标准环境变量 http_proxy/https_proxy 显式配置时优先
    （source=env）；否则读系统代理（macOS SystemConfiguration，source=system）；
    系统例外表或 no_proxy 命中则直连（source=bypass），没配代理是 none。
    只作用于 webhook 的 POST，不改进程全局代理，也不动证书校验。
    注意不能用 getproxies() 直接判：只设了 no_proxy/非标准 *_proxy 时它也会返回
    非空字典，却没有任何 http/https 键，urllib 实际不会走代理。"""
    try:
        env = urllib.request.getproxies_environment()
    except Exception:
        env = {}
    explicit = {k: v for k, v in env.items() if k in ("http", "https")}
    if explicit:
        return explicit, "env"
    try:
        mac = getattr(urllib.request, "getproxies_macosx_sysconf", None)
        if mac is not None:
            system = mac() or {}
        else:
            # 其它平台：getproxies() 已覆盖环境变量/Windows 注册表，
            # 只保留 urllib 真会分派的 scheme 键。
            system = {k: v for k, v in urllib.request.getproxies().items()
                      if k in ("http", "https")}
    except Exception:
        return {}, "unknown"
    if not system:
        return {}, "none"
    parts = urllib.parse.urlsplit(url)
    scheme, host = parts.scheme.lower(), parts.hostname
    if not host or scheme not in ("http", "https"):
        return {}, "none"
    if _proxy_bypass(host):
        return {}, "bypass"
    if scheme in system:
        return {scheme: system[scheme]}, "system"
    return {}, "none"


def _proxy_diagnostic(url):
    """与 webhook_proxy_resolution 同源：只报是否配置、是否实际命中、来源
    （env/system/bypass/none/unknown），不输出代理地址、端口、用户名或密码。"""
    try:
        proxies, source = webhook_proxy_resolution(url)
    except Exception:
        return {"configured": "unknown", "matches": "unknown", "source": "unknown"}
    if source == "unknown":
        return {"configured": "unknown", "matches": "unknown", "source": "unknown"}
    if proxies:
        return {"configured": "yes", "matches": "yes", "source": source}
    return {"configured": "yes" if source == "bypass" else "no",
            "matches": "no", "source": source}


def webhook_open(req, timeout, proxies):
    """webhook 请求出口。proxies 非空时走该代理；为空 dict 时显式传
    ProxyHandler({}) 强制直连（不回退读取环境或系统代理）。独立函数便于测试替换，不碰全局 opener。"""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler(proxies or {}))
    return opener.open(req, timeout=timeout)


def webhook_error_detail(exc, url):
    """把 webhook 失败整理成脱敏诊断对象。只收类名、数字、布尔与枚举字符串：
    不读 str(e)/repr(e)/响应体，不记录字符串 reason（可能带主机/地址）、
    URL、密钥或请求头。未知项直接省略，代理状态未知标 "unknown"。"""
    reason = getattr(exc, "reason", None)
    detail = {
        "type": type(exc).__name__,
        "is_timeout": isinstance(exc, TimeoutError) or isinstance(reason, TimeoutError),
        "proxy": _proxy_diagnostic(url),
    }
    if isinstance(exc, urllib.error.HTTPError):
        # HTTPError 是 URLError 子类；只保留状态码，服务端 reason 短语不收。
        detail["http_status"] = exc.code
        return detail
    if isinstance(reason, BaseException):
        detail["reason_type"] = type(reason).__name__
    errno_obj, errno_val = None, None
    for obj in (reason, exc):
        # SSLError/SSLCertVerificationError 的 errno 是 OpenSSL 错误码，不是系统
        # errno（如 1 会被误读成 EPERM）；证书校验码单独在 ssl_verify_code 里给。
        if isinstance(obj, ssl.SSLError):
            continue
        if isinstance(obj, BaseException):
            v = getattr(obj, "errno", None)
            if isinstance(v, int):
                errno_obj, errno_val = obj, v
                break
    if errno_val is not None:
        detail["errno"] = errno_val
        label = _errno_label(errno_obj, errno_val)
        if label:
            detail["errno_name"] = label
    for obj in (reason, exc):
        if isinstance(obj, ssl.SSLCertVerificationError):
            code = getattr(obj, "verify_code", None)
            if isinstance(code, int):
                detail["ssl_verify_code"] = code
            break
    return detail


def _is_retryable_webhook_error(exc):
    """4xx（含 401/403/404）属于认证或请求错，不重试也不直连兜底；
    传输层异常与 HTTP 5xx 可重试。"""
    if isinstance(exc, urllib.error.HTTPError):
        return 500 <= exc.code < 600
    return True


def _webhook_error_label(exc):
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code}"
    return type(exc).__name__


def send_webhook(result):
    """可选 webhook 完成通知（Grok Bot 等支持 webhook 唤醒的调用方用）。
    env LOOP_WEBHOOK_URL＋LOOP_WEBHOOK_SECRET 都配了才发，默认关。成功与报错都
    POST 结果 JSON；鉴权走 Authorization: Bearer <密钥>，不进日志、不进 stdout。
    webhook 只是通知，结果文件才是事实源：发失败不连累整轮。
    出网默认跟随本机系统代理（见 webhook_proxy_resolution）：主出口最多尝试 3 次
    （退避 0.5s、1.0s）；若用了代理且 3 次均因瞬态/5xx 失败，再强制直连（proxies={}）
    兜底 1 次；HTTP 4xx 等非 5xx 状态码立刻失败、不重试也不兜底。
    返回 (off/sent/failed, webhook_error, webhook_error_detail)；失败时
    webhook_error 与 webhook_error_detail 保留主出口最后一次错误（与 detail["proxy"]
    一致），另带 attempts（总尝试次数），走过直连兜底时附 fallback_direct 与
    fallback_error，stderr 打印同一份脱敏诊断，都不含 URL/密钥/主机名/头内容。"""
    url = os.environ.get("LOOP_WEBHOOK_URL")
    secret = os.environ.get("LOOP_WEBHOOK_SECRET")
    if not url or not secret:
        return "off", None, None
    body = json.dumps(result, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json",
               "Authorization": "Bearer " + secret}
    proxies, _source = webhook_proxy_resolution(url)
    attempts = 0
    primary_exc = None
    max_primary = 1 + len(WEBHOOK_RETRY_DELAYS)
    for i in range(max_primary):
        attempts += 1
        try:
            # 每次新建 Request：urllib ProxyHandler.proxy_open 会原地改写 req.host，复用会污染直连兜底
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            with webhook_open(req, WEBHOOK_TIMEOUT_S, proxies) as resp:
                resp.read()
            return "sent", None, None
        except Exception as e:
            primary_exc = e
            if not _is_retryable_webhook_error(e):
                break
            if i < len(WEBHOOK_RETRY_DELAYS):
                time.sleep(WEBHOOK_RETRY_DELAYS[i])
    fallback_exc = None
    fallback_direct = False
    if proxies and _is_retryable_webhook_error(primary_exc):
        fallback_direct = True
        attempts += 1
        try:
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            with webhook_open(req, WEBHOOK_TIMEOUT_S, {}) as resp:
                resp.read()
            return "sent", None, None
        except Exception as e:
            fallback_exc = e
    err = _webhook_error_label(primary_exc)
    detail = webhook_error_detail(primary_exc, url)
    detail["attempts"] = attempts
    if fallback_direct:
        detail["fallback_direct"] = True
        if fallback_exc is not None:
            detail["fallback_error"] = _webhook_error_label(fallback_exc)
    # 不打印 URL/密钥/头内容；状态码/类名定位失败面，detail 给证据，结果文件仍是事实源。
    sys.stderr.write("[dispatch] webhook 发送失败：%s %s（结果文件仍是事实源）\n"
                     % (err, json.dumps(detail, ensure_ascii=False, sort_keys=True)))
    return "failed", err, detail


def emit_result(obj, result_path):
    """stdout 一行 JSON；同一字符串再写一份到结果文件（最终交付，逐字一致）。
    交付前可选 POST webhook 唤醒调用方，把 webhook: off/sent/failed 记进结果；
    失败时另记 webhook_error（HTTP 状态码或异常类名）与 webhook_error_detail
    （脱敏诊断，与 stderr 同一份，不含 URL/密钥）。"""
    status, wh_err, wh_detail = send_webhook(obj)
    obj["webhook"] = status
    if wh_err is not None:
        obj["webhook_error"] = wh_err
        if wh_detail is not None:
            obj["webhook_error_detail"] = wh_detail
    line = json.dumps(obj, ensure_ascii=False)
    if result_path is not None:
        try:
            result_path.parent.mkdir(parents=True, exist_ok=True)
            result_path.write_text(line + "\n", encoding="utf-8", newline="")
        except OSError as e:
            sys.stderr.write("[dispatch] 结果文件写入失败：%s\n" % e)
    print(line)


def verbatim_present(value, text):
    """逐字子串校验（锚点与抽取字段共用）。先按字面搜；屏幕会折行、把 tab
    展开成空格，字面搜不到时再做一次去空白折叠搜索（仍是确定性检查）。"""
    if value in text:
        return True
    strip_ws = lambda s: re.sub(r"\s+", "", s)
    return strip_ws(value) in strip_ws(text)


def closest_fragment(value, text):
    """在原文非空行里找与 value 最接近的一行（去空白后按相似度），原样返回。"""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return ""
    strip_ws = lambda s: re.sub(r"\s+", "", s)
    v = strip_ws(value)
    best, best_ratio = "", -1.0
    for ln in lines:
        r = difflib.SequenceMatcher(None, v, strip_ws(ln)).ratio()
        if r > best_ratio:
            best, best_ratio = ln, r
    return best


def verify_extraction(ext, screen, raw_path):
    """裁定 C：questions/conclusion/blocked 全部逐字摘原文。
    对每个非空字段值做子串校验；搜不到的标 rewritten 并附最接近片段；
    全部搜不到则 error。needs_user 是布尔判断字段、next_command 是推测字段
    （ADR-0008），均不参与校验。"""
    checks = [("conclusion", ext["conclusion"]), ("blocked", ext["blocked"])]
    checks += [(f"questions[{i}]", q) for i, q in enumerate(ext["questions"])]
    rewritten = {}
    checked = 0
    for key, val in checks:
        if not val.strip():
            continue
        checked += 1
        if not verbatim_present(val, screen):
            rewritten[key] = closest_fragment(val, screen)
    if checked and len(rewritten) == checked:
        raise DispatchError(
            "extraction_rewritten",
            "抽取字段全部未通过逐字校验：pi 在改写而不是抽取",
            [f"整屏原文已落盘：{raw_path}",
             "最接近的原文片段：" + json.dumps(rewritten, ensure_ascii=False)[:300]],
        )
    return rewritten


def verify_reply(reply, screen):
    """reply 是 pi 裁剪后的干净全文。先整体子串校验（回复本是连续整块时最强）；
    不中再逐行校验（允许删掉穿插的杂物行）。返回未过逐字校验的行列表，全过返回 []。"""
    if not reply.strip():
        return ["(reply 为空)"]
    if verbatim_present(reply, screen):
        return []
    return [ln for ln in reply.splitlines()
            if ln.strip() and not verbatim_present(ln, screen)]


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


def check_identity(terminal, args):
    if not identity_ok(terminal, args.expect_proc):
        raise DispatchError(
            "identity_mismatch",
            "目标前台进程不含预期子串（--expect-proc）",
            ["核对 hc get 的 foreground.argv[0] 与 status.app；不要向身份不符的终端发送"],
        )


def find_app_bin():
    """恒可见不变量要求 herdr-app；探不到即 missing_tool（创建/既有/复活共用）。"""
    app_bin = find_tool("herdr-app")
    if not app_bin:
        raise DispatchError("missing_tool", "PATH 与常见目录里都找不到 herdr-app",
                            ["恒可见不变量要求 herdr-app；装上后重试"])
    return app_bin


def dialog_error(terminal):
    st = terminal.get("status") or {}
    kind = st.get("kind")
    msg = st.get("msg") or ""
    return DispatchError(
        "dialog_blocked",
        "目标会话停在弹框等人决定" + (f"（kind={kind}）" if kind else "") + (f"：{msg}" if msg else ""),
        ["不按键、不代答；请用户到目标会话屏幕前处理，处理后用新放行 id 重新发起"],
    )


def wait_for_result(hc_bin, args, baseline, deadline):
    """hc wait 循环：状态变化即醒、到点 check-in、墙钟兜底。
    返回落定复核通过后的 hc get 结果。"""
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise DispatchError(
                "timeout",
                "墙钟超时：目标会话仍在工作",
                ["原文未取回；可稍后用 hc read 查看，或经用户放行后另起一轮"],
            )
        t = min(args.check_in, remaining)
        try:
            w = hc_json(hc_bin, ["wait", args.target, "--timeout", f"{t:.1f}"], t + WAIT_GUARD_MARGIN_S)
        except HcFailure as e:
            raise DispatchError("hc_failed", f"hc wait 失败：{e.message}", e.hints)
        reason = w.get("reason")
        if reason == "exit":
            raise DispatchError(
                "target_exited",
                f"目标程序退出（exit_code={w.get('exit_code')} signal={w.get('signal')}）",
                ["确认目标会话状态后另起一轮"],
            )
        if reason == "not_started":
            raise DispatchError(
                "not_started",
                "提示词已发出，但目标没有进入 working",
                ["确认提示词是否真的提交给了目标程序；本轮不再发送"],
            )
        if reason == "status":
            state = w.get("status")
            if state == "blocked":
                raise dialog_error(hc_json(hc_bin, ["get", args.target], QUERY_GUARD_S))
            if state == "error":
                raise DispatchError("target_error", "目标自报 error", ["到目标会话屏幕前查看"])
            if state in ("idle", "done") and w.get("turn", 0) > baseline:
                outcome = settle(hc_bin, args, w["turn"])
                if outcome is not None:
                    return outcome
                continue
            # working、或 turn 未过基线：继续等
            continue
        # timeout（check-in）及其他 reason：用 hc get 兜底核对一次
        g = hc_json(hc_bin, ["get", args.target], QUERY_GUARD_S)
        if g.get("state") != "live":
            raise DispatchError("target_exited", "目标终端已结束", ["hc get 查看 reason"])
        state = status_state(g)
        if state == "blocked":
            raise dialog_error(g)
        if state == "error":
            raise DispatchError("target_error", "目标自报 error", ["到目标会话屏幕前查看"])
        if state in ("idle", "done") and g.get("turn", 0) > baseline:
            outcome = settle(hc_bin, args, g["turn"])
            if outcome is not None:
                return outcome


def settle(hc_bin, args, wait_turn):
    """落定复核：静置 settle_seconds 后 hc get，连续 settle_checks 次
    状态仍 idle/done 且 turn 不变才算落定。回到 working 等则返回 None 继续等。"""
    for _ in range(args.settle_checks):
        time.sleep(args.settle_seconds)
        g = hc_json(hc_bin, ["get", args.target], QUERY_GUARD_S)
        if g.get("state") != "live":
            raise DispatchError("target_exited", "落定复核时目标终端已结束", [])
        state = status_state(g)
        if state == "blocked":
            raise dialog_error(g)
        if state == "error":
            raise DispatchError("target_error", "落定复核时目标自报 error", [])
        if state not in ("idle", "done") or g.get("turn", 0) != wait_turn:
            return None
    return g


def do_send(hc_bin, args, text, ledger, ledger_path):
    """--if idle 首发；被拒（status_mismatch）且 hc get 复核为 done 才补发一次。
    返回 (send, resent)。前置与主任务共用同一套发送护栏（裁定 6）：
    永不 --force、不碰 hc keys；被拒时零字节进入，不算重发。"""
    try:
        send = hc_json(
            hc_bin,
            ["send", "--if", "idle", "--", args.target, text],
            SEND_GUARD_S,
        )
        return send, False
    except HcFailure as e:
        if e.kind != "status_mismatch":
            kind = "send_refused" if e.kind == "blocked" else "hc_failed"
            raise DispatchError(kind, f"hc send 失败：{e.message}",
                                e.hints + ["未再发送；台账已记录本次尝试"])
        g = hc_json(hc_bin, ["get", args.target], QUERY_GUARD_S)
        if status_state(g) != "done":
            raise DispatchError(
                "send_refused",
                f"hc send --if idle 被拒，复核状态是 {status_state(g) or '未自报'} 而非 done，不补发",
                e.hints,
            )
        ledger.update(phase="resending",
                      first_refusal={"kind": e.kind, "returncode": e.returncode})
        write_json_atomic(ledger_path, ledger)
        g2 = hc_json(hc_bin, ["get", args.target], QUERY_GUARD_S)
        if g2.get("state") != "live" or status_state(g2) not in ("idle", "done"):
            raise DispatchError(
                "send_refused",
                f"补发前复核状态是 {status_state(g2) or '未自报'}，不补发",
                ["目标可能已重新开始工作或停在弹框；本轮未再发送"],
            )
        try:
            send = hc_json(hc_bin, ["send", "--", args.target, text], SEND_GUARD_S)
        except HcFailure as e2:
            raise DispatchError("send_refused", f"done 补发仍失败：{e2.message}",
                                e2.hints + ["本轮不再发送；台账已记录两次尝试"])
        return send, True


def reset_settle(hc_bin, args):
    """前置落定复核：idle/done 且屏幕连续 settle_checks 次静置不变。
    /clear 是本地命令、可能不进 working/不增 turn，故用屏幕内容稳定而非 turn 判据。
    屏幕仍在变或离开 idle/done 返回 False；blocked/error/退出照常抛错。"""
    prev = None
    for _ in range(args.settle_checks):
        time.sleep(args.settle_seconds)
        g = hc_json(hc_bin, ["get", args.target], QUERY_GUARD_S)
        if g.get("state") != "live":
            raise DispatchError("target_exited", "前置落定复核时目标终端已结束", [])
        state = status_state(g)
        if state == "blocked":
            raise dialog_error(g)
        if state == "error":
            raise DispatchError("target_error", "前置落定复核时目标自报 error", [])
        if state not in ("idle", "done"):
            return False
        screen = hc_json(hc_bin, ["read", args.target, "--lines", str(READ_LINES)], QUERY_GUARD_S)["text"]
        if prev is not None and screen != prev:
            return False
        prev = screen
    return True


def wait_for_reset(hc_bin, args, deadline):
    """等前置（/new /clear /compact /model）落定：短轮询 hc get 至 idle/done，再屏幕静置复核。
    不要求 turn 增长（/clear、/model 可能不增 turn）。前置不抽取、不落 run_record。"""
    while True:
        if time.monotonic() >= deadline:
            raise DispatchError(
                "timeout",
                "前置墙钟超时：目标仍未落定",
                ["主任务未发送；本轮已停止，可经用户放行后另起一轮"],
            )
        g = hc_json(hc_bin, ["get", args.target], QUERY_GUARD_S)
        if g.get("state") != "live":
            raise DispatchError("target_exited", "前置期间目标终端已结束", ["hc get 查看 reason"])
        state = status_state(g)
        if state == "blocked":
            raise dialog_error(g)
        if state == "error":
            raise DispatchError("target_error", "前置期间目标自报 error", ["到目标会话屏幕前查看"])
        if state in ("idle", "done"):
            if reset_settle(hc_bin, args):
                return
            continue  # 屏幕仍在变（/clear 重绘 / /compact 收尾）：重新复核
        time.sleep(RESET_POLL_S)  # working：短轮询


# shell 包装（ADR-0009）：认作持久 shell 的进程名（basename、去登录前缀 -）。
SHELL_NAMES = {"sh", "bash", "zsh", "nu", "fish", "dash", "ksh", "tcsh", "csh"}


def _proc_base(name):
    return (name or "").rsplit("/", 1)[-1].lstrip("-")


def fg_is_shell(fg):
    """前台进程是不是 shell（认 name 与 argv[0] 的 basename）。"""
    argv = fg.get("argv") or []
    first = argv[0] if isinstance(argv, list) and argv else ""
    return _proc_base(fg.get("name")) in SHELL_NAMES or _proc_base(first) in SHELL_NAMES


def wait_for_shell_ready(hc_bin, target, deadline):
    """创建后等 shell 就绪：短轮询 hc get 至 state live＋前台是 shell。
    刚 run 完名字可能短暂未解析（not_found 继续等）；终端结束/超时→launch_failed。"""
    while True:
        if time.monotonic() >= deadline:
            raise DispatchError("launch_failed", "等新终端 shell 就绪超时",
                                ["主任务未发送；本轮已停止，可经用户放行后另起一轮"])
        try:
            g = hc_json(hc_bin, ["get", target], QUERY_GUARD_S)
        except HcFailure as e:
            if e.kind == "not_found":
                time.sleep(RESET_POLL_S)
                continue
            raise DispatchError("launch_failed", f"等 shell 就绪时 hc get 失败：{e.message}", e.hints)
        if g.get("state") != "live":
            raise DispatchError("launch_failed", "新终端 shell 就绪前已结束",
                                ["hc get 查看 reason；经用户放行后另起一轮"])
        if fg_is_shell(g.get("foreground") or {}):
            return g
        time.sleep(RESET_POLL_S)


def wait_for_agent_ready(hc_bin, args, deadline):
    """等 agent 就绪：前台不再是 shell 且状态 idle/done。超时/终端结束→launch_failed；
    弹框/error 照常抛（既有 kind 不动）。返回就绪时的 hc get。"""
    while True:
        if time.monotonic() >= deadline:
            raise DispatchError("launch_failed", "等 agent 就绪超时（启动命令已发）",
                                ["主任务未发送；本轮已停止，可经用户放行后另起一轮"])
        try:
            g = hc_json(hc_bin, ["get", args.target], QUERY_GUARD_S)
        except HcFailure as e:
            if e.kind == "not_found":
                # 名字只解析 live 终端：启动期间终端死亡即名字失解析。
                raise DispatchError("launch_failed", "agent 启动期间终端已结束（名字失解析）",
                                    ["hc get 按 t 号查看 reason；经用户放行后另起一轮"])
            raise DispatchError("launch_failed", f"等 agent 就绪时 hc get 失败：{e.message}", e.hints)
        if g.get("state") != "live":
            raise DispatchError("launch_failed", "agent 启动期间终端已结束",
                                ["hc get 查看 reason；经用户放行后另起一轮"])
        state = status_state(g)
        if state == "blocked":
            raise dialog_error(g)
        if state == "error":
            raise DispatchError("target_error", "新 agent 自报 error", ["到目标会话屏幕前查看"])
        fg = g.get("foreground") or {}
        if not fg_is_shell(fg) and state in ("idle", "done"):
            return g
        time.sleep(RESET_POLL_S)


def send_launch(hc_bin, args, ledger, ledger_path, ctx):
    """启动前置发送（创建/复活共用）：shell 不是 agent、不报 idle，用裸 send（不带 --if）。
    启动命令是命令不是提示词：不抽取、不落 run_record，占前置发送槽。"""
    ledger.update(phase="launch_sending")
    write_json_atomic(ledger_path, ledger)
    try:
        send = hc_json(hc_bin, ["send", "--", args.target, args.launch], SEND_GUARD_S)
    except HcFailure as e:
        raise DispatchError("launch_failed", f"启动命令发送失败：{e.message}",
                            e.hints + ["主任务未发送；台账已记录本次尝试"])
    ctx["launch_sent"] = True
    ledger.update(launch_sent=True, launch_send=send, phase="launch_waiting_agent")
    write_json_atomic(ledger_path, ledger)
    g = wait_for_agent_ready(hc_bin, args, time.monotonic() + args.wall_clock)
    check_identity(g, args)
    ledger.update(phase="launch_done", launch_done_at=now_iso())
    write_json_atomic(ledger_path, ledger)
    return g


def do_create(hc_bin, args, ledger, ledger_path, ctx):
    """创建前置（ADR-0009，单形态 shell 包装）：hc run --bg --keep --name --cwd -- zsh -l
    →打 loop=1＋loop-agent=<expect-proc> 标签→等 shell 就绪→send 启动命令→等 agent 就绪
    →核身份。名字唯一性由 herdr 仲裁（name_taken 竞态映射 launch_failed）。
    任一步失败整轮 launch_failed，ctx/台账带部分状态（终端已建/启动已发如实报）。"""
    ledger.update(phase="launch_running")
    write_json_atomic(ledger_path, ledger)
    try:
        r = hc_json(hc_bin, ["run", "--bg", "--keep", "--name", args.target,
                             "--cwd", args.cwd, "--", "zsh", "-l"], SEND_GUARD_S)
    except HcFailure as e:
        raise DispatchError("launch_failed", f"hc run 创建命名会话失败（{e.kind}）：{e.message}",
                            e.hints + ["同名 live 终端存在（name_taken）时不建不 kill；"
                                       "经用户放行后另起一轮或改用既有会话"])
    addr = r.get("address")
    if not addr:
        raise DispatchError("launch_failed", "hc run 未返回新终端地址", [str(r)[:200]])
    ctx["target_address"] = addr
    ctx["launched"] = True
    ledger.update(target_address=addr, launched=True, phase="launch_labeling")
    write_json_atomic(ledger_path, ledger)
    labels = ["loop=1"]
    if args.expect_proc:
        labels.append("loop-agent=" + args.expect_proc)
    try:
        hc_json(hc_bin, ["label", args.target, *labels], QUERY_GUARD_S)
    except HcFailure as e:
        raise DispatchError("launch_failed", f"打 loop 标签失败：{e.message}",
                            e.hints + ["终端已建（launched:true）；无标签终端下轮会被拒收编"])
    ledger.update(phase="launch_waiting_shell")
    write_json_atomic(ledger_path, ledger)
    wait_for_shell_ready(hc_bin, args.target, time.monotonic() + args.wall_clock)
    return send_launch(hc_bin, args, ledger, ledger_path, ctx)


def find_pane(ls_data, address):
    """在 herdr-app ls 结果里找终端的 pane 条目；无 pane 返回 None。"""
    for sp in ls_data.get("spaces") or []:
        for tb in sp.get("tabs") or []:
            for t in tb.get("terminals") or []:
                if t.get("terminal") == address and t.get("pane"):
                    return t
    return None


def find_tab(ls_data, address):
    """在 herdr-app ls 结果里找终端所在 tab 条目（显示名落地用，ADR-0010）；
    定位不到（无 tab 或 tab 无 id）返回 None。"""
    for sp in ls_data.get("spaces") or []:
        for tb in sp.get("tabs") or []:
            if not tb.get("id"):
                continue
            for t in tb.get("terminals") or []:
                if t.get("terminal") == address:
                    return tb
    return None


def apply_display_name(app_bin, address, display_name, ledger, ledger_path, ctx):
    """显示名落地（ADR-0010）：ls 定位 tab id → tab.rename → ls 复核 tab.name==显示名
    （命令成功不算数）。时序在领养复核 pane 落地后、前置/主任务发送前。
    失败降级告警不丢放行：rename 失败/复核不符/无 tab → ctx/台账记 display_name_warning
    （reason：rename_error/rename_mismatch/no_tab，带详情），主流程继续；pane 落地仍是
    硬闸（adopt_failed 语义不变）。result display_name 回显本轮传入值：落地步骤一跑就带
    （含 no_tab/ls 失败等未真正发出 rename 的降级情形，审计「请求了什么名」），成功与 error 都带。
    不写 summary 标签：机器可读靠自动跟随 tab 名的 where 标签。"""
    ctx["display_name"] = display_name
    ledger.update(phase="naming", display_name=display_name)
    write_json_atomic(ledger_path, ledger)
    warning = None
    tab = None
    try:
        ls = app_json(app_bin, ["ls"], QUERY_GUARD_S)
        tab = find_tab(ls, address)
    except DispatchError as e:
        warning = {"reason": "rename_error", "detail": e.message}
    if warning is None and tab is None:
        warning = {"reason": "no_tab", "detail": f"ls 里定位不到终端 {address} 所在 tab"}
    if warning is None:
        try:
            app_json(app_bin, ["do", "tab.rename", json.dumps(
                {"id": tab["id"], "name": display_name}, ensure_ascii=False)], QUERY_GUARD_S)
        except DispatchError as e:
            warning = {"reason": "rename_error", "detail": e.message}
    if warning is None:
        # 复核：命令成功不算数，必须只读 ls 核 tab.name 真的等于显示名。
        try:
            ls2 = app_json(app_bin, ["ls"], QUERY_GUARD_S)
            tab2 = find_tab(ls2, address)
            actual = tab2.get("name") if tab2 else None
        except DispatchError as e:
            warning = {"reason": "rename_error", "detail": e.message}
            actual = None
        if warning is None and actual != display_name:
            warning = {"reason": "rename_mismatch", "actual": actual,
                       "detail": f"复核 tab.name={actual!r} ≠ 显示名 {display_name!r}"}
    if warning is not None:
        ctx["display_name_warning"] = warning
        ledger.update(display_name_warning=warning)
    else:
        ledger.update(phase="naming_done", naming_done_at=now_iso())
    write_json_atomic(ledger_path, ledger)


def adopt(hc_bin, app_bin, address, session_name, cwd, ledger, ledger_path, ctx, precheck,
          adopt_timeout=ADOPT_TIMEOUT_S, adopt_poll=ADOPT_POLL_S):
    """领养（ADR-0009 恒可见不变量）：status 探测→只读 ls 核 pane→缺则（本机无 space
    先 space.new focus:false）→terminal.keep→带超时轮询 ls 复核 pane 落地（#40）→无 space 分支清理赠送
    shell（tab.close confirmed:true＋hc rm）。任一步失败抛 adopt_failed，ctx 记部分状态
    （workspace:"failed"＋adopt_reason＋space_created/pane_adopted 如实报）。
    precheck=True（既有路径补领养）：写台账前，失败零发送、id 不消耗、同 id 重试；
    precheck=False（创建路径）：写台账后，失败保留已建终端、id 消耗、新 id 重试修复。"""
    def fail(reason, message, hints):
        ctx["workspace"] = "failed"
        ctx["adopt_reason"] = reason
        if ledger is not None:
            ledger.update(workspace="failed", adopt_reason=reason)
        raise DispatchError("adopt_failed", message, hints)

    retry = (["打开 herdr-app 后用同一放行 id 重试（零发送、id 未消耗）"] if precheck
             else ["已建终端保留；打开 herdr-app 后用新放行 id 重试，走既有路径补领养修复"])
    if ledger is not None:
        ledger.update(phase="adopting")
        write_json_atomic(ledger_path, ledger)
    try:
        app_json(app_bin, ["status"], QUERY_GUARD_S)
    except DispatchError as e:
        fail("app_closed", f"herdr-app 没开或不可达，领养失败：{e.message}", retry)
    try:
        ls = app_json(app_bin, ["ls"], QUERY_GUARD_S)
    except DispatchError as e:
        fail("app_closed", f"herdr-app ls 失败，领养失败：{e.message}", retry)
    if find_pane(ls, address):
        ctx["workspace"] = "adopted"
        if ledger is not None:
            ledger.update(phase="adopt_done", workspace="adopted")
            write_json_atomic(ledger_path, ledger)
        return
    no_space = not (ls.get("spaces") or [])
    if no_space:
        # 本机无 space：keep 无处落。建空间（focus:false 不抢用户当前视图），赠送 shell 稍后清。
        try:
            app_json(app_bin, ["do", "space.new", json.dumps(
                {"name": session_name, "folder": cwd, "focus": False},
                ensure_ascii=False)], QUERY_GUARD_S)
        except DispatchError as e:
            fail("keep_error", f"本机无 space，space.new 失败：{e.message}", retry)
        ctx["space_created"] = True
        if ledger is not None:
            ledger.update(space_created=True)
            write_json_atomic(ledger_path, ledger)
    try:
        app_json(app_bin, ["do", "terminal.keep",
                           json.dumps({"terminal": address})], QUERY_GUARD_S)
    except DispatchError as e:
        fail("keep_error", f"terminal.keep 失败：{e.message}", retry)
    # keep 后台完成、失败弹 toast：命令成功不算数，必须只读 ls 复核 pane 落地。
    # 落地是异步的（#40）：单次 ls 可能跑在落地之前而误判 no_pane，改带超时轮询——
    # 窗口内反复只读 ls，pane 出现即成功；窗口耗尽仍无 pane 才报 no_pane（keep 真失败如实报）。
    deadline = time.monotonic() + adopt_timeout
    while True:
        try:
            ls2 = app_json(app_bin, ["ls"], QUERY_GUARD_S)
        except DispatchError as e:
            fail("app_closed", f"keep 后 herdr-app ls 失败：{e.message}", retry)
        if find_pane(ls2, address):
            break
        if time.monotonic() >= deadline:
            fail("no_pane", "keep 后轮询窗口耗尽仍无 pane（后台落地失败）", retry)
        time.sleep(adopt_poll)
    if no_space:
        ctx["pane_adopted"] = True
        # 清理赠送 shell：找本终端所在 space（刚建、只有赠送 tab＋keep tab），
        # 其余 tab 即赠送 shell——tab.close 结束终端，再 hc rm 清 finished 条目。
        my_space = next((sp for sp in ls2.get("spaces") or []
                         if any(t.get("terminal") == address
                                for tb in sp.get("tabs") or []
                                for t in tb.get("terminals") or [])), None)
        for tb in (my_space or {}).get("tabs") or []:
            addrs = [t.get("terminal") for t in tb.get("terminals") or []]
            if address in addrs:
                continue
            try:
                app_json(app_bin, ["do", "tab.close", json.dumps(
                    {"id": tb["id"], "confirmed": True})], QUERY_GUARD_S)
            except DispatchError as e:
                fail("keep_error", f"清理赠送 shell tab 失败：{e.message}",
                    retry + ["pane 已落地、空间已建；赠送 shell 残留，如实报"])
            for a in addrs:
                try:
                    hc_json(hc_bin, ["rm", a], QUERY_GUARD_S)
                except DispatchError as e:
                    fail("keep_error", f"hc rm 赠送 shell 终端失败：{e.message}",
                        retry + ["赠送 tab 已关、终端 finished 未 rm，如实报"])
    ctx["workspace"] = "adopted"
    if ledger is not None:
        ledger.update(phase="adopt_done", workspace="adopted")
        write_json_atomic(ledger_path, ledger)


def parse_extraction(stdout):
    s = stdout.strip()
    candidates = []
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", s, re.S)
    if m:
        candidates.append(m.group(1))
    i, j = s.find("{"), s.rfind("}")
    if i != -1 and j > i:
        candidates.append(s[i : j + 1])
    for c in candidates:
        obj = try_json(c)
        if not isinstance(obj, dict):
            continue
        if (
            isinstance(obj.get("needs_user"), bool)
            and isinstance(obj.get("blocked"), str)
            and isinstance(obj.get("questions"), list)
            and all(isinstance(q, str) for q in obj["questions"])
            and isinstance(obj.get("conclusion"), str)
            and isinstance(obj.get("reply"), str)
        ):
            ext = {k: obj[k] for k in EXTRACT_FIELDS if k != "next_command"}
            # next_command 是推测字段（ADR-0008）：缺失或非字符串按 ""，
            # 建议性字段不连累整轮（与 webhook 失败不连累同哲学）。
            nc = obj.get("next_command")
            ext["next_command"] = nc if isinstance(nc, str) else ""
            return ext
    raise DispatchError(
        "extraction_invalid",
        "抽取输出中找不到符合固定形状的 JSON",
        ["原文已落盘，可人工按 prompts/extract.md 重做抽取", stdout.strip()[:300]],
    )


def extract_via_pi(pi_bin, args, raw_path):
    extract_md = Path(__file__).resolve().parent.parent / "prompts" / "extract.md"
    if not extract_md.is_file():
        raise DispatchError("missing_prompt", f"找不到抽取提示词：{extract_md}", [])
    instruction = (
        "整屏原文已随消息附上（@文件）。按系统提示词裁剪并抽取，"
        "只输出一个 JSON 对象，含 needs_user、reply、questions、conclusion、blocked、next_command 六个字段。"
        f'整屏原文落盘路径：{raw_path}'
    )
    argv = [
        pi_bin, "-p",
        "--no-tools", "--no-session", "--no-extensions", "--no-skills",
        "--no-prompt-templates", "--no-context-files", "--no-mcp",
        "--append-system-prompt", str(extract_md),
    ]
    if args.pi_model:
        argv += ["--model", args.pi_model]
    argv += ["--", f"@{raw_path}", instruction]
    p = run_cmd(argv, args.extract_timeout)
    if p.returncode != 0:
        raise DispatchError(
            "extraction_failed",
            f"pi 退出码 {p.returncode}",
            ["原文已落盘，可人工重做抽取", (p.stderr or p.stdout).strip()[:300]],
        )
    # 研究表明 pi 模型失败时退出码可能仍为 0，所以无论退出码都要校验输出。
    return parse_extraction(p.stdout)


def extract_via_api(args, raw_path):
    """直连 API 裁剪＋抽取（ADR-0005）：OpenAI 兼容 chat completions，
    system=extract.md，user=指令＋内联整屏原文。temp 0、不流式、不重试、
    不发 response_format；超时复用 --extract-timeout。代理决议与出口和
    webhook 同一套（ADR-0003）。失败只记状态码/类名，不读响应体，密钥/URL 零回显。"""
    base, key, model, max_tokens = api_config()
    extract_md = Path(__file__).resolve().parent.parent / "prompts" / "extract.md"
    if not extract_md.is_file():
        raise DispatchError("missing_prompt", f"找不到抽取提示词：{extract_md}", [])
    raw_text = Path(raw_path).read_text(encoding="utf-8")
    instruction = (
        "整屏原文已内联在本消息末尾。按系统提示词裁剪并抽取，"
        "只输出一个 JSON 对象，含 needs_user、reply、questions、conclusion、blocked、next_command 六个字段。"
    )
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": extract_md.read_text(encoding="utf-8")},
            {"role": "user", "content": instruction + "\n\n" + raw_text},
        ],
        "temperature": 0,
        "max_tokens": max_tokens,
    }).encode("utf-8")
    url = base + "/chat/completions"
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + key})
    proxies, _ = webhook_proxy_resolution(url)
    try:
        with webhook_open(req, args.extract_timeout, proxies) as r:
            payload = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise DispatchError("extraction_failed", f"API HTTP {e.code}",
                            ["原文已落盘，可人工重做抽取"])
    except Exception as e:
        reason = getattr(e, "reason", None)
        if isinstance(e, TimeoutError) or isinstance(reason, TimeoutError):
            raise DispatchError("tool_timeout",
                                f"API 抽取超护栏 {args.extract_timeout} 秒",
                                ["原文已落盘，可人工重做抽取"])
        raise DispatchError("extraction_failed", f"API {type(e).__name__}",
                            ["原文已落盘，可人工重做抽取"])
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise DispatchError("extraction_invalid",
                            "API 响应缺 choices[0].message.content",
                            ["原文已落盘，可人工重做抽取"])
    return parse_extraction(content)


def extract(pi_bin, args, raw_path):
    """抽取入口：按 LOOP_EXTRACT_BACKEND 分叉（默认 api 直连，pi 显式备选）。"""
    if resolve_extract_backend() == "api":
        return extract_via_api(args, raw_path)
    return extract_via_pi(pi_bin, args, raw_path)


def positive_float(s):
    v = float(s)
    if v <= 0:
        raise argparse.ArgumentTypeError("须为正数")
    return v


def positive_int(s):
    v = int(s)
    if v <= 0:
        raise argparse.ArgumentTypeError("须为正整数")
    return v


def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        description="loop 分发脚本：发送已放行提示词、等待落定、取回原文落盘、pi 抽取。",
    )
    ap.add_argument("--target", required=True, help="目标会话地址：会话名（推荐，一名一 live、"
                    "恒解析到最新 live 终端）或 t 号/machine/t7；缺失或已死时按会话名 get-or-create")
    ap.add_argument("--release-id", required=True, help="放行 id（唯一；一个放行只跑一轮）")
    ap.add_argument("--prompt-file", required=True, help="已放行的提示词文件路径（原文照发）")
    ap.add_argument("--reset", choices=["none", "new", "clear", "compact"], default="none",
                    help="主任务前的重置前置，发字面命令 /new /clear /compact（调用方按 agent 认哪个挑）；"
                         "默认 none 不重置，即现状单 prompt")
    ap.add_argument("--target-model", help="切模型前置（ADR-0006）：主任务前发字面命令 /model <名字>，"
                    "名字原样透传（脚本不认识模型、不按 agent 映射、不核实生效）；与 --reset 独立，"
                    "同传时顺序 重置→切模型→主任务；默认不传即现状")
    ap.add_argument("--launch", help="创建/复活前置（ADR-0009）：shell 启动命令字符串，字面透传"
                    "（脚本不映射 kind→命令、不感知 herdr 模板；模板退居调用方持久配置仓库，用时抄命令字面传）。"
                    "目标缺失/已死/空闲 shell 时在同一放行内创建或复活命名会话；与 --cwd 成对必填、无默认。"
                    "启动命令是命令不是提示词：不抽取、不落 run_record，占前置发送槽")
    ap.add_argument("--cwd", help="创建命名会话的工作目录（与 --launch 同时必填、无默认）；"
                    "复活沿用终端原 cwd，不重写")
    ap.add_argument("--display-name", help="显示名（ADR-0010）：命名会话标签页上给人看的名字，"
                    "调用方显式传完整字符串（脚本不派生、不拼接、不理解、不校验式样）。"
                    "创建/复活/重建三条新鲜路径必传（缺则 display_name_required，与 launch_required "
                    "同构：退出码 2、零发送、id 不消耗、补参同 id 重试）；复用轮可选——传了就覆盖 "
                    "tab 名、不传保持旧值。机械校验：非空、1–64 字符、禁换行与控制字符（非法 bad_args）。"
                    "落地 tab.rename＋ls 复核，失败降级 display_name_warning 不丢放行")
    ap.add_argument("--rebuild", action="store_true",
                    help="强制重建（ADR-0009）：仅用户明示授权 kill 重建后传；只在 session_occupied 占位分支生效"
                    "（其余分流误传 bad_args），只杀 idle/done 占位者（working/blocked 报 rebuild_busy），"
                    "kill 后同名新建；放行必贴「将 kill <名字>（占位者摘要）并重建」")
    ap.add_argument("--expect-proc", help="身份预检：前台进程应含此子串（认 argv[0]/status.app，如 grok/claude；"
                    "foreground.name 不可靠——claude 实测为版本号，不作判据）")
    ap.add_argument("--loop-dir", default=str(Path(tempfile.gettempdir()) / "loop"),
                    help="本地记录根目录（默认 $TMPDIR/loop，随 OS 临时清理）")
    ap.add_argument("--pi-model", help="pi --model 透传（仅 LOOP_EXTRACT_BACKEND=pi 时生效；默认用 pi 自身配置的模型）")
    # ---- 已裁定默认值（issue #16，2026-11-19），可覆盖 ----
    ap.add_argument("--wall-clock", type=positive_float, default=3600.0,
                    help="墙钟上限秒数，从发送成功起计，不重置（默认 3600）")
    ap.add_argument("--check-in", type=positive_float, default=180.0,
                    help="hc wait 每次 check-in 秒数（默认 180）")
    ap.add_argument("--settle-seconds", type=positive_float, default=10.0,
                    help="落定复核每次静置秒数（默认 10）")
    ap.add_argument("--settle-checks", type=positive_int, default=2,
                    help="落定复核次数（默认 2）")
    ap.add_argument("--adopt-timeout", type=positive_float, default=ADOPT_TIMEOUT_S,
                    help="领养复核轮询窗口秒数（#40，keep 后等 pane 落地，默认 30）")
    ap.add_argument("--adopt-poll", type=positive_float, default=ADOPT_POLL_S,
                    help="领养复核轮询间隔秒数（默认 2）")
    ap.add_argument("--extract-timeout", type=positive_float, default=1200.0,
                    help="pi 抽取子进程护栏秒数（默认 1200）")
    return ap.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    ledger = None
    ledger_path = None
    result_path = None
    started = None
    reset_sent = False
    model_sent = False
    backend = None
    task_name = None
    original_target = args.target
    # ctx 收集本轮部分状态（target_address/launched/launch_sent/rebuilt/workspace/
    # adopt_reason/space_created/pane_adopted/reset_skipped），成功与 error 都带适用字段。
    ctx = {}
    fresh = False
    try:
        # 创建参数成对校验（ADR-0009）：--launch 与 --cwd 同时必填、无默认。
        # 静态旗标错误，放行 id 不消耗（退出码 2）。
        if bool(args.launch) != bool(args.cwd):
            raise DispatchError("bad_args", "--launch 与 --cwd 须同时传（创建参数成对、无默认）",
                                ["补 --launch <shell 启动命令> --cwd <目录>，或两个都去掉"],
                                returncode=2)
        # 显示名机械校验（ADR-0010）：任何分流都校验。非法（空/超 64/换行/控制字符）→
        # bad_args（退出码 2、零发送、id 不消耗）。不校验式样（前缀/分隔符归调用方纪律）。
        if args.display_name is not None:
            dn = args.display_name
            if not dn or len(dn) > DISPLAY_NAME_MAX or DISPLAY_NAME_CONTROL_RE.search(dn):
                raise DispatchError(
                    "bad_args", "--display-name 非法（须非空、1–64 字符、禁换行与控制字符）",
                    ["改正显示名后用同一放行 id 重试（零发送、id 不消耗）"],
                    returncode=2)
        check_structure_and_tools()
        # 抽取后端启动预检（ADR-0005）：api 缺配置立刻报错，不跑完一轮才发现；
        # pi 备选才需要 find_tool(pi)，api 路径不依赖 node/pi。
        backend = resolve_extract_backend()
        if backend == "api":
            api_config()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", args.release_id):
            raise DispatchError("bad_release_id", "放行 id 只允许字母数字与 ._-，且不以符号开头", [])
        task_name = generate_short_task_name(args.release_id)
        validate_task_name(task_name)
        prompt_path = Path(args.prompt_file)
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
        # 锚点 = 提示词第一个非空行的前 24 个字符（不足 24 取整行）。
        # 目标 TUI 会把提示词回声折叠成「首行 + …」，末行永远看不到，故取首行前缀。
        anchor = nonempty[0][:24]
        sha = hashlib.sha256(raw).hexdigest()

        runs = Path(args.loop_dir) / "runs"
        # raw_path = 整屏原料（审计）；record_path = pi 裁剪后的干净全文（回看对象）。
        raw_path = runs / f"{args.release_id}.raw.md"
        record_path = runs / f"{args.release_id}.md"
        ledger_path = runs / f"{args.release_id}.json"
        # 结果文件是最终交付（成功与 error 都写），台账记过程；
        # 重发拒绝仍只看台账/整屏原料落盘，结果文件存在不单独拦截。
        result_path = runs / f"{args.release_id}.result.json"
        if ledger_path.exists() or raw_path.exists():
            raise DispatchError(
                "duplicate_release",
                f"放行 id {args.release_id} 已有台账或原文落盘，一个放行只跑一轮",
                ["查看台账确认上次结果；要重发须经用户重新放行、用新 id"],
            )
        hc_bin = find_tool("hc")
        pi_bin = find_tool("pi") if backend == "pi" else None
        if not hc_bin:
            raise DispatchError("missing_tool", "PATH 与常见目录里都找不到 hc", [])
        if backend == "pi" and not pi_bin:
            raise DispatchError("missing_tool", "PATH 与常见目录里都找不到 pi",
                                ["某些机器非交互 shell 的 PATH 里没有 node；"
                                 "脚本已探 ~/.local/bin、homebrew、nvm node bin 仍未果"])

        # 1) 预检分流（ADR-0009）：hc get 一律作分流判据（不用 hc ls 的 agent 派生字段）。
        #    not_found/finished→创建（finished 同名重建、旧条目按 t 号留审计）；
        #    live→既有路径（核身份→idle 核对→发送）。
        try:
            g = hc_json(hc_bin, ["get", args.target], QUERY_GUARD_S)
        except HcFailure as e:
            if e.kind != "not_found":
                raise
            g = None
        if g is None or g.get("state") == "finished":
            branch = "create"
        elif g.get("state") != "live":
            raise DispatchError("not_idle", "目标终端已结束，不发送", [])
        else:
            if g.get("address"):
                ctx["target_address"] = g["address"]
            # 标签绑定（ADR-0009）：loop=1＋loop-agent=<expect-proc> 是收编凭据；
            # 无 loop 标签或标签符不符的同名 live 终端拒收编→占位。
            labels = g.get("labels") or {}
            label_ok = (labels.get("loop") == "1"
                        and (not args.expect_proc
                             or labels.get("loop-agent") == args.expect_proc))
            fg_shell = fg_is_shell(g.get("foreground") or {})
            if not label_ok:
                branch = "occupied"
            elif fg_shell and status_state(g) is None:
                branch = "revive"      # 空闲 shell＋标签符→原地复活
            elif not fg_shell and identity_ok(g, args.expect_proc):
                branch = "existing"    # 前台 agent 身份符＋标签符→既有路径
            else:
                branch = "occupied"    # 别的 agent/程序（身份不符），或 shell 异常态
        if branch == "occupied" and args.rebuild:
            branch = "rebuild"
        if args.rebuild and branch != "rebuild":
            raise DispatchError(
                "bad_args", "--rebuild 只在 session_occupied 占位分支生效，其余分流误传",
                ["去掉 --rebuild；强制重建仅用于用户显式授权 kill 占位者后同名重建"],
                returncode=2)
        if branch == "occupied":
            fg = g.get("foreground") or {}
            argv = fg.get("argv") or []
            fg_desc = (argv[0] if isinstance(argv, list) and argv else "") or fg.get("name") or "未知"
            raise DispatchError(
                "session_occupied",
                f"会话名 {args.target} 被占用：{ctx.get('target_address') or args.target} "
                f"前台={fg_desc} turn={g.get('turn')} 状态={status_state(g) or '未自报'}"
                + ("（无 loop 标签，拒收编）" if labels.get("loop") != "1"
                   else "（loop 标签与 --expect-proc 不符）" if not label_ok
                   else "（前台 shell 但 status 非 null，异常态）" if fg_shell
                   else "（前台与 --expect-proc 不符）"),
                ["呈报占位者给用户；确需重建须经用户显式授权后带 --rebuild（只杀 idle/done）",
                 "或换 --target 到另一命名会话；不建不 kill、零发送、id 不消耗"],
            )
        if branch in ("create", "revive", "rebuild"):
            # 创建/复活需 --launch（＋--cwd），缺则 launch_required（与 reset_required 同构：
            # 退出码 2、零发送、放行 id 不消耗、补参同 id 重试）。
            why = {"create": "目标会话不存在或已死，创建",
                   "revive": "目标是空闲 shell，复活",
                   "rebuild": "强制重建"}[branch]
            if not args.launch:
                raise DispatchError(
                    "launch_required",
                    f"{why}需要 --launch（＋--cwd）",
                    ["补 --launch <shell 启动命令> --cwd <目录> 后用同一放行 id 重试（零发送、id 不消耗）",
                     "创建参数（会话名、启动命令、cwd、显示名）须随放行一并贴出，见 SKILL.md"],
                    returncode=2)
            # 显示名必传（ADR-0010）：新鲜路径缺 --display-name → display_name_required，
            # 与 launch_required 完全同构（退出码 2、零发送、id 不消耗、补参同 id 重试）。
            # 两参同缺时 launch_required 先报（上面已判）。
            if args.display_name is None:
                raise DispatchError(
                    "display_name_required",
                    f"{why}需要 --display-name",
                    ["补 --display-name <显示名> 后用同一放行 id 重试（零发送、id 不消耗）",
                     "显示名须随放行一并贴出，见 SKILL.md"],
                    returncode=2)
            if branch in ("create", "rebuild") and (
                    not SESSION_NAME_RE.match(args.target)
                    or RESERVED_ID_RE.match(args.target)):
                raise DispatchError(
                    "bad_args",
                    f"--target {args.target} 不是合法会话名（1–32 位 a-z0-9-_），无法创建",
                    ["--target 推荐传会话名（<kind>-<repo> 或 bot slug）；t 号是保留地址、不能作名字"],
                    returncode=2)
            if branch == "rebuild" and status_state(g) not in ("idle", "done"):
                # 只杀 idle/done 占位者；working/blocked/未自报一律拒杀（在跑的工作与
                # 等人决定的弹框绝不被杀；未自报无法确认空闲，保守拒）。
                raise DispatchError(
                    "rebuild_busy",
                    f"占位者状态是 {status_state(g) or '未自报'}，不是 idle/done，拒杀",
                    ["等占位者落定或请用户到屏幕前处理；kill 只在 idle/done 时执行"],
                )
            fresh = True
            if branch == "revive":
                # 复活同样过预检 pane 闸（US26：app 没开时所有轮次停摆，无隐性例外）；
                # 启动机械本身不 run、不重写标签（Decision 5）。
                adopt(hc_bin, find_app_bin(), ctx["target_address"],
                      g.get("name") or args.target, g.get("cwd") or ".",
                      None, None, ctx, precheck=True,
                      adopt_timeout=args.adopt_timeout, adopt_poll=args.adopt_poll)
        else:
            # 身份已在分流核过（identity_ok）。恒可见（ADR-0009）：既有路径每轮预检
            # （写台账前）核 pane，缺则补领养（park 等造成的可见性漂移自愈）；补不成→
            # adopt_failed，零发送、id 不消耗、同 id 重试。app 没开时所有轮次停摆（已接受的代价）。
            adopt(hc_bin, find_app_bin(), g.get("address") or args.target,
                  g.get("name") or args.target, g.get("cwd") or ".",
                  None, None, ctx, precheck=True,
                  adopt_timeout=args.adopt_timeout, adopt_poll=args.adopt_poll)
            state = status_state(g)
            if state not in ("idle", "done"):
                raise DispatchError(
                    "not_idle",
                    f"目标状态是 {state or '未自报'}，不是 idle/done，不发送",
                    ["--if idle 只认 idle；等目标落定或请用户处理后再来"],
                )

        # 1.2) 工程纪律预检（ADR-0004）：会话起始 skill 须在空会话或重置后运行。
        #      放在 hc get ＋身份预检之后、写台账与任何发送之前：
        #      turn == 0（精确整数 0）代表尚未跑过任何轮次的全新空会话，直接豁免放行；
        #      turn > 0 或 turn 缺失且 --reset=none 时拒发（放行 id 不消耗，补 --reset 同 id 可重试）。
        #      创建/复活/重建的新鲜会话视同 turn 0 豁免、不看终端 turn（turn 是终端级累计，
        #      复活会话 turn>0 但会话是空的，ADR-0009）。
        if args.reset == "none" and not fresh:
            hit = find_session_start_trigger(text)
            if hit is not None:
                turn = g.get("turn")
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

        # 2) 发送前再核一次文件未变，先记账后发送。
        if hashlib.sha256(prompt_path.read_bytes()).hexdigest() != sha:
            raise DispatchError("prompt_changed", "提示词文件在预检期间被改动，不发送", [])
        runs.mkdir(parents=True, exist_ok=True)
        ledger = {
            "release_id": args.release_id,
            "task_name": task_name,
            "target": args.target,
            "prompt_path": str(prompt_path),
            "prompt_sha256": sha,
            "anchor": anchor,
            "reset": args.reset,
            "target_model": args.target_model,
            "launch": args.launch,
            "cwd": args.cwd,
            "display_name": args.display_name,
            "rebuild": args.rebuild,
            "target_address": ctx.get("target_address"),
            # 预检领养（既有/复活路径）在写台账前，无 phase 可记；结果随台账留痕。
            "workspace": ctx.get("workspace"),
            "params": {
                "wall_clock": args.wall_clock, "check_in": args.check_in,
                "settle_seconds": args.settle_seconds, "settle_checks": args.settle_checks,
                "lines": READ_LINES, "extract_timeout": args.extract_timeout,
                "extract_backend": backend,
            },
            "phase": "sending",
            "created_at": now_iso(),
        }
        write_json_atomic(ledger_path, ledger)

        # 2.4) 创建前置（ADR-0009）：not_found/finished 分流且带 --launch 时，同一放行内
        #      建命名会话（单形态 shell 包装）→打标签→等 shell→send 启动命令→等 agent→核身份
        #      →领养（恒可见）。放行一并授权创建＋主任务；启动命令不抽取、不落 run_record。
        #      领养在写台账后：失败保留已建终端、放行 id 消耗（新 id 重试走既有路径修复）。
        if branch in ("create", "rebuild"):
            if branch == "rebuild":
                # 强制重建（ADR-0009，用户显式授权）：只杀 idle/done 占位者（预检已核），
                # kill 后同名新建；台账/result 记 rebuilt＋旧 t 号，kill 后创建失败也能
                # 审计「旧会话已死、新会话未成」。
                ledger.update(phase="rebuild_killing")
                write_json_atomic(ledger_path, ledger)
                try:
                    hc_json(hc_bin, ["kill", args.target], SEND_GUARD_S)
                except HcFailure as e:
                    raise DispatchError("launch_failed", f"重建 kill 占位者失败：{e.message}",
                                        e.hints + ["占位者未死；本轮停止，经用户放行后另起一轮"])
                ctx["rebuilt"] = True
                ctx["rebuilt_old_target"] = ctx.get("target_address")
                ledger.update(rebuilt=True, rebuilt_old_target=ctx["rebuilt_old_target"])
                write_json_atomic(ledger_path, ledger)
            do_create(hc_bin, args, ledger, ledger_path, ctx)
            adopt(hc_bin, find_app_bin(), ctx["target_address"], args.target, args.cwd,
                  ledger, ledger_path, ctx, precheck=False,
                  adopt_timeout=args.adopt_timeout, adopt_poll=args.adopt_poll)
        elif branch == "revive":
            # 复活（ADR-0009）：不 run、不重写标签；原地重发启动命令→等 agent 就绪→核身份。
            # 名字/cwd/终端级历史不动，会话是空的。
            send_launch(hc_bin, args, ledger, ledger_path, ctx)

        # 2.45) 显示名落地（ADR-0010）：领养复核 pane 落地后、前置/主任务发送前。
        #       创建/重建在领养成功之后、复活在启动命令发出 agent 就绪之后（上面各分支已就绪）；
        #       既有路径仅当传了显示名（复用轮可选，传了就覆盖、不传不碰）。新鲜路径必传（预检已核）。
        #       失败降级 display_name_warning 不丢放行；pane 落地仍是硬闸。
        if args.display_name is not None:
            apply_display_name(find_app_bin(), ctx.get("target_address"),
                               args.display_name, ledger, ledger_path, ctx)

        # 2.5) 重置前置（--reset != none）：同一 target 上先发字面命令 /new /clear /compact，
        #      等落定（idle/done + 屏幕静置）后再发主任务。放行一并授权前置＋主任务：
        #      前置按枚举校验（argparse choices），主任务仍逐字来自放行文件、校 sha256。
        #      前置不抽取、不落 run_record；任一步发不出即整轮停（台账记部分状态）。
        #      刚创建/复活的新鲜会话自动跳过重置发送（不在空会话上空等一次重置落定），
        #      result 记 reset_skipped:"fresh_launch"；切模型前置照跑（ADR-0006 纪律平移）。
        if args.reset != "none" and fresh:
            ctx["reset_skipped"] = "fresh_launch"
            ledger.update(reset_skipped="fresh_launch")
            write_json_atomic(ledger_path, ledger)
        elif args.reset != "none":
            reset_cmd = "/" + args.reset
            ledger.update(phase="reset_sending")
            write_json_atomic(ledger_path, ledger)
            pre_send, pre_resent = do_send(hc_bin, args, reset_cmd, ledger, ledger_path)
            reset_sent = True
            ledger.update(phase="reset_waiting", reset_send=pre_send, reset_resent=pre_resent)
            write_json_atomic(ledger_path, ledger)
            wait_for_reset(hc_bin, args, time.monotonic() + args.wall_clock)
            ledger.update(phase="reset_done", reset_done_at=now_iso())
            write_json_atomic(ledger_path, ledger)

        # 2.6) 切模型前置（--target-model，ADR-0006）：重置（如有）之后、主任务之前，
        #      发字面命令 /model <名字>，等落定（idle/done + 屏幕静置）再发主任务。
        #      名字原样透传：脚本不认识模型、不按 agent 映射、不核实生效、不预读跳过；
        #      与 --reset 零耦合，可单独带。放行一并授权；前置不抽取、不落 run_record；
        #      发不出即整轮停（台账记部分状态 model_sent）。
        if args.target_model:
            model_cmd = "/model " + args.target_model
            ledger.update(phase="model_sending")
            write_json_atomic(ledger_path, ledger)
            pre_send, pre_resent = do_send(hc_bin, args, model_cmd, ledger, ledger_path)
            model_sent = True
            ledger.update(phase="model_waiting", model_send=pre_send, model_resent=pre_resent)
            write_json_atomic(ledger_path, ledger)
            wait_for_reset(hc_bin, args, time.monotonic() + args.wall_clock)
            ledger.update(phase="model_done", model_done_at=now_iso())
            write_json_atomic(ledger_path, ledger)

        # 3) 主任务发送（墙钟从主任务发送成功起计，不重置）。
        started = time.monotonic()
        send, resent = do_send(hc_bin, args, text, ledger, ledger_path)
        baseline = send["turn"]
        ledger.update(phase="sent_waiting", sent_at=now_iso(), send=send, resent=resent)
        write_json_atomic(ledger_path, ledger)

        # 4) 等待落定（墙钟从主任务发送成功起计）。
        deadline = started + args.wall_clock
        final = wait_for_result(hc_bin, args, baseline, deadline)

        # 5) 取回当前屏 + 完整性检查。
        read = hc_json(hc_bin, ["read", args.target, "--lines", str(READ_LINES)], QUERY_GUARD_S)
        screen = read["text"]
        # 锚点（提示词回声首行前缀）只作信息、不阻断：本步已在 turn>基线＋落定之后，
        # 本轮确已被处理；长任务跑完提示词回声滚出当前屏（--lines 0）是常态，
        # 回复仍在屏底，锚点搜不到不代表回复不完整（PAW 实测多次误判后降级）。
        anchor_found = verbatim_present(anchor, screen)
        # 折叠标记仍是硬判据：▲▼ 说明目标 TUI 藏了内容，当前屏看不到完整回复。
        reasons = []
        if "▲" in screen or "▼" in screen:
            reasons.append("fold_markers")
        complete = not reasons
        elapsed = round(time.monotonic() - started, 1)

        # 6) 整屏原料逐字落盘（不加任何头尾），台账记读取元信息。
        raw_path.write_text(screen, encoding="utf-8", newline="")
        ledger.update(
            phase="read", read_at=now_iso(),
            turn=final.get("turn"), status=status_state(final),
            elapsed_s=elapsed, chars=len(screen),
            hc_read={"complete": read.get("complete"), "how": read.get("how"), "why": read.get("why")},
            complete=complete, complete_reason=reasons, anchor_found=anchor_found,
            run_record=str(raw_path), raw_record=str(raw_path),
        )
        write_json_atomic(ledger_path, ledger)

        # 折叠判据命中即 error，不进 pi；原文已照常落盘。锚点缺失只记信息、不阻断。
        if not complete:
            raise DispatchError(
                "incomplete_result",
                "完整性判据命中：" + "、".join(reasons),
                [f"整屏原文已落盘：{raw_path}", "请用户回看原文；不按键、不自动补发"],
            )

        # 7) 裁剪＋抽取（默认 API 直连，pi 备选）＋逐字校验（裁定 C），合并输出。
        ledger.update(phase="extracting")
        write_json_atomic(ledger_path, ledger)
        ext = extract(pi_bin, args, raw_path)
        reply = ext.pop("reply")
        rewritten = verify_extraction(ext, screen, raw_path)
        bad_reply_lines = verify_reply(reply, screen)
        if bad_reply_lines:
            rewritten["reply"] = closest_fragment(bad_reply_lines[0], screen)
        # 干净全文落盘为回看对象；整屏原料另存审计。
        record_path.write_text(reply, encoding="utf-8", newline="")
        ledger.update(rewritten=rewritten, run_record=str(record_path),
                      reply_chars=len(reply), reply_bad_lines=len(bad_reply_lines))
        result = {
            "ok": True,
            "phase": "done",
            "release_id": args.release_id,
            "task_name": task_name,
            "target": original_target,
            "reset": args.reset,
            "turn": final.get("turn"),
            "status": status_state(final),
            "complete": complete,
            "complete_reason": reasons,
            "anchor_found": anchor_found,
            "elapsed_s": elapsed,
            "chars": len(screen),
            "run_record": str(record_path),
            "raw_record": str(raw_path),
            "prompt_sha256": sha,
            "rewritten": rewritten,
            "extract_backend": backend,
            **ext,
        }
        # target_model 仅传了才出现：语义是「本轮请求切换到的名字」，非「已验证生效」。
        if args.target_model:
            result["target_model"] = args.target_model
        # 命名会话（ADR-0009）：target 恒为传入地址（名字或 t 号），target_address 是解析
        # t 号（审计定位）；launched/rebuilt/workspace/reset_skipped 等适用字段随 ctx 带出。
        # turn 是终端级累计计数（复活会话 turn>0 但会话是空的）。
        for k in ("target_address", "launched", "rebuilt", "rebuilt_old_target",
                  "workspace", "reset_skipped", "display_name", "display_name_warning"):
            if k in ctx:
                result[k] = ctx[k]
        ledger.update(phase="done", done_at=now_iso())
        write_json_atomic(ledger_path, ledger)
        emit_result(result, result_path)
        return 0
    except DispatchError as e:
        if ledger is not None and ledger_path is not None:
            ledger.update(phase=f"error:{e.kind}", error={"kind": e.kind, "message": e.message},
                          failed_at=now_iso())
            try:
                write_json_atomic(ledger_path, ledger)
            except OSError:
                pass
        out = {
            "ok": False,
            "release_id": args.release_id,
            "target": original_target,
            "error": {"kind": e.kind, "message": e.message, "hints": e.hints},
        }
        if task_name is not None:
            out["task_name"] = task_name
        if backend is not None:
            out["extract_backend"] = backend
        if args.reset != "none":
            out["reset"] = args.reset
            out["error"]["reset_sent"] = reset_sent
        if args.target_model:
            out["target_model"] = args.target_model
            out["error"]["model_sent"] = model_sent
        # 命名会话部分状态（ADR-0009）：终端已建/启动已发/旧会话已死/领养失败原因等如实报。
        for k in ("target_address", "launched", "launch_sent", "rebuilt",
                  "rebuilt_old_target", "workspace", "adopt_reason",
                  "space_created", "pane_adopted", "reset_skipped",
                  "display_name", "display_name_warning"):
            if k in ctx:
                out[k] = ctx[k]
        if started is not None:
            out["error"]["elapsed_s"] = round(time.monotonic() - started, 1)
        if ledger is not None and ledger.get("run_record"):
            out["error"]["run_record"] = ledger["run_record"]
        emit_result(out, result_path)
        return e.returncode


if __name__ == "__main__":
    sys.exit(main())
