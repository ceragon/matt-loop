#!/usr/bin/env python3
"""turn.py — 回读与校验的纯文本函数（ADR-0020 决定 10）。

自 `dispatch.py` 迁入，函数体不变：断轮、逐字校验、折叠判据、会话起始触发、
粘贴归一与计量、任务短名生成；后增回声剥离 strip_echo（ADR-0026，caller 档
turn_text 用）。纯函数、无 I/O、无子进程；失败用 `DispatchError`
（errors.py）。`dispatch.py` 重导出本模块全部旧名，既有调用点与测试零改动。
"""

import difflib
import hashlib
import re

from errors import DispatchError

# 会话起始 skill（ADR-0004）：新会话必须以其中之一开场，调用时目标会话必须先重置。
# 清单出自 /ask-matt 工作流（默认调用方已装 Matt 全套 skill）；中途 skill
# （to-spec/to-tickets/tdd/code-review/retro 等）不在内，普通回复不触发。
# 行首匹配：允许行前空白，命令后须不是词字符或连字符（/implementing、/implement-foo
# 不误中，/wayfinder#7 无空格带参算调用）；implement-spec 排在 implement 前，命中时报长名。
# 行文中提及不触发——讨论工作流的普通回复要放行；「调用独占一行」由 SKILL.md 纪律约定。
SESSION_START_RE = re.compile(
    r"^[ \t]*/(implement-spec|implement|wayfinder|grill-with-docs|prototype)(?![\w-])",
    re.MULTILINE)


def generate_short_task_name(release_id: str) -> str:
    h = hashlib.sha256(release_id.encode("utf-8")).hexdigest()[:8]
    name = f"loop-{h}"
    return name[:32]


def find_session_start_trigger(text):
    """行首斜杠命令命中会话起始 skill 时返回 (skill, 行号)，否则 None。"""
    m = SESSION_START_RE.search(text)
    if m is None:
        return None
    return m.group(1), text.count("\n", 0, m.start()) + 1


def normalize_paste(text):
    """复刻 Claude Code 2.1.331 粘贴归一化：CRLF/CR→LF、tab→4 空格。"""
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\t", "    ")


def utf16_units(text):
    """UTF-16 码元数（中文 1、emoji 2）：UTF-16-LE 编码字节数除以 2。"""
    return len(text.encode("utf-16-le")) // 2


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


def cut_turn(text, anchor):
    """断轮（Turn Cut，ADR-0013）：从回读全文里机械切出本轮段，确定性、不用模型。

    优先取「去前导空白后以 ❯ 开头且含锚点」的最后一行（提示词回声形态），从该行
    切到全文末尾——回复正文引用锚点但无 ❯ 前缀时不会误切回复开头。无 ❯ 形态回声
    时退化为含锚点的最后一行；锚点完全找不到则整段使用（诊断不因断轮失败再丢失）。
    锚点匹配沿用 verbatim_present（字面＋去空白折叠容错）。选中的回声行保留在段内。
    返回 (turn_text, echo_found, cut_line)：cut_line 是 1 起切点行号；整段使用时
    echo_found=False、cut_line=1、turn_text 逐字等于全文。
    """
    lines = text.splitlines(keepends=True)
    cut = None
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].lstrip().startswith("❯") and verbatim_present(anchor, lines[i]):
            cut = i
            break
    if cut is None:
        for i in range(len(lines) - 1, -1, -1):
            if verbatim_present(anchor, lines[i]):
                cut = i
                break
    if cut is None:
        return text, False, 1
    return "".join(lines[cut:]), True, cut + 1


def strip_echo(turn_text, prompt_text):
    """回声剥离（ADR-0026 决定 3，caller 档 turn_text 用）：确定性机械步，不用模型。

    脚本手里有发送提示词原文：从断轮切点（回声行）起，把段首逐行去空白归一后与
    提示词归一文本做前缀匹配，消耗掉回声及其软换行续行（首行先剥 ❯ 前缀与前导
    空白）；提示词归一文本被**完整消耗**且仍有剩余行时，首个不匹配行即回复首行。
    返回 (body, warning)：干净剥离 warning=None。
    不猜（echo_degraded）：首行即不匹配、提示词未消耗完（TUI 折叠回声「首行＋…」
    等匹配发散）、或消耗完无剩余行（空回复）——退回含回声行的段全文，交调用方
    自识别。锚点完全未命中（whole_segment）由调用方判 echo_found，不进本函数。
    """
    strip_ws = lambda s: re.sub(r"\s+", "", s)
    prompt_norm = strip_ws(prompt_text)
    lines = turn_text.splitlines(keepends=True)
    consumed = ""
    i = 0
    while i < len(lines):
        ln = lines[i]
        if i == 0:
            ln = ln.lstrip()
            if ln.startswith("❯"):
                ln = ln[1:]
        cand = consumed + strip_ws(ln)
        if cand and prompt_norm.startswith(cand):
            consumed = cand
            i += 1
        else:
            break
    if i == 0 or consumed != prompt_norm or i >= len(lines):
        return turn_text, "echo_degraded"
    return "".join(lines[i:]), None


