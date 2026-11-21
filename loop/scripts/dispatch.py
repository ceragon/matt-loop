#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""dispatch.py — loop skill 的分发脚本（ADR-0002，方案 C）。

运行方式：python3 scripts/dispatch.py …（脚本自带版本门：>=3.10 直跑；<3.10
自动经 uv run --no-python-downloads 用机器上已有的 3.10+ 解释器重跑，不下载。）

机械活：读放行提示词 → 核对目标 → hc send --if idle → hc wait 循环 →
hc read → 完整性检查 → 整屏原料落盘 .raw.md → pi 裁剪出干净全文 reply 落盘 .md
＋抽取选段字段 → 合并成一行 JSON。异常直接返回 error（退出码非零），不进 pi。
只用 Python 标准库。

机械保证（不靠提示词）：
  - 原文照发：主任务文件内容一字不改地作为 hc send 的位置参数。
  - 重置前置（--reset new/clear/compact，默认 none）：放行一并授权前置＋主任务；
    先发字面命令、等落定（idle/done + 屏幕静置）再发主任务；前置按枚举校验、不抽取。
  - 一个放行至多发送成功两次（前置＋主任务，各一次）：先写台账
    $TMPDIR/loop/runs/<放行id>.json 再发送；台账存在即拒绝。
  - 首发只用 --if idle；被拒（status_mismatch）且 hc get 复核为 done 时，
    再核一次非 working/blocked 后不带 --if 补发一次（被拒时零字节进入，不算重发）。
    永不 --force，不碰 hc keys。
  - 发送前核对目标身份（--expect-proc，认 foreground.name/argv[0]/status.app，可选但强烈建议传）。
  - 台账记录实际发出内容的 sha256，成功输出也带 prompt_sha256。
  - 完整性判据：折叠标记（▲▼）命中即 error，不进 pi；原文照常落盘。锚点（提示词
    回声首行前缀）只作信息 anchor_found、不阻断——检查在 turn>基线＋落定之后，
    长任务回声滚出当前屏是常态，回复仍在屏底。
  - 工具定位：hc/pi/uv 先 shutil.which，PATH 找不到时自动探常见目录（~/.local/bin、
    homebrew、nvm node bin），后台 job 缺 PATH 也能起。
  - 本地记录归置：--loop-dir 默认 $TMPDIR/loop（tempfile.gettempdir()/loop），runs/ 放
    记录、releases/ 放放行提示词；随 OS 临时清理、不留持久审计，台账拒重发保护止于
    重启（放行 id 每次唯一）。PATH 由 find_tool 兜底，不需手写包装脚本。
  - 抽取字段逐字校验：搜不到原文的字段标 rewritten 并附最接近片段；
    全部搜不到则 error(extraction_rewritten)。reply 干净全文先整体、再逐行校验，
    未过逐字校验的行标 rewritten["reply"]。

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
诊断只记录事实，不改鉴权、证书校验或代理策略。长任务用调用方自己的后台任务跑、靠完成通知收结果
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
# 可选 webhook 的 POST 超时：只是完成通知，短超时即可，发失败不连累整轮。
WEBHOOK_TIMEOUT_S = 10.0

# summary 已移除（原文才是重点）：pi 若仍多给 summary，投影时直接丢弃，
# 不算坏行、不进 rewritten。
EXTRACT_FIELDS = ("needs_user", "questions", "conclusion", "blocked", "reply")


class DispatchError(Exception):
    def __init__(self, kind, message, hints=None):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.hints = hints or []


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


