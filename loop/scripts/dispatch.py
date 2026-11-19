#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""dispatch.py — loop skill 的分发脚本（ADR-0002，方案 C）。

运行方式：uv run --no-python-downloads scripts/dispatch.py …
（只用机器上已有的解释器；uv 会在 ~/.cache/uv 建一个很小的 venv，属正常运行环境。）

机械活：读放行提示词 → 核对目标 → hc send --if idle → hc wait 循环 →
hc read → 完整性检查 → 原文逐字落盘 → pi 提炼 → 合并成一行 JSON。
异常直接返回 error（退出码非零），不进 pi。只用 Python 标准库。

机械保证（不靠提示词）：
  - 原文照发：文件内容一字不改地作为 hc send 的位置参数。
  - 至多发送成功一次：先写台账 .loop/runs/<放行id>.json 再发送；台账存在即拒绝。
  - 首发只用 --if idle；被拒（status_mismatch）且 hc get 复核为 done 时，
    再核一次非 working/blocked 后不带 --if 补发一次（被拒时零字节进入，不算重发）。
    永不 --force，不碰 hc keys。
  - 发送前核对目标身份（--expect-title / --expect-proc，可选但强烈建议传）。
  - 台账记录实际发出内容的 sha256，成功输出也带 prompt_sha256。
  - 完整性判据（锚点 + 折叠标记）命中即 error，不进 pi；原文照常落盘。
  - 提炼字段逐字校验：搜不到原文的字段标 rewritten 并附最接近片段；
    全部搜不到则 error(extraction_rewritten)。

已裁定默认值（issue #16，2026-11-19）：墙钟 3600s（发送成功起计、不重置）、
check-in 180s、落定静置 10s × 2 次、pi 提炼护栏 1200s、hc read --lines 0。
"""

import argparse
import difflib
import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# 版本门：写成 3.9 也能解析的形式。低于 3.10 直接返回 error，
# 不进入流程（3.9 的 Path.write_text 没有 newline 参数，会在落盘时半路崩）。
if sys.version_info < (3, 10):
    print(json.dumps({
        "ok": False,
        "error": {
            "kind": "python_too_old",
            "message": "需要 Python >= 3.10，当前 %d.%d.%d" % sys.version_info[:3],
            "hints": ["用 uv run --no-python-downloads scripts/dispatch.py 运行，"
                      "只用机器上已有的 3.10+ 解释器"],
        },
    }, ensure_ascii=False))
    sys.exit(1)

# 机械护栏（非等待策略，不属待拍板项）：
# hc send 会等人未写完的草稿（draft_wait 默认 60 秒），护栏须大于它。
SEND_GUARD_S = 120.0
# hc get / hc read 都是即时查询，30 秒足够宽。
QUERY_GUARD_S = 30.0
# hc wait 子进程护栏 = 本次 --timeout + 余量。
WAIT_GUARD_MARGIN_S = 30.0
# 裁定 A3：只取当前屏。对 grok，--lines > 0 时 hc 的 complete 恒 false，语义失真。
READ_LINES = 0

EXTRACT_FIELDS = ("needs_user", "questions", "summary", "conclusion", "blocked")


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


def verbatim_present(value, text):
    """逐字子串校验（锚点与提炼字段共用）。先按字面搜；屏幕会折行、把 tab
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


def verify_extraction(ext, screen, record_path):
    """裁定 C：questions/conclusion/summary/blocked 全部逐字摘原文。
    对每个非空字段值做子串校验；搜不到的标 rewritten 并附最接近片段；
    全部搜不到则 error。needs_user 是唯一的布尔判断字段，不参与校验。"""
    checks = [("summary", ext["summary"]), ("conclusion", ext["conclusion"]),
              ("blocked", ext["blocked"])]
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
            "提炼字段全部未通过逐字校验：pi 在改写而不是抽取",
            [f"原文已落盘：{record_path}",
             "最接近的原文片段：" + json.dumps(rewritten, ensure_ascii=False)[:300]],
        )
    return rewritten