# 折叠判据（ADR-0019，#79）：只认 claude 实证形态，两独立分支。
# … 恒 U+2026（真实语料 e2 80 a6 核过），不是三个 ASCII 点；行首允许前导空白，
# 单复数 lines?（模板有 +1 line 形态）。分支二字面不带左括号——
# ⎿ Backgrounded agent (↓ to manage · ctrl+o to expand) 变体左括号后另有内容。
FOLD_COUNT_RE = re.compile(r"… \+\d+ lines?\b")
FOLD_HINT = "ctrl+o to expand"


def fold_hits(turn_text, prompt_text):
    """折叠判据（ADR-0019）：返回命中行列表（空＝完整）。命中不阻断，只标记 complete:false。

    两独立分支：去前导空白后行首匹配 … +N lines；行内含 ctrl+o to expand。
    回声排除：本轮段里与提示词某行（去首尾空白后）精确相同的行不参与——
    自己发出去的已知文本不是 TUI 渲染证据。.turn.md 本身不动（逐字校验语料维持 ADR-0013）。
    残余（降级吸收）：回声首行带 ❯ 前缀、超宽行软换行碎段无法精确匹配；
    其他 kind 与键位重映射形态可能漏检（ADR-0019 缺口声明）。
    返回值目前只取真值（是否命中）；行文本保留供人工复核对照，不进 result。
    """
    prompt_lines = {ln.strip() for ln in prompt_text.splitlines() if ln.strip()}
    hits = []
    for ln in turn_text.splitlines():
        s = ln.strip()
        if not s or s in prompt_lines:
            continue
        if FOLD_COUNT_RE.match(ln.lstrip()) or FOLD_HINT in ln:
            hits.append(ln)
    return hits


def verify_extraction(ext, turn_text, turn_path, raw_path, backend):
    """裁定 C：questions/conclusion/blocked 全部逐字摘原文（语料＝本轮原料，ADR-0013）。
    对每个非空字段值做子串校验；搜不到的标 rewritten 并附最接近片段；
    全部搜不到则 error。needs_user 是布尔判断字段、next_skill 是推测字段
    （ADR-0008），均不参与校验。

    错误载荷（#50）：hints 每个未过校验字段一条「字段名＋最接近的原文整行」
    （closest_fragment 原样整行、零截断），另给本轮原料与回读全文路径；不再对
    序列化 JSON 切片。message 按本轮实际抽取后端（api|pi）取值，不写死 pi。"""
    checks = [("conclusion", ext["conclusion"]), ("blocked", ext["blocked"])]
    checks += [(f"questions[{i}]", q) for i, q in enumerate(ext["questions"])]
    rewritten = {}
    checked = 0
    for key, val in checks:
        if not val.strip():
            continue
        checked += 1
        if not verbatim_present(val, turn_text):
            rewritten[key] = closest_fragment(val, turn_text)
    if checked and len(rewritten) == checked:
        hints = [f"{key} 未过逐字校验，最接近的原文整行：{frag}"
                 for key, frag in rewritten.items()]
        hints += [f"本轮原料已落盘：{turn_path}", f"回读全文已落盘：{raw_path}"]
        raise DispatchError(
            "extraction_rewritten",
            f"抽取字段全部未通过逐字校验：{backend} 在改写而不是抽取",
            hints,
        )
    return rewritten


def verify_reply(reply, turn_text):
    """reply 是抽取器裁剪后的干净全文（语料＝本轮原料，ADR-0013）。先整体子串校验
    （回复本是连续整块时最强）；不中再逐行校验（允许删掉穿插的杂物行）。
    返回未过逐字校验的行列表，全过返回 []。"""
    if not reply.strip():
        return ["(reply 为空)"]
    if verbatim_present(reply, turn_text):
        return []
    return [ln for ln in reply.splitlines()
            if ln.strip() and not verbatim_present(ln, turn_text)]