def _proxy_diagnostic(url):
    """只报「是否配置了代理」与「该请求是否实际命中代理」，两值均为
    yes/no/unknown；不输出代理地址、端口、用户名或密码。
    「命中」按 urllib 的真实行为判断：<scheme> 键存在且未被 proxy_bypass 放行；
    只配了 ALL_PROXY（urllib 不按协议分派 'all'）时如实标 unknown，不猜。"""
    diag = {"configured": "unknown", "matches": "unknown"}
    try:
        proxies = urllib.request.getproxies()
    except Exception:
        return diag
    diag["configured"] = "yes" if proxies else "no"
    if not proxies:
        diag["matches"] = "no"
        return diag
    try:
        parts = urllib.parse.urlsplit(url)
        scheme, host = parts.scheme.lower(), parts.hostname
        if not host or scheme not in ("http", "https"):
            return diag           # 判断不了：matches 保持 unknown
        if urllib.request.proxy_bypass(host):
            diag["matches"] = "no"
        elif scheme in proxies:
            diag["matches"] = "yes"
        elif "all" in proxies:
            diag["matches"] = "unknown"
        else:
            diag["matches"] = "no"
    except Exception:
        diag["matches"] = "unknown"
    return diag


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


def send_webhook(result):
    """可选 webhook 完成通知（Grok Bot 等支持 webhook 唤醒的调用方用）。
    env LOOP_WEBHOOK_URL＋LOOP_WEBHOOK_SECRET 都配了才发，默认关。成功与报错都
    POST 结果 JSON；鉴权走 Authorization: Bearer <密钥>，不进日志、不进 stdout。
    webhook 只是通知，结果文件才是事实源：发失败不连累整轮。
    返回 (off/sent/failed, webhook_error, webhook_error_detail)；失败时
    webhook_error 只记 HTTP 状态码（如 "HTTP 401"）或异常类名（如 "URLError"），
    webhook_error_detail 是同一份脱敏诊断（见 webhook_error_detail），stderr
    打印的也是它——三处信息一致，都不含 URL/密钥/主机名/头内容。"""
    url = os.environ.get("LOOP_WEBHOOK_URL")
    secret = os.environ.get("LOOP_WEBHOOK_SECRET")
    if not url or not secret:
        return "off", None, None
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(result, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + secret},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=WEBHOOK_TIMEOUT_S) as resp:
            resp.read()
        return "sent", None, None
    except urllib.error.HTTPError as e:
        err = f"HTTP {e.code}"
        detail = webhook_error_detail(e, url)
    except Exception as e:
        err = type(e).__name__
        detail = webhook_error_detail(e, url)
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
    全部搜不到则 error。needs_user 是唯一的布尔判断字段，不参与校验。"""
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


def check_identity(terminal, args):
    if args.expect_proc:
        fg = terminal.get("foreground") or {}
        st = terminal.get("status") or {}
        argv = fg.get("argv") or []
        first = argv[0] if isinstance(argv, list) and argv else ""
        # claude 等的 foreground.name 可能是版本号；argv[0] 与 status.app 才是可靠信号
        candidates = [fg.get("name") or "", first, st.get("app") or ""]
        if not any(args.expect_proc in c for c in candidates):
            raise DispatchError(
                "identity_mismatch",
                "目标前台进程不含预期子串（--expect-proc）",
                ["核对 hc get 的 foreground.name/argv[0] 与 status.app；不要向身份不符的终端发送"],
            )


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
    """等前置（/new /clear /compact）落定：短轮询 hc get 至 idle/done，再屏幕静置复核。
    不要求 turn 增长（/clear 可能不增 turn）。前置不抽取、不落 run_record。"""
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
            return {k: obj[k] for k in EXTRACT_FIELDS}
    raise DispatchError(
        "extraction_invalid",
        "pi 输出中找不到符合固定形状的 JSON",
        ["原文已落盘，可人工用 pi 按 prompts/extract.md 重做抽取", stdout.strip()[:300]],
    )


def extract(pi_bin, args, raw_path):
    extract_md = Path(__file__).resolve().parent.parent / "prompts" / "extract.md"
    if not extract_md.is_file():
        raise DispatchError("missing_prompt", f"找不到抽取提示词：{extract_md}", [])
    instruction = (
        "整屏原文已随消息附上（@文件）。按系统提示词裁剪并抽取，"
        "只输出一个 JSON 对象，含 needs_user、reply、questions、conclusion、blocked 五个字段。"
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
    ap.add_argument("--target", required=True, help="目标终端地址（hc 的 t7、名字或 machine/t7）")
    ap.add_argument("--release-id", required=True, help="放行 id（唯一；一个放行只跑一轮）")
    ap.add_argument("--prompt-file", required=True, help="已放行的提示词文件路径（原文照发）")
    ap.add_argument("--reset", choices=["none", "new", "clear", "compact"], default="none",
                    help="主任务前的重置前置，发字面命令 /new /clear /compact（调用方按 agent 认哪个挑）；"
                         "默认 none 不重置，即现状单 prompt")
    ap.add_argument("--expect-proc", help="身份预检：前台进程应含此子串（认 name/argv[0]/app，如 grok/claude）")
    ap.add_argument("--loop-dir", default=str(Path(tempfile.gettempdir()) / "loop"),
                    help="本地记录根目录（默认 $TMPDIR/loop，随 OS 临时清理）")
    ap.add_argument("--pi-model", help="pi --model 透传（默认用 pi 自身配置的模型）")
    # ---- 已裁定默认值（issue #16，2026-11-19），可覆盖 ----
    ap.add_argument("--wall-clock", type=positive_float, default=3600.0,
                    help="墙钟上限秒数，从发送成功起计，不重置（默认 3600）")
    ap.add_argument("--check-in", type=positive_float, default=180.0,
                    help="hc wait 每次 check-in 秒数（默认 180）")
    ap.add_argument("--settle-seconds", type=positive_float, default=10.0,
                    help="落定复核每次静置秒数（默认 10）")
    ap.add_argument("--settle-checks", type=positive_int, default=2,
                    help="落定复核次数（默认 2）")
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
    try:
        check_structure_and_tools()
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
        pi_bin = find_tool("pi")
        if not hc_bin:
            raise DispatchError("missing_tool", "PATH 与常见目录里都找不到 hc", [])
        if not pi_bin:
            raise DispatchError("missing_tool", "PATH 与常见目录里都找不到 pi",
                                ["某些机器非交互 shell 的 PATH 里没有 node；"
                                 "脚本已探 ~/.local/bin、homebrew、nvm node bin 仍未果"])

        # 1) 预检：状态 idle/done + 身份（票面第 2 步；done 时 --if idle 会拒发，
        #    走下面裁定 6 的复核补发路径）。
        g = hc_json(hc_bin, ["get", args.target], QUERY_GUARD_S)
        if g.get("state") != "live":
            raise DispatchError("not_idle", "目标终端已结束，不发送", [])
        state = status_state(g)
        if state not in ("idle", "done"):
            raise DispatchError(
                "not_idle",
                f"目标状态是 {state or '未自报'}，不是 idle/done，不发送",
                ["--if idle 只认 idle；等目标落定或请用户处理后再来"],
            )
        check_identity(g, args)

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
            "params": {
                "wall_clock": args.wall_clock, "check_in": args.check_in,
                "settle_seconds": args.settle_seconds, "settle_checks": args.settle_checks,
                "lines": READ_LINES, "extract_timeout": args.extract_timeout,
            },
            "phase": "sending",
            "created_at": now_iso(),
        }
        write_json_atomic(ledger_path, ledger)

        # 2.5) 重置前置（--reset != none）：同一 target 上先发字面命令 /new /clear /compact，
        #      等落定（idle/done + 屏幕静置）后再发主任务。放行一并授权前置＋主任务：
        #      前置按枚举校验（argparse choices），主任务仍逐字来自放行文件、校 sha256。
        #      前置不抽取、不落 run_record；任一步发不出即整轮停（台账记部分状态）。
        if args.reset != "none":
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

        # 7) pi 裁剪 + 抽取 + 逐字校验（裁定 C），合并输出。
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
            "task_name": task_name,
            "target": args.target,
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
            **ext,
        }
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
        out = {"ok": False, "error": {"kind": e.kind, "message": e.message, "hints": e.hints}}
        if args.reset != "none":
            out["reset"] = args.reset
            out["error"]["reset_sent"] = reset_sent
        if started is not None:
            out["error"]["elapsed_s"] = round(time.monotonic() - started, 1)
        if ledger is not None and ledger.get("run_record"):
            out["error"]["run_record"] = ledger["run_record"]
        emit_result(out, result_path)
        return 1


if __name__ == "__main__":
    sys.exit(main())