def check_identity(terminal, args):
    if args.expect_title:
        titles = [terminal.get("title") or ""]
        st = terminal.get("status")
        if isinstance(st, dict):
            titles.append(st.get("title") or "")
        if not any(args.expect_title in t for t in titles):
            raise DispatchError(
                "identity_mismatch",
                f"目标标题不含预期子串（--expect-title）",
                ["核对 hc get 的 title / status.title；不要向身份不符的终端发送"],
            )
    if args.expect_proc:
        fg = terminal.get("foreground") or {}
        if args.expect_proc not in (fg.get("name") or ""):
            raise DispatchError(
                "identity_mismatch",
                "目标前台进程名不含预期子串（--expect-proc）",
                ["核对 hc get 的 foreground.name；不要向身份不符的终端发送"],
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
            and isinstance(obj.get("summary"), str)
            and isinstance(obj.get("conclusion"), str)
        ):
            return {k: obj[k] for k in EXTRACT_FIELDS}
    raise DispatchError(
        "extraction_invalid",
        "pi 输出中找不到符合固定形状的 JSON",
        ["原文已落盘，可人工用 pi 按 prompts/extract.md 重做提炼", stdout.strip()[:300]],
    )


def extract(pi_bin, args, record_path):
    extract_md = Path(__file__).resolve().parent.parent / "prompts" / "extract.md"
    if not extract_md.is_file():
        raise DispatchError("missing_prompt", f"找不到提炼提示词：{extract_md}", [])
    instruction = (
        "原文已随消息附上（@文件）。按系统提示词抽取，"
        "只输出一个 JSON 对象，含 needs_user、questions、summary、conclusion、blocked 五个字段。"
        f'原文落盘路径：{record_path}'
    )
    argv = [
        pi_bin, "-p",
        "--no-tools", "--no-session", "--no-extensions", "--no-skills",
        "--no-prompt-templates", "--no-context-files", "--no-mcp",
        "--append-system-prompt", str(extract_md),
    ]
    if args.pi_model:
        argv += ["--model", args.pi_model]
    argv += ["--", f"@{record_path}", instruction]
    p = run_cmd(argv, args.extract_timeout)
    if p.returncode != 0:
        raise DispatchError(
            "extraction_failed",
            f"pi 退出码 {p.returncode}",
            ["原文已落盘，可人工重做提炼", (p.stderr or p.stdout).strip()[:300]],
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
        description="loop 分发脚本：发送已放行提示词、等待落定、取回原文落盘、pi 提炼。",
    )
    ap.add_argument("--target", required=True, help="目标终端地址（hc 的 t7、名字或 machine/t7）")
    ap.add_argument("--release-id", required=True, help="放行 id（唯一；一个放行只跑一轮）")
    ap.add_argument("--prompt-file", required=True, help="已放行的提示词文件路径（原文照发）")
    ap.add_argument("--expect-title", help="身份预检：目标标题应含此子串")
    ap.add_argument("--expect-proc", help="身份预检：前台进程名应含此子串（如 grok）")
    ap.add_argument("--loop-dir", default=".loop", help="本地记录根目录（默认 ./.loop）")
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
                    help="pi 提炼子进程护栏秒数（默认 1200）")
    return ap.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    ledger = None
    ledger_path = None
    started = None
    try:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", args.release_id):
            raise DispatchError("bad_release_id", "放行 id 只允许字母数字与 ._-，且不以符号开头", [])
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
        record_path = runs / f"{args.release_id}.md"
        ledger_path = runs / f"{args.release_id}.json"
        if ledger_path.exists() or record_path.exists():
            raise DispatchError(
                "duplicate_release",
                f"放行 id {args.release_id} 已有台账或原文落盘，一个放行只跑一轮",
                ["查看台账确认上次结果；要重发须经用户重新放行、用新 id"],
            )
        hc_bin = shutil.which("hc")
        pi_bin = shutil.which("pi")
        if not hc_bin:
            raise DispatchError("missing_tool", "PATH 里找不到 hc", [])
        if not pi_bin:
            raise DispatchError("missing_tool", "PATH 里找不到 pi",
                                ["某些机器非交互 shell 的 PATH 里没有 node，"
                                 "pi 起不来时先确认 PATH 并显式补上"])

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
            "target": args.target,
            "prompt_path": str(prompt_path),
            "prompt_sha256": sha,
            "anchor": anchor,
            "params": {
                "wall_clock": args.wall_clock, "check_in": args.check_in,
                "settle_seconds": args.settle_seconds, "settle_checks": args.settle_checks,
                "lines": READ_LINES, "extract_timeout": args.extract_timeout,
            },
            "phase": "sending",
            "created_at": now_iso(),
        }
        write_json_atomic(ledger_path, ledger)

        started = time.monotonic()
        resent = False
        try:
            send = hc_json(
                hc_bin,
                ["send", "--if", "idle", "--", args.target, text],
                SEND_GUARD_S,
            )
        except HcFailure as e:
            if e.kind != "status_mismatch":
                kind = "send_refused" if e.kind == "blocked" else "hc_failed"
                raise DispatchError(kind, f"hc send 失败：{e.message}",
                                    e.hints + ["未再发送；台账已记录本次尝试"])
            # 裁定 6：--if idle 被拒（零字节进入，不算重发）→ hc get 复核，
            # 确认 done 才补发；补发前再核一次非 working/blocked。
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
            resent = True
        baseline = send["turn"]
        ledger.update(phase="waiting", sent_at=now_iso(), send=send, resent=resent)
        write_json_atomic(ledger_path, ledger)

        # 3) 等待落定（墙钟从发送成功起计）。
        deadline = started + args.wall_clock
        final = wait_for_result(hc_bin, args, baseline, deadline)

        # 4) 取回当前屏 + 完整性检查。
        read = hc_json(hc_bin, ["read", args.target, "--lines", str(READ_LINES)], QUERY_GUARD_S)
        screen = read["text"]
        reasons = []
        if not verbatim_present(anchor, screen):
            reasons.append("anchor_missing")
        if "▲" in screen or "▼" in screen:
            reasons.append("fold_markers")
        complete = not reasons
        elapsed = round(time.monotonic() - started, 1)

        # 5) 原文逐字落盘（不加任何头尾），台账记读取元信息。
        record_path.write_text(screen, encoding="utf-8", newline="")
        ledger.update(
            phase="read", read_at=now_iso(),
            turn=final.get("turn"), status=status_state(final),
            elapsed_s=elapsed, chars=len(screen),
            hc_read={"complete": read.get("complete"), "how": read.get("how"), "why": read.get("why")},
            complete=complete, complete_reason=reasons,
            run_record=str(record_path),
        )
        write_json_atomic(ledger_path, ledger)

        # 裁定 A1：判据命中即 error，不进 pi；原文已照常落盘。
        if not complete:
            raise DispatchError(
                "incomplete_result",
                "完整性判据命中：" + "、".join(reasons),
                [f"原文已落盘：{record_path}", "请用户回看原文；不按键、不自动补发"],
            )

        # 6) pi 提炼 + 逐字校验（裁定 C），合并输出。
        ledger.update(phase="extracting")
        write_json_atomic(ledger_path, ledger)
        ext = extract(pi_bin, args, record_path)
        rewritten = verify_extraction(ext, screen, record_path)
        ledger.update(rewritten=rewritten)
        result = {
            "ok": True,
            "target": args.target,
            "turn": final.get("turn"),
            "status": status_state(final),
            "complete": complete,
            "complete_reason": reasons,
            "elapsed_s": elapsed,
            "chars": len(screen),
            "run_record": str(record_path),
            "prompt_sha256": sha,
            "rewritten": rewritten,
            **ext,
        }
        ledger.update(phase="done", done_at=now_iso())
        write_json_atomic(ledger_path, ledger)
        print(json.dumps(result, ensure_ascii=False))
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
        if started is not None:
            out["error"]["elapsed_s"] = round(time.monotonic() - started, 1)
        if ledger is not None and ledger.get("run_record"):
            out["error"]["run_record"] = ledger["run_record"]
        print(json.dumps(out, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
