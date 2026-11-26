#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""dispatch.py — loop skill 的分发脚本（ADR-0002，方案 C）。

运行方式：python3 scripts/dispatch.py …（脚本自带版本门：>=3.10 直跑；<3.10
自动经 uv run --no-python-downloads 用机器上已有的 3.10+ 解释器重跑，不下载。）

机械活：读放行提示词 → 核对目标 → hc send --if idle → hc wait 循环 →
hc read（回读最近 1000 行历史＋当前屏，ADR-0013）→ 断轮切出本轮段 → 完整性检查
（只对本轮段）→ 回读全文落盘 .raw.md＋本轮原料落盘 .turn.md → 抽取器裁剪出干净
全文 reply 落盘 .md＋抽取选段字段（默认直连 API，pi 显式备选，ADR-0005）→ 合并成
一行 JSON。异常直接返回 error（退出码非零），不进抽取。只用 Python 标准库。

机械保证（不靠提示词）：
  - 原文照发：主任务文件内容一字不改地作为 hc send 的位置参数。
  - 重置前置（--reset new/clear/compact，默认 none）：放行一并授权前置＋主任务；
    先发字面命令、等落定（idle/done + 屏幕静置）再发主任务；前置按枚举校验、不抽取。
  - 切模型前置（--target-model <名字>，ADR-0006）：重置（如有）之后、主任务之前发
    字面命令 /model <名字>，等落定再发主任务；名字原样透传、不核实生效、不预读跳过、
    不按 agent 映射，与 --reset 独立可单独带；target_model 是请求值非验证值。
  - 命名会话 get-or-create（ADR-0009）：--target 推荐传会话名（一名一 live 终端，名字恒解析
    到最新 live）。预检 hc get 分流：not_found/finished→创建（hc run --bg --keep --no-parent
    --name --cwd -- zsh -l 单形态 shell 包装，--no-parent 无父链、独立于调用方终端（#46，
    调用方背景终端退出不连带杀）→打 loop=1/loop-agent 标签→等 shell 就绪→裸 send --launch
    启动命令→领养（先于等就绪，#46）→等 agent 就绪→核身份）；live＋空闲 shell＋loop 标签符→复活（原地重发启动命令，
    名字/cwd/终端级历史不动）；live＋别的 agent/程序或无 loop 标签→session_occupied 停轮呈报，
    --rebuild 经用户显式授权只杀 idle/done 占位者后同名重建。--launch 字面透传 shell 命令、
    --cwd 显式给目录（二者成对必填、无默认、脚本零映射）；需创建/复活却没传→launch_required
    （退出码 2、零发送、id 不消耗、补参同 id 重试）。启动命令是命令不是提示词：不抽取、不落
    run_record，占前置发送槽。换 kind＝换 --target 到另一命名会话（--switch-agent 已移除）。
  - 恒可见（领养，ADR-0009）：命名会话恒有 herdr-app pane。创建路径启动命令发出即领养
    （先于等 agent 就绪，#46：卡登录/授权/网络弹框也先可见，就绪失败另报状态不影响可见性）、
    既有/复活路径每轮预检（写台账前）经单一 app_json 咽喉：herdr-app status 探测（绝不用裸
    herdr-app，它会开窗）→只读 ls 核 pane→缺则按 --space 三态解析目标空间（路径零匹配
    space.new{name:目录名,folder:路径,focus:false}）→do terminal.keep→ls 复核落地→
    space_created 时 tab.close confirmed:true→轮询等赠送终端 finished→hc rm 清赠送 shell
    （#46：tab.close 异步结束终端，单次 rm 撞 live 被拒；清理失败降级 gift_cleanup_warning
    不丢已成功的领养）。进不了即 adopt_failed 整轮报错、不降级 headless：
    创建路径保留已建终端、id 消耗；预检路径零发送、id 不消耗。app 没开时所有轮次停摆
    （已接受的代价）。
  - 空间定位（--space 必传，ADR-0011，#43 grill 定稿）：keep 无空间定位参数（place 只管
    相对焦点 pane 位置），无 pane 终端落进当前选中空间。--space 三态解析归一纯函数
    parse_space（#70，唯一匹配实现，领养与学习共用；建不建空间是调用方策略——领养路径
    零匹配建、学习只读不建）：/ 或 ~ 开头→路径（按 space.folder 匹配：唯一复用／零个
    领养执行 space.new{name:目录名,folder,focus:false}／多个 space_ambiguous；路径须
    已存在目录否则 bad_args）；sp 前缀→id 直配；其余→名（ls 唯一匹配，
    重名 bad_args，不存在 space_not_found）。与 --cwd 独立。只在需要 keep 时生效：目标≠当前
    shown 才 space.select→status 复核（不符先走还原护栏再 select_error，keep 不发）；
    目标==shown 不 select。keep→复核 pane 及其所在空间（落进非目标空间→space_target_warning
    告警不丢放行）→还原选中。还原护栏：用户接管选中即跳过（user_switched）；keep 失败路径
    也还原；还原失败降级 space_restore_warning 不丢放行。pane 已在提前返回不抢。
  - 显示名层（ADR-0010）：--display-name 调用方显式传完整字符串，脚本不派生、不拼接、不理解、
    不校验式样，只机械校验（非空、1–64 字符、禁换行控制字符，非法 bad_args）＋落地。新鲜路径
    （创建/复活/重建）必传，缺则 display_name_required（与 launch_required 同构：退出码 2、
    零发送、id 不消耗、补参同 id 重试；两参同缺 launch_required 先报）；复用轮可选，传了就覆盖、
    不传不碰。落地时序：领养复核 pane 落地后、前置/主任务发送前，ls 定位 tab id→tab.rename
    →ls 复核 tab.name==显示名（命令成功不算数）；台账 naming phase。失败降级告警不丢放行：
    rename 失败/复核不符/无 tab→display_name_warning（rename_error/rename_mismatch/no_tab），
    主流程继续；pane 落地仍是硬闸（adopt_failed 不变）。result display_name 回显本轮传入值
    （落地步骤一跑就带，含 no_tab/ls 失败等降级情形，成功与 error 都带）。不写 summary 标签
    （机器可读靠自动跟随 tab 名的 where 标签）；space.new 的 name 按 ADR-0011 为目录名。
  - 一个放行至多发送成功三次（重置或启动前置＋切模型前置＋主任务，各一次；刚创建/复活的
    新鲜会话自动跳过重置，result 记 reset_skipped:"fresh_launch"，切模型前置照跑）：先写台账
    $TMPDIR/loop/runs/<放行id>.json 再发送；台账存在即拒绝。
  - 会话起始 skill 纪律（ADR-0004）：提示词行首出现 /wayfinder、/grill-with-docs、
    /implement、/implement-spec、/prototype 之一且 --reset=none 时，在 hc get ＋
    身份预检之后、写台账前校验：turn == 0（全新空会话）直接豁免放行；turn > 0
    或缺失时拒发（reset_required，退出码 2），放行 id 不消耗，补 --reset 同 id 可重试。
    分流判定创建/复活/重建的新鲜会话视同 turn 0 豁免、不看终端 turn（turn 是终端级
    累计计数，复活会话 turn>0 但会话是空的，ADR-0009 修正 ADR-0007）。
    其前另有静态形态闸 split_required（ADR-0017，#61）：命中触发器且归一化 UTF-16
    码元 >700 或含内部换行（尾部换行不算）时，在工具查找/hc get 之前拒发（退出码 2、
    零发送零副作用、id 不消耗），先于 reset_required、不分 agent、与 --reset 独立。
  - 首发只用 --if idle；被拒（status_mismatch）且 hc get 复核为 done 时，
    再核一次非 working/blocked 后不带 --if 补发一次（被拒时零字节进入，不算重发）。
    永不 --force，不碰 hc keys。
  - 发送前核对目标身份（--expect-proc，认 foreground.argv[0]/status.app，可选但强烈建议传；
    foreground.name 不可靠——claude 实测为版本号，不作判据，ADR-0009）。
  - 台账记录实际发出内容的 sha256，成功输出也带 prompt_sha256。
  - 完整性判据（ADR-0019，#79）：折叠判据只查本轮段（与提示词行精确相同的回声行不参与），
    只认实证形态（行首 … +N lines／行内 ctrl+o to expand）；命中只标记 complete:false，
    照常抽取、照常出 result，incomplete_result 退役。锚点（提示词回声首行前缀）只作信息
    anchor_found、不阻断——检查在 turn>基线＋落定之后，超过 1000 行历史时回声仍可能不可得，
    搜不到不代表回复不完整（ADR-0013）。
  - 工具定位：hc/uv（及 backend=pi 时的 pi）先 shutil.which，PATH 找不到时自动探常见目录（~/.local/bin、
    homebrew、nvm node bin），后台 job 缺 PATH 也能起。
  - 本地记录归置：--loop-dir 默认 $TMPDIR/loop（tempfile.gettempdir()/loop），runs/ 放
    记录、releases/ 放放行提示词；随 OS 临时清理、不留持久审计，台账拒重发保护止于
    重启（放行 id 每次唯一）。PATH 由 find_tool 兜底，不需手写包装脚本。
  - 抽取字段逐字校验：搜不到原文的字段标 rewritten 并附最接近片段；
    全部搜不到则 error(extraction_rewritten)。reply 干净全文先整体、再逐行校验，
    未过逐字校验的行标 rewritten["reply"]。next_skill 是推测字段（ADR-0008 修订，#48）：
    下一轮应使用的纯 skill 名（候选固定七项、不带斜杠不含参数），豁免逐字校验，
    缺失/非字符串/带斜杠/带参数/集外值均宽容投影为 ""，不整轮报错。
  - 本地学习（ADR-0012，#48 阶段二，learning.py）：LOOP_LEARNING=off 可整体关闭。开启时
    主任务发送成功后机械结算上一轮同 target 预测（next_skill_feedback）＋记同 space＋target
    近期 skill 序列；成功抽取后创建待观察（含空预测）；抽取前选 ≤4 条同 space 案例注入既有
    调用（不新增模型请求）。存储默认 ${XDG_DATA_HOME:-~/.local/share}/matt-loop/learning/
    （LOOP_LEARNING_DIR 可覆盖），90 天＋每 space 100 条，短临界区锁不跨抽取调用；任何
    锁/I/O/权限/损坏降级 learning_warning，不新增 error kind、不改退出码、不阻断主轮。
  - 抽取后端（ADR-0005）：LOOP_EXTRACT_BACKEND=api|pi，默认 api。api 走 OpenAI 兼容
    chat completions（LOOP_API_BASE/KEY/MODEL 全必填无默认，启动预检缺失即
    api_not_configured）；temp 0、max_tokens 默认 32768（LOOP_API_MAX_TOKENS 可覆盖）、
    超时复用 --extract-timeout、不重试不流式；代理与 webhook 同源（ADR-0003）；
    失败只记状态码/类名，不读响应体，密钥/URL 零回显。pi 备选路径照旧。

交付（双产物，ADR-0014，#52 阶段一）：stdout 一行 JSON，同一字符串同时写
$TMPDIR/loop/runs/<放行id>.result.json（全量结果，成功与 error 都写，审计与回看对象）；
另写 <放行id>.notify.json（通知投影＋通知记录，逐字记录实际 POST 正文）。成功通知严格
只含 13 个判读字段（ok/release_id/target/needs_user/questions/conclusion/blocked/
rewritten/run_record/elapsed_s/next_skill/complete/complete_reason，ADR-0019 增补），
error 通知保留发送前完整对象。调用方
（DS Bot/Grok Bot）统一先读 notify.json、需要完整诊断/审计/回看再读 result.json。
run_record 指抽取器裁剪后的干净全文 .md（回看对象），raw_record 指回读全文 .raw.md
（审计，含旧轮），turn_record 指本轮原料 .turn.md（抽取器输入＋逐字校验语料，ADR-0013）。
台账记过程，结果文件记最终交付，重发拒绝仍以台账为准。
中途本地 IO 失败（OSError）由顶层兜底映射 io_failed（退出码恒 1），走同一 error 出口；
hints 按台账已开与否分两态（未开→同 id 重试；已开→新放行 id），有现成语义的读取点
逐点包既有 kind（sha 复核→prompt_unreadable、抽取提示词→missing_prompt，ADR-0024）。
可选 webhook 完成通知：env LOOP_WEBHOOK_URL＋LOOP_WEBHOOK_SECRET 都配了才发，
成功/报错都 POST 通知投影字节（与 notify.json 完全相同，只序列化一次、重试复用；
鉴权走 Authorization: Bearer），发失败不连累整轮。顺序：构造投影→序列化→写通知记录→
首次 POST→重试→写全量结果＋stdout；通知记录写失败仍唤醒，全量结果记 notify_record_error
（类名）＋stderr 诊断，不改退出码/ok、不递归第二条 error 通知。结果记 webhook:
off/sent/failed，失败另记 webhook_error（HTTP 状态码或异常类名）与 webhook_error_detail
（脱敏诊断：异常类名、reason 类名、errno/errno_name、是否超时、SSL 验证错误码、HTTP
状态码、代理是否配置/是否命中）；这些发送诊断只归全量结果、不进通知投影。不收
str(e)/repr(e)/字符串 reason/响应体，不含 URL、密钥、主机名、头内容。stderr 打印同一份诊断。
出网默认跟随本机系统代理：标准 http_proxy/https_proxy 显式配置时优先，否则读
系统代理（macOS），系统例外表或 no_proxy 命中则直连；只作用于 webhook POST，
不改证书校验、不关代理、不换网络策略。长任务用调用方自己的后台任务跑、靠完成通知收结果
（DS Bot 用 job 完成通知、Grok Bot 用 webhook，见 SKILL.md 交付清单），
不要用 hc exec 或短超时包装器同步等。

已裁定默认值（issue #16，2026-11-19）：墙钟 3600s（发送成功起计、不重置）、
check-in 180s、落定静置 10s × 2 次、抽取护栏 1200s、hc read --lines 1000（ADR-0013）。
"""

import argparse
import enum
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
import types
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# learning.py 与本脚本同目录；无论 dispatch 被直跑还是经 importlib 加载（部分测试不加
# sys.path），都先把脚本目录挂上，保证兄弟模块可导入。
sys.path.insert(0, str(Path(__file__).resolve().parent))
import learning  # noqa: E402
# 兄弟模块（部署单元是 loop/scripts/ 整目录）。依赖方向 dispatch → herdr/turn → errors，无环。
from errors import DispatchError  # noqa: E402,F401
from herdr import Herdr, HerdrFailure, NotFound  # noqa: E402
from turn import (  # noqa: E402,F401
    FOLD_COUNT_RE, FOLD_HINT, SESSION_START_RE, SESSION_START_SKILLS,
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

# 成功通知投影白名单（ADR-0014，#52 阶段一；ADR-0019 增补完整性标记）：成功通知严格只含这 13 个判读/交回字段，
# 顺序即下列顺序。error 通知不套白名单、保留发送前完整对象。webhook/webhook_error/
# webhook_error_detail 是 POST 后才产生的发送诊断，只归全量结果，绝不进投影。
NOTIFY_SUCCESS_FIELDS = (
    "ok", "release_id", "target", "needs_user", "questions", "conclusion",
    "blocked", "rewritten", "run_record", "elapsed_s", "next_skill",
    "complete", "complete_reason",
)


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
# 迁移前 hc_json 只把「非零退出」包成独立进程层失败类（现已并入 HerdrFailure）；进程/
# 输出层失败（run_cmd 的 missing_tool/tool_timeout 与 hc_json 的 hc_output）原样穿透。
# 网关把这些也收进 HerdrFailure，这里按 kind 恢复逐字穿透（G2，ADR-0018 决定 9）。
_HC_PASSTHROUGH_KINDS = frozenset({"missing_tool", "tool_timeout", "hc_output"})
# 回读范围（ADR-0013，supersede 裁定 A3「只取当前屏」）：请求最近 1000 行拼接
# 历史＋当前屏（hc stitched 历史上限）。长回复滚出当前屏的部分仍可取回；超过
# 1000 行历史的开头仍不可得（如实声明，不承诺无限历史）。hc 自报 complete 只记
# 台账、不作判据（A3 所称 grok complete 恒 false 未全面证伪，见 ADR-0013 证据边界）。
READ_LINES = 1000
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

# summary 已移除（原文才是重点）：抽取器若仍多给 summary，投影时直接丢弃，
# 不算坏行、不进 rewritten。
# next_skill（ADR-0008 修订，#48）是唯一推测字段：下一轮应使用的纯 skill 名（不带斜杠、
# 不含参数），候选固定七项；缺失/非字符串/带斜杠/带参数/集外值均宽容投影为 ""，不整轮
# 报错；不参与逐字校验。格式错误另记 invalid（learning.project_next_skill），供阶段二
# 统计 invalid_prediction，不得把格式错误算作正确弃答。
EXTRACT_FIELDS = ("needs_user", "questions", "conclusion", "blocked", "reply", "next_skill")

# 会话起始 skill 命令行形态闸（ADR-0017，#61）：Claude Code 把超体积或多行粘贴
# 折叠成 [Pasted text #N +M lines] 块，块内行首斜杠命令不展开、skill 不被调用。
# 上游实证折叠阈值 800 UTF-16 码元（2.1.331 原生二进制内嵌 JS：常量 @323578697，
# 判定式 长度>800 OR 换行数>max(0,min(rows-10,2)) @343663417），留余量取 700。
# 硬编码、零旋钮（无 env、无旗标），与 READ_LINES 同等待遇；改值须重验上游版本。
SPLIT_MAX_UTF16 = 700


def has_internal_newline(text):
    """内部换行＝剥掉尾部换行（含连续多个）后仍含换行（尾换行豁免，ADR-0017 定案 A）。"""
    return "\n" in text.rstrip("\n")


# 抽取后端（ADR-0005）：默认直连 API（OpenAI 兼容 chat completions），pi 为显式备选。
# API 参数全走 env、全必填、无内置默认；密钥与 URL 零回显。
EXTRACT_BACKENDS = ("api", "pi")
API_MAX_TOKENS_DEFAULT = 32768


def resolve_extract_backend(env=None):
    """LOOP_EXTRACT_BACKEND=api|pi，默认 api；非法值是参数类错误（退出码 2）。"""
    v = (env if env is not None else os.environ).get("LOOP_EXTRACT_BACKEND", "api").strip().lower()
    if v not in EXTRACT_BACKENDS:
        raise DispatchError("bad_extract_backend",
                            "LOOP_EXTRACT_BACKEND 值非法（只认 api|pi）", [], returncode=2)
    return v


def api_config(env=None):
    """读 API 三个 env＋可选 max_tokens；缺必填项报 api_not_configured
    （只列变量名，不带值），max_tokens 非法报 bad_api_max_tokens。启动预检用。"""
    names = ("LOOP_API_BASE", "LOOP_API_KEY", "LOOP_API_MODEL")
    _env = env if env is not None else os.environ
    vals = {n: _env.get(n, "").strip() for n in names}
    missing = [n for n in names if not vals[n]]
    if missing:
        raise DispatchError(
            "api_not_configured",
            "抽取后端=api 但 env 缺失：" + ", ".join(missing),
            ["配齐三个变量（见 webhook.env.example 抽取配置节）后重试",
             "或设 LOOP_EXTRACT_BACKEND=pi 走 pi 备选"],
            returncode=2)
    try:
        max_tokens = int(_env.get("LOOP_API_MAX_TOKENS", "").strip()
                         or API_MAX_TOKENS_DEFAULT)
        if max_tokens <= 0:
            raise ValueError
    except ValueError:
        raise DispatchError("bad_api_max_tokens",
                            "LOOP_API_MAX_TOKENS 须为正整数", [], returncode=2)
    return (vals["LOOP_API_BASE"].rstrip("/"), vals["LOOP_API_KEY"],
            vals["LOOP_API_MODEL"], max_tokens)


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
    """跑一条 hc 命令并解析 --json 输出；非零退出抛 HerdrFailure。"""
    # --json 必须紧跟子命令：send 用 `--` 保护以 - 开头的提示词文本，
    # `--` 之后的一切都被 clap 当位置参数，选项放后面会被吞掉。
    p = run_cmd([hc_bin, args[0], "--json", *args[1:]], timeout_s)
    if p.returncode != 0:
        detail = try_json(p.stdout) or try_json(p.stderr)
        kind = None
        if isinstance(detail, dict):
            kind = detail.get("kind") or (detail.get("error") or {}).get("kind")
        raise HerdrFailure(
            kind or "hc_failed",
            f"hc {' '.join(args)} 退出码 {p.returncode}"
            + (f"（{kind}）" if kind else ""),
            [s.strip()[:300] for s in (p.stderr, p.stdout) if s and s.strip()],
            p.returncode,
            op="hc_json", source="hc", native_kind=kind, detail=detail,
        )
    data = try_json(p.stdout)
    if not isinstance(data, dict):
        raise HerdrFailure(
            "hc_output",
            f"hc {' '.join(args)} 的 stdout 不是 JSON 对象",
            [p.stdout.strip()[:300]],
            op="hc_json", source="hc",
        )
    return data


def app_json(app_bin, args, timeout_s):
    """跑一条 herdr-app 命令并解析 JSON 输出；非零退出抛 HerdrFailure。
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
        raise HerdrFailure(
            "app_failed",
            f"herdr-app {' '.join(args[:2])} 退出码 {p.returncode}"
            + (f"：{str(err)[:200]}" if err else ""),
            [s.strip()[:300] for s in (p.stderr, p.stdout) if s and s.strip()],
            p.returncode,
            op="app_json", source="app", detail=detail,
        )
    data = try_json(p.stdout)
    if not isinstance(data, dict):
        raise HerdrFailure(
            "app_output",
            f"herdr-app {' '.join(args[:2])} 的 stdout 不是 JSON 对象",
            [p.stdout.strip()[:300]],
            op="app_json", source="app",
        )
    return data


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


def notify_projection(result):
    """通知投影（ADR-0014，#52 阶段一）：从完整结果选出唤醒载荷。
    成功（ok=True）严格只取 NOTIFY_SUCCESS_FIELDS 白名单字段（结果缺某字段则不带，
    不补造）；error 不套白名单，原样返回发送前完整对象（含 kind/message/hints 与
    全部部分状态）。投影在 POST 前构造，故不含 webhook/webhook_error/
    webhook_error_detail 等发送后才产生的诊断——那些只归全量结果。"""
    if result.get("ok"):
        return {k: result[k] for k in NOTIFY_SUCCESS_FIELDS if k in result}
    return result


def send_webhook(body, deps=None):
    """可选 webhook 完成通知（Grok Bot 等支持 webhook 唤醒的调用方用）。
    env LOOP_WEBHOOK_URL＋LOOP_WEBHOOK_SECRET 都配了才发，默认关。body 是调用方
    预先序列化好的通知投影字节（notify_projection → json.dumps 一次），本函数不再
    序列化、不改写：首发与所有重试、直连兜底复用同一组字节（ADR-0014）。
    鉴权走 Authorization: Bearer <密钥>，不进日志、不进 stdout。
    webhook 只是通知，结果文件才是事实源：发失败不连累整轮。
    出网默认跟随本机系统代理（见 webhook_proxy_resolution）：主出口最多尝试 3 次
    （退避 0.5s、1.0s）；若用了代理且 3 次均因瞬态/5xx 失败，再强制直连（proxies={}）
    兜底 1 次；HTTP 4xx 等非 5xx 状态码立刻失败、不重试也不兜底。
    返回 (off/sent/failed, webhook_error, webhook_error_detail)；失败时
    webhook_error 与 webhook_error_detail 保留主出口最后一次错误（与 detail["proxy"]
    一致），另带 attempts（总尝试次数），走过直连兜底时附 fallback_direct 与
    fallback_error，stderr 打印同一份脱敏诊断，都不含 URL/密钥/主机名/头内容。"""
    deps = deps if deps is not None else Deps()
    url = deps.env.get("LOOP_WEBHOOK_URL")
    secret = deps.env.get("LOOP_WEBHOOK_SECRET")
    if not url or not secret:
        return "off", None, None
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
            with deps.http_open(req, WEBHOOK_TIMEOUT_S, proxies) as resp:
                resp.read()
            return "sent", None, None
        except Exception as e:
            primary_exc = e
            if not _is_retryable_webhook_error(e):
                break
            if i < len(WEBHOOK_RETRY_DELAYS):
                deps.sleep(WEBHOOK_RETRY_DELAYS[i])
    fallback_exc = None
    fallback_direct = False
    if proxies and _is_retryable_webhook_error(primary_exc):
        fallback_direct = True
        attempts += 1
        try:
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            with deps.http_open(req, WEBHOOK_TIMEOUT_S, {}) as resp:
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


def emit_result(deps, obj, result_path, notify_path):
    """双产物交付（ADR-0014，#52 阶段一）。顺序：构造通知投影 → 只序列化一次 →
    写通知记录 notify.json → 首次 POST → 必要重试（复用同一组字节）→ 写全量结果
    result.json 并输出 stdout。通知记录字节与实际请求体完全相同（不追加换行），
    无论 webhook 是否启用都生成。通知记录写失败仍尝试 POST，全量结果记
    notify_record_error（异常类名）、stderr 打印诊断；不改退出码与 ok，不递归
    生成第二条 error 通知。webhook: off/sent/failed 等发送诊断只记全量结果，
    不进通知投影。stdout 一行 JSON，与全量结果文件逐字一致。尚未建立安全运行
    路径的早期 error（result_path/notify_path 为 None）不构造通知记录，保留既有
    error 输出与发送行为。"""
    body = json.dumps(notify_projection(obj), ensure_ascii=False).encode("utf-8")
    if notify_path is not None:
        try:
            notify_path.parent.mkdir(parents=True, exist_ok=True)
            notify_path.write_bytes(body)
        except OSError as e:
            obj["notify_record_error"] = type(e).__name__
            sys.stderr.write("[dispatch] 通知记录写入失败：%s %s（仍尝试唤醒；结果文件仍是事实源）\n"
                             % (type(e).__name__, e))
    status, wh_err, wh_detail = send_webhook(body, deps)
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


def dialog_error(session):
    st = session.status or {}
    kind = st.get("kind")
    msg = st.get("msg") or ""
    return DispatchError(
        "dialog_blocked",
        "目标会话停在弹框等人决定" + (f"（kind={kind}）" if kind else "") + (f"：{msg}" if msg else ""),
        ["不按键、不代答；请用户到目标会话屏幕前处理，处理后用新放行 id 重新发起"],
    )


def wait_for_result(deps, herdr, args, baseline, deadline):
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
        t = min(args.check_in, remaining)
        try:
            w = herdr.wait_change(args.target, t)
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
                raise dialog_error(herdr.session(args.target))
            if state == "error":
                raise DispatchError("target_error", "目标自报 error", ["到目标会话屏幕前查看"])
            if state in ("idle", "done") and (w.turn or 0) > baseline:
                outcome = settle(deps, herdr, args, w.turn)
                if outcome is not None:
                    return outcome
                continue
            # working、或 turn 未过基线：继续等
            continue
        # timeout（check-in）及其他 reason：用 hc get 兜底核对一次
        g = herdr.session(args.target)
        if g.state != "live":
            raise DispatchError("target_exited", "目标终端已结束", ["hc get 查看 reason"])
        state = g.status_state
        if state == "blocked":
            raise dialog_error(g)
        if state == "error":
            raise DispatchError("target_error", "目标自报 error", ["到目标会话屏幕前查看"])
        if state in ("idle", "done") and (g.turn or 0) > baseline:
            outcome = settle(deps, herdr, args, g.turn)
            if outcome is not None:
                return outcome


def settle(deps, herdr, args, wait_turn):
    """落定复核：静置 settle_seconds 后 hc get，连续 settle_checks 次
    状态仍 idle/done 且 turn 不变才算落定。回到 working 等则返回 None 继续等。"""
    for _ in range(args.settle_checks):
        deps.sleep(args.settle_seconds)
        g = herdr.session(args.target)
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


def do_send(herdr, args, text, ledger, ledger_path):
    """--if idle 首发；被拒（status_mismatch）且 hc get 复核为 done 才补发一次。
    返回 (send, resent)。前置与主任务共用同一套发送护栏（裁定 6）：
    永不 --force、不碰 hc keys；被拒时零字节进入，不算重发。
    首发与补发是两个网关操作（ADR-0018 决定 5/8 补记）：被拒且复核 done 时，
    本层在两次复核之间落账 phase=resending＋first_refusal（与基线逐字一致），
    再调 herdr.resend；外部 kind/文案/hints 映射仍在本调用点。"""
    try:
        receipt = herdr.send(args.target, text, guard="idle")
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
    ledger.update(phase="resending",
                  first_refusal={"kind": receipt.first_refusal["kind"],
                                 "returncode": receipt.first_refusal["returncode"]})
    write_json_atomic(ledger_path, ledger)
    try:
        resent = herdr.resend(args.target, text)
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


def reset_settle(deps, herdr, args):
    """前置落定复核：idle/done 且屏幕连续 settle_checks 次静置不变。
    /clear 是本地命令、可能不进 working/不增 turn，故用屏幕内容稳定而非 turn 判据。
    屏幕仍在变或离开 idle/done 返回 False；blocked/error/退出照常抛错。"""
    prev = None
    for _ in range(args.settle_checks):
        deps.sleep(args.settle_seconds)
        g = herdr.session(args.target)
        if g.state != "live":
            raise DispatchError("target_exited", "前置落定复核时目标终端已结束", [])
        state = g.status_state
        if state == "blocked":
            raise dialog_error(g)
        if state == "error":
            raise DispatchError("target_error", "前置落定复核时目标自报 error", [])
        if state not in ("idle", "done"):
            return False
        screen = herdr.read(args.target)
        if prev is not None and screen != prev:
            return False
        prev = screen
    return True


def wait_for_reset(deps, herdr, args, deadline):
    """等前置（/new /clear /compact /model）落定：短轮询 hc get 至 idle/done，再屏幕静置复核。
    不要求 turn 增长（/clear、/model 可能不增 turn）。前置不抽取、不落 run_record。"""
    while True:
        if deps.monotonic() >= deadline:
            raise DispatchError(
                "timeout",
                "前置墙钟超时：目标仍未落定",
                ["主任务未发送；本轮已停止，可经用户放行后另起一轮"],
            )
        g = herdr.session(args.target)
        if g.state != "live":
            raise DispatchError("target_exited", "前置期间目标终端已结束", ["hc get 查看 reason"])
        state = g.status_state
        if state == "blocked":
            raise dialog_error(g)
        if state == "error":
            raise DispatchError("target_error", "前置期间目标自报 error", ["到目标会话屏幕前查看"])
        if state in ("idle", "done"):
            if reset_settle(deps, herdr, args):
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


def wait_for_agent_ready(deps, herdr, args, deadline):
    """等 agent 就绪：前台不再是 shell 且状态 idle/done。超时/终端结束→launch_failed；
    弹框/error 照常抛（既有 kind 不动）。返回就绪时的 Session。"""
    while True:
        if deps.monotonic() >= deadline:
            raise DispatchError("launch_failed", "等 agent 就绪超时（启动命令已发）",
                                ["主任务未发送；本轮已停止，可经用户放行后另起一轮"])
        try:
            g = herdr.session(args.target)
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


def send_launch_command(herdr, args, ledger, ledger_path, ctx):
    """启动前置发送（创建/复活共用）：shell 不是 agent、不报 idle，用裸 send（不带 --if）。
    启动命令是命令不是提示词：不抽取、不落 run_record，占前置发送槽。
    只管发送不等就绪（#46 Bug3）：创建路径在发送与等就绪之间插领养（恒可见）。"""
    ledger.update(phase="launch_sending")
    write_json_atomic(ledger_path, ledger)
    try:
        receipt = herdr.send(args.target, args.launch, guard=None)
    except HerdrFailure as e:
        if e.kind in _HC_PASSTHROUGH_KINDS:
            raise
        raise DispatchError("launch_failed", f"启动命令发送失败：{e.message}",
                            e.hints + ["主任务未发送；台账已记录本次尝试"])
    ctx["launch_sent"] = True
    ledger.update(launch_sent=True, launch_send=receipt.raw, phase="launch_waiting_agent")
    write_json_atomic(ledger_path, ledger)


def wait_launch_agent(deps, herdr, args, ledger, ledger_path):
    """等 agent 就绪＋核身份（创建/复活共用）。创建路径在领养之后跑（#46 Bug3）：
    卡弹框/超时失败时会话已领养可见，就绪失败另报状态、不影响可见性。"""
    ledger.update(phase="launch_waiting_agent")
    write_json_atomic(ledger_path, ledger)
    g = wait_for_agent_ready(deps, herdr, args, deps.monotonic() + args.wall_clock)
    check_identity(g.raw, args)
    ledger.update(phase="launch_done", launch_done_at=now_iso())
    write_json_atomic(ledger_path, ledger)
    return g


def do_create(deps, herdr, args, ledger, ledger_path, ctx):
    """创建前置（ADR-0009，单形态 shell 包装）：hc run --bg --keep --no-parent --name
    --cwd -- zsh -l→打 loop=1＋loop-agent=<expect-proc> 标签→等 shell 就绪→send 启动命令。
    等 agent 就绪＋核身份拆在 wait_launch_agent（#46 Bug3：主流程在两者之间插领养，
    卡弹框也先可见）。名字唯一性由 herdr 仲裁（name_taken 竞态映射 launch_failed）。
    任一步失败整轮 launch_failed，ctx/台账带部分状态（终端已建/启动已发如实报）。"""
    ledger.update(phase="launch_running")
    write_json_atomic(ledger_path, ledger)
    try:
        # --no-parent（#46 Bug1）：命名会话无父链、独立于调用方终端——调用方背景
        # 终端退出/被杀不连带杀会话（die_with_parent 默认 true 会让会话活不过一轮），跨轮持久。
        created = herdr.create_session(args.target, args.cwd)
    except HerdrFailure as e:
        if e.stage == "missing_address":
            raise DispatchError("launch_failed", e.message, e.hints)
        if e.kind in _HC_PASSTHROUGH_KINDS:
            raise
        raise DispatchError("launch_failed", f"hc run 创建命名会话失败（{e.kind}）：{e.message}",
                            e.hints + ["同名 live 终端存在（name_taken）时不建不 kill；"
                                       "经用户放行后另起一轮或改用既有会话"])
    addr = created.address
    ctx["target_address"] = addr
    ctx["launched"] = True
    ledger.update(target_address=addr, launched=True, phase="launch_labeling")
    write_json_atomic(ledger_path, ledger)
    labels = ["loop=1"]
    if args.expect_proc:
        labels.append("loop-agent=" + args.expect_proc)
    try:
        herdr.label_session(args.target, labels)
    except HerdrFailure as e:
        if e.kind in _HC_PASSTHROUGH_KINDS:
            raise
        raise DispatchError("launch_failed", f"打 loop 标签失败：{e.message}",
                            e.hints + ["终端已建（launched:true）；无标签终端下轮会被拒收编"])
    ledger.update(phase="launch_waiting_shell")
    write_json_atomic(ledger_path, ledger)
    wait_for_shell_ready(deps, herdr, args.target, deps.monotonic() + args.wall_clock)
    send_launch_command(herdr, args, ledger, ledger_path, ctx)


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


def apply_display_name(deps, herdr, address, display_name, ledger, ledger_path, ctx):
    """显示名落地（ADR-0010）：网关 `rename_tab`（ls 定位 tab id → tab.rename → ls 复核
    tab.name==显示名；命令成功不算数）。时序在领养复核 pane 落地后、前置/主任务发送前。
    失败降级告警不丢放行：rename 失败/复核不符/无 tab → ctx/台账记 display_name_warning
    （reason：rename_error/rename_mismatch/no_tab，带详情），主流程继续；pane 落地仍是
    硬闸（adopt_failed 语义不变）。result display_name 回显本轮传入值：落地步骤一跑就带
    （含 no_tab/ls 失败等未真正发出 rename 的降级情形，审计「请求了什么名」），成功与 error 都带。
    不写 summary 标签：机器可读靠自动跟随 tab 名的 where 标签。"""
    herdr.require_app()
    ctx["display_name"] = display_name
    ledger.update(phase="naming", display_name=display_name)
    write_json_atomic(ledger_path, ledger)
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
        ctx["display_name_warning"] = warning
        ledger.update(display_name_warning=warning)
    else:
        ledger.update(phase="naming_done", naming_done_at=now_iso())
    write_json_atomic(ledger_path, ledger)


def adopt(deps, herdr, address, ledger, ledger_path, ctx, precheck, space,
          adopt_timeout=ADOPT_TIMEOUT_S, adopt_poll=ADOPT_POLL_S):
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
    herdr.require_app()
    def fail(reason, message, hints):
        ctx["workspace"] = "failed"
        ctx["adopt_reason"] = reason
        if ledger is not None:
            ledger.update(workspace="failed", adopt_reason=reason)
        raise DispatchError("adopt_failed", message, hints)

    def gift_warning(reason, detail):
        # 赠送清理失败降级告警（#46 Bug2）：pane 已落地、恒可见满足，不丢已成功的领养。
        w = {"reason": reason, "detail": detail}
        ctx["gift_cleanup_warning"] = w
        if ledger is not None:
            ledger.update(gift_cleanup_warning=w)
            write_json_atomic(ledger_path, ledger)

    def record_restore(cm):
        # f.restore 结局写进 ctx 与台账（现行落点：keep 后的 finally，成功失败都记）。
        warning = cm.warning
        if warning is not None and warning.get("reason") == "restore_mismatch":
            # 文案与基线逐字一致：网关 warning 不带「用户可能又切了」注脚。
            warning = dict(warning)
            warning["detail"] = (
                "还原复核选中=%r ≠ 原选中 %r（用户可能又切了）"
                % (warning.get("actual"), original_space))
        ctx["space_restore"] = cm.restore
        if warning is not None:
            ctx["space_restore_warning"] = warning
        if ledger is not None:
            ledger.update(space_restore=cm.restore)
            if warning is not None:
                ledger.update(space_restore_warning=warning)
            write_json_atomic(ledger_path, ledger)

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
        ctx["pane_adopted"] = True
        if ledger is not None:
            ledger.update(pane_adopted=True)
        # 落地复核所在空间（#43）：pane 存在还不够，须在目标空间。keep 异步
        # 落地，窗口内选中被切走时 pane 会落进别的空间——如实告警不静默；
        # pane 已落地恒可见满足，不丢放行（多半是用户自己切走所致）。
        if space_target is not None:
            actual = placement.space_id
            ctx["space_landed"] = actual
            if ledger is not None:
                ledger.update(space_landed=actual)
            if actual != space_target:
                w = {"reason": "landed_elsewhere", "actual": actual,
                     "detail": f"pane 落进空间 {actual!r} ≠ 目标 {space_target!r}"
                               "（落地窗口内选中被切走）"}
                ctx["space_target_warning"] = w
                if ledger is not None:
                    ledger.update(space_target_warning=w)
        return placement

    retry = (["打开 herdr-app 后用同一放行 id 重试（零发送、id 未消耗）"] if precheck
             else ["已建终端保留；打开 herdr-app 后用新放行 id 重试，走既有路径补领养修复"])
    if ledger is not None:
        ledger.update(phase="adopting")
        write_json_atomic(ledger_path, ledger)
    try:
        original_space = herdr.selected_space()
    except DispatchError as e:
        fail("app_closed", f"herdr-app 没开或不可达，领养失败：{e.message}", retry)
    try:
        layout = herdr.layout()
    except DispatchError as e:
        fail("app_closed", f"herdr-app ls 失败，领养失败：{e.message}", retry)
    if layout.placement(address) is not None:
        ctx["workspace"] = "adopted"
        if ledger is not None:
            ledger.update(phase="adopt_done", workspace="adopted")
            write_json_atomic(ledger_path, ledger)
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
        ctx["space_created"] = True
        if ledger is not None:
            ledger.update(space_created=True)
            write_json_atomic(ledger_path, ledger)
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
    ctx["space_selected"] = space_target
    if ledger is not None:
        ledger.update(space_selected=space_target)
        write_json_atomic(ledger_path, ledger)
    selection_switched = space_target != original_space
    if selection_switched:
        if ledger is not None:
            ledger.update(phase="space_selecting")
            write_json_atomic(ledger_path, ledger)
    else:
        ctx["space_restore"] = "not_needed"
        if ledger is not None:
            ledger.update(space_restore="not_needed")
            write_json_atomic(ledger_path, ledger)
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
    if ctx.get("space_created"):
        # 清理赠送 shell（绑 space_created，#43 定稿）：从 keep 落地那次 ls 的落点
        # 定位终端所在 space（刚建、只有赠送 tab＋keep tab），其余 tab 即赠送 shell——
        # tab.close 结束终端，再 hc rm 清 finished 条目。复用同一棵 app 树，不再自读 ls。
        for w in herdr.discard_shell_tabs(
                placement.space_id, address, within=adopt_timeout,
                poll=adopt_poll, layout=placement.layout):
            gift_warning(w["reason"], w["detail"])
    ctx["workspace"] = "adopted"
    if ledger is not None:
        ledger.update(phase="adopt_done", workspace="adopted")
        write_json_atomic(ledger_path, ledger)


def parse_extraction(stdout, meta=None):
    """解析抽取输出成固定六字段。next_skill 经闭集宽容投影（ADR-0008）。

    meta 为可选 dict：给定时写入 meta["next_skill_invalid"]（格式错误标记，供阶段二
    统计），不进入返回的 ext（ext 恒为 EXTRACT_FIELDS 六字段，形状门不检查 next_skill）。
    """
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
            ext = {k: obj[k] for k in EXTRACT_FIELDS if k != "next_skill"}
            # next_skill 是唯一推测字段（ADR-0008）：闭集宽容投影，建议性字段不连累整轮
            # （与 webhook 失败不连累同哲学）。格式错误标 invalid，另供学习统计。
            skill, invalid = learning.project_next_skill(obj.get("next_skill"))
            ext["next_skill"] = skill
            if meta is not None:
                meta["next_skill_invalid"] = invalid
            return ext
    raise DispatchError(
        "extraction_invalid",
        "抽取输出中找不到符合固定形状的 JSON",
        ["原文已落盘，可人工按 prompts/extract.md 重做抽取", stdout.strip()[:300]],
    )


def extract_via_pi(pi_bin, args, turn_path):
    extract_md = Path(__file__).resolve().parent.parent / "prompts" / "extract.md"
    if not extract_md.is_file():
        raise DispatchError("missing_prompt", f"找不到抽取提示词：{extract_md}", [])
    instruction = (
        "本轮原料已随消息附上（@文件）。按系统提示词裁剪并抽取，"
        "只输出一个 JSON 对象，含 needs_user、reply、questions、conclusion、blocked、next_skill 六个字段。"
    )
    # 在线 few-shot（ADR-0012）：同 space 历史案例＋当前近期序列，无历史时为空、沿用原 prompt。
    injection = getattr(args, "learning_injection", "")
    if injection:
        instruction += "\n\n" + injection
    instruction += f'\n本轮原料落盘路径：{turn_path}'
    argv = [
        pi_bin, "-p",
        "--no-tools", "--no-session", "--no-extensions", "--no-skills",
        "--no-prompt-templates", "--no-context-files", "--no-mcp",
        "--append-system-prompt", str(extract_md),
    ]
    if args.pi_model:
        argv += ["--model", args.pi_model]
    argv += ["--", f"@{turn_path}", instruction]
    p = run_cmd(argv, args.extract_timeout)
    if p.returncode != 0:
        raise DispatchError(
            "extraction_failed",
            f"pi 退出码 {p.returncode}",
            ["原文已落盘，可人工重做抽取", (p.stderr or p.stdout).strip()[:300]],
        )
    # 研究表明 pi 模型失败时退出码可能仍为 0，所以无论退出码都要校验输出。
    meta = {}
    ext = parse_extraction(p.stdout, meta)
    args.learning_next_skill_invalid = meta.get("next_skill_invalid", False)
    return ext


def extract_via_api(args, turn_path):
    """直连 API 裁剪＋抽取（ADR-0005）：OpenAI 兼容 chat completions，
    system=extract.md，user=指令＋内联本轮原料（ADR-0013）。temp 0、不流式、不重试、
    不发 response_format；超时复用 --extract-timeout。代理决议与出口和
    webhook 同一套（ADR-0003）。失败只记状态码/类名，不读响应体，密钥/URL 零回显。"""
    base, key, model, max_tokens = api_config()
    extract_md = Path(__file__).resolve().parent.parent / "prompts" / "extract.md"
    if not extract_md.is_file():
        raise DispatchError("missing_prompt", f"找不到抽取提示词：{extract_md}", [])
    # 本轮原料读失败不包：归 io_failed 顶层兜底（ADR-0024，#111）。
    turn_text = Path(turn_path).read_text(encoding="utf-8")
    # 抽取提示词读失败映射既有 missing_prompt（ADR-0024，#111）：is_file 检查后
    # 中途读失败（权限/磁盘故障）保持既有语义，不新增同义 kind。
    try:
        system_prompt = extract_md.read_text(encoding="utf-8")
    except OSError as e:
        raise DispatchError("missing_prompt", f"读不到抽取提示词：{extract_md}：{e}", [])
    instruction = (
        "本轮原料已内联在本消息末尾。按系统提示词裁剪并抽取，"
        "只输出一个 JSON 对象，含 needs_user、reply、questions、conclusion、blocked、next_skill 六个字段。"
    )
    # 在线 few-shot（ADR-0012）：同 space 历史案例＋当前近期序列插在指令与本轮原料之间，
    # 本轮原料仍居消息末尾；无历史时为空、沿用原 prompt（冷启动行为不变）。
    injection = getattr(args, "learning_injection", "")
    user_content = instruction + ("\n\n" + injection if injection else "") + "\n\n" + turn_text
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
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
    meta = {}
    ext = parse_extraction(content, meta)
    args.learning_next_skill_invalid = meta.get("next_skill_invalid", False)
    return ext


def extract(pi_bin, args, turn_path):
    """抽取入口：按 LOOP_EXTRACT_BACKEND 分叉（默认 api 直连，pi 显式备选）。
    两种后端都读本轮原料（ADR-0013），不再读含旧轮的回读全文。"""
    if resolve_extract_backend() == "api":
        return extract_via_api(args, turn_path)
    return extract_via_pi(pi_bin, args, turn_path)


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
        description="loop 分发脚本：发送已放行提示词、等待落定、回读断轮落盘、抽取器抽取。",
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
    ap.add_argument("--space", required=True,
                    help="空间定位（#43 grill 定稿，ADR-0011，必传）：领养 keep 的落点空间。"
                    "三态解析（归一纯函数 parse_space，#70，领养与学习共用匹配）：/ 或 ~ 开头→路径"
                    "（按 space.folder 匹配：唯一复用／零个领养执行 "
                    "space.new{name:目录名,folder:路径,focus:false}／多个 space_ambiguous；"
                    "路径须已存在目录否则 bad_args）；sp 前缀→id 直配；其余→名（ls 唯一匹配，"
                    "重名 bad_args，不存在 space_not_found）。学习侧是只读投影：零匹配不建空间。"
                    "与 --cwd 独立（空间 folder 未必"
                    "等于终端 cwd）。只在需要 keep（pane 缺）时生效：目标≠当前 shown 才 "
                    "space.select→keep→复核→还原选中（用户接管即跳过）；pane 已在提前返回不抢")
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
                    help="抽取子进程护栏秒数（默认 1200，api/pi 两后端共用）")
    return ap.parse_args(argv)


class Deps:
    """外部世界的七件事（ADR-0016 决定 3）：hc_json / app_json 两个命令层调用、
    monotonic + sleep 时钟、只读环境变量映射 env、工具定位 locate、http_open、
    run_cmd、extract。默认实现在**调用时**才解析模块级旧全局（不在构造时绑定），
    因此现有测试的注入方式（dispatch.hc_json = fake 等）照旧生效。
    hc_bin / app_bin / pi_bin 由 main 在原有解析点填入，调用链不再逐层透传。"""

    def __init__(self, *, hc_json=None, app_json=None, monotonic=None, sleep=None,
                 env=None, locate=None, http_open=None, run_cmd=None, extract=None):
        self._hc_json = hc_json
        self._app_json = app_json
        self._monotonic = monotonic
        self._sleep = sleep
        self._env = env
        self._locate = locate
        self._http_open = http_open
        self._run_cmd = run_cmd
        self._extract = extract
        self.hc_bin = None
        self.app_bin = None
        self.pi_bin = None

    def hc_json(self, args, timeout_s):
        return (self._hc_json or hc_json)(self.hc_bin, args, timeout_s)

    def app_json(self, args, timeout_s):
        return (self._app_json or app_json)(self.app_bin, args, timeout_s)

    def monotonic(self):
        return (self._monotonic or time.monotonic)()

    def sleep(self, s):
        return (self._sleep or time.sleep)(s)

    @property
    def env(self):
        return self._env if self._env is not None else types.MappingProxyType(os.environ)

    def locate(self, name, probe_dirs=None):
        return (self._locate or find_tool)(name, probe_dirs)

    def http_open(self, req, timeout, proxies):
        return (self._http_open or webhook_open)(req, timeout, proxies)

    def run_cmd(self, argv, timeout_s):
        return (self._run_cmd or run_cmd)(argv, timeout_s)

    def extract(self, args, turn_path):
        return (self._extract or extract)(self.pi_bin, args, turn_path)


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


def main(argv=None, *, deps=None):
    args = parse_args(argv)
    deps = deps if deps is not None else Deps()
    herdr = Herdr(deps)
    ledger = None
    # 台账已开＝首版台账成功落盘（ADR-0024）：io_failed 两态 hints 的机械判据。
    # 首写失败时 ledger dict 虽在内存，文件未落盘、duplicate 闸不拦，id 未消耗。
    ledger_opened = False
    ledger_path = None
    result_path = None
    notify_path = None
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

    def error_exit(e):
        """统一 error 出口（DispatchError 与顶层 io_failed 兜底共用，ADR-0024）：
        尽力写终态台账（失败吞）、部分状态机械带出、emit_result 既有降级交付。"""
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
                  "space_created", "pane_adopted", "gift_cleanup_warning",
                  "reset_skipped", "display_name", "display_name_warning",
                  "space", "space_selected", "space_landed", "space_target_warning",
                  "space_restore", "space_restore_warning",
                  "next_skill_feedback", "learning_warning"):
            if k in ctx:
                out[k] = ctx[k]
        if started is not None:
            out["error"]["elapsed_s"] = round(deps.monotonic() - started, 1)
        if ledger is not None and ledger.get("run_record"):
            out["error"]["run_record"] = ledger["run_record"]
        # 完整性标记（ADR-0019）：回读后发生的错误（如抽取失败）不丢折叠标记。
        if ledger is not None and "complete" in ledger:
            out["complete"] = ledger["complete"]
            out["complete_reason"] = ledger["complete_reason"]
        emit_result(deps, out, result_path, notify_path)
        return e.returncode

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
        # --space 机械校验（#43 grill 定稿）：必传（argparse required）；空/纯空白 →
        # bad_args；路径形态（/ 或 ~ 开头）须是已存在目录，否则 bad_args（退出码 2、
        # 零发送、id 不消耗）。名/id/路径匹配是数据问题，领养时对着 ls 经 parse_space
        # 三态解析（#70 归一）。
        ctx["space"] = args.space   # 请求值回显（传了就带，成功与 error 都带，同 target_model 哲学）
        if not args.space.strip():
            raise DispatchError("bad_args", "--space 非法（须非空）",
                                ["传目标空间名/id/路径"], returncode=2)
        if args.space.startswith(("/", "~")):
            if not Path(args.space).expanduser().is_dir():
                raise DispatchError("bad_args",
                                    f"--space 路径不是已存在目录：{args.space}",
                                    ["传已存在的目录路径，或改传空间名/id"], returncode=2)
        check_structure_and_tools()
        # 抽取后端启动预检（ADR-0005）：api 缺配置立刻报错，不跑完一轮才发现；
        # pi 备选才需要 find_tool(pi)，api 路径不依赖 node/pi。
        backend = resolve_extract_backend(deps.env)
        if backend == "api":
            api_config(deps.env)
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
        # raw_path = 回读原料（全文逐字，审计，含被断轮切掉的旧轮）；
        # turn_path = 本轮原料（断轮段，抽取器输入＋逐字校验语料，ADR-0013）；
        # record_path = 抽取器裁剪后的干净全文（回看对象）。
        raw_path = runs / f"{args.release_id}.raw.md"
        turn_path = runs / f"{args.release_id}.turn.md"
        record_path = runs / f"{args.release_id}.md"
        ledger_path = runs / f"{args.release_id}.json"
        # 结果文件是最终交付（成功与 error 都写），台账记过程；
        # 重发拒绝仍只看台账/回读原料落盘，结果文件存在不单独拦截。
        result_path = runs / f"{args.release_id}.result.json"
        # 通知记录（ADR-0014）：与结果文件同目录、同放行 id，逐字记录实际 POST 正文。
        # 放行 id 已在上方校验合法，此处构造路径安全；早期 error 到此之前 notify_path 仍 None。
        notify_path = runs / f"{args.release_id}.notify.json"
        if ledger_path.exists() or raw_path.exists():
            raise DispatchError(
                "duplicate_release",
                f"放行 id {args.release_id} 已有台账或原文落盘，一个放行只跑一轮",
                ["查看台账确认上次结果；要重发须经用户重新放行、用新 id"],
            )
        # 1.1) 静态形态闸（ADR-0017，#61）：会话起始 skill 命令行必须单独单行一轮。
        #      duplicate_release 之后、工具查找/hc get/领养之前：纯文本判定，零发送
        #      零副作用、放行 id 不消耗（退出码 2）。不分 agent kind、与 --reset 独立、
        #      新鲜路径不豁免；与 reset_required 双命中先报本错。体积按含尾换行的
        #      归一化全文计（忠实于实际发送物），内部换行＝剥掉尾部换行后仍含换行。
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
        deps.pi_bin = deps.locate("pi") if backend == "pi" else None
        if not deps.hc_bin:
            raise DispatchError("missing_tool", "PATH 与常见目录里都找不到 hc", [])
        if backend == "pi" and not deps.pi_bin:
            raise DispatchError("missing_tool", "PATH 与常见目录里都找不到 pi",
                                ["某些机器非交互 shell 的 PATH 里没有 node；"
                                 "脚本已探 ~/.local/bin、homebrew、nvm node bin 仍未果"])

        # 1) 预检分流（ADR-0009 / ADR-0020 决定 4）：hc get 一律作分流判据（不用
        #    hc ls 的 agent 派生字段）；纯函数 classify 出五路、gate_route 依次报
        #    占位四后缀文案→launch_required→display_name_required→非法会话名→
        #    rebuild_busy。not_found/finished→创建（finished 同名重建、旧条目按 t 号
        #    留审计）；live 按标签/身份分流；occupied→rebuild 升格与 --rebuild 误传
        #    校验同在 classify 内。
        try:
            g = herdr.session(args.target)
        except NotFound:
            g = None
        if g is not None and g.state == "live" and g.address:
            ctx["target_address"] = g.address
        route = classify(g, args)
        gate_route(route, g, args)
        branch = route.value
        fresh = route.is_fresh
        if route.is_fresh:
            if branch == "revive":
                # 复活同样过预检 pane 闸（US26：app 没开时所有轮次停摆，无隐性例外）；
                # 启动机械本身不 run、不重写标签（Decision 5）。
                adopt(deps, herdr, ctx["target_address"],
                      None, None, ctx, True, args.space,
                      adopt_timeout=args.adopt_timeout, adopt_poll=args.adopt_poll)
        else:
            # 身份已在分流核过（identity_ok）。恒可见（ADR-0009）：既有路径每轮预检
            # （写台账前）核 pane，缺则补领养（park 等造成的可见性漂移自愈）；补不成→
            # adopt_failed，零发送、id 不消耗、同 id 重试。app 没开时所有轮次停摆（已接受的代价）。
            adopt(deps, herdr, g.address or args.target,
                  None, None, ctx, True, args.space,
                  adopt_timeout=args.adopt_timeout, adopt_poll=args.adopt_poll)
            state = g.status_state
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
        #      静态形态闸 split_required（ADR-0017）已在工具查找前先检；双命中先报
        #      split，本闸只管重置口径。
        if args.reset == "none" and not fresh:
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

        # 2) 发送前再核一次文件未变，先记账后发送。
        # 复核读失败映射既有 prompt_unreadable（ADR-0024，#111）：提示词中途消失
        # 保持既有语义，不新增同义 kind、不落 io_failed 兜底。
        try:
            recheck = prompt_path.read_bytes()
        except OSError as e:
            raise DispatchError("prompt_unreadable", f"读不到提示词文件：{e}", [])
        if hashlib.sha256(recheck).hexdigest() != sha:
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
            "space": args.space,
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
        ledger_opened = True   # 首版台账成功落盘＝放行 id 消耗（ADR-0024 两态 hints 判据）

        # 2.4) 创建前置（ADR-0009）：not_found/finished 分流且带 --launch 时，同一放行内
        #      建命名会话（单形态 shell 包装）→打标签→等 shell→send 启动命令→领养（恒可见）
        #      →等 agent 就绪→核身份（#46 Bug3：领养先于等就绪——卡登录/授权/网络弹框时
        #      会话也已可见，用户能到屏幕前手动处理；就绪失败另报状态，不影响可见性）。
        #      放行一并授权创建＋主任务；启动命令不抽取、不落 run_record。
        #      领养在写台账后：失败保留已建终端、放行 id 消耗（新 id 重试走既有路径修复）。
        if branch in ("create", "rebuild"):
            if branch == "rebuild":
                # 强制重建（ADR-0009，用户显式授权）：只杀 idle/done 占位者（预检已核），
                # kill 后同名新建；台账/result 记 rebuilt＋旧 t 号，kill 后创建失败也能
                # 审计「旧会话已死、新会话未成」。
                ledger.update(phase="rebuild_killing")
                write_json_atomic(ledger_path, ledger)
                try:
                    herdr.kill(args.target)
                except HerdrFailure as e:
                    if e.kind in _HC_PASSTHROUGH_KINDS:
                        raise
                    raise DispatchError("launch_failed", f"重建 kill 占位者失败：{e.message}",
                                        e.hints + ["占位者未死；本轮停止，经用户放行后另起一轮"])
                ctx["rebuilt"] = True
                ctx["rebuilt_old_target"] = ctx.get("target_address")
                ledger.update(rebuilt=True, rebuilt_old_target=ctx["rebuilt_old_target"])
                write_json_atomic(ledger_path, ledger)
            do_create(deps, herdr, args, ledger, ledger_path, ctx)
            adopt(deps, herdr, ctx["target_address"],
                  ledger, ledger_path, ctx, False, args.space,
                  adopt_timeout=args.adopt_timeout, adopt_poll=args.adopt_poll)
            wait_launch_agent(deps, herdr, args, ledger, ledger_path)
        elif branch == "revive":
            # 复活（ADR-0009）：不 run、不重写标签；原地重发启动命令→等 agent 就绪→核身份
            # （领养已在预检做过）。名字/cwd/终端级历史不动，会话是空的。
            send_launch_command(herdr, args, ledger, ledger_path, ctx)
            wait_launch_agent(deps, herdr, args, ledger, ledger_path)

        # 2.45) 显示名落地（ADR-0010）：领养复核 pane 落地后、前置/主任务发送前。
        #       创建/重建在领养成功之后、复活在启动命令发出 agent 就绪之后（上面各分支已就绪）；
        #       既有路径仅当传了显示名（复用轮可选，传了就覆盖、不传不碰）。新鲜路径必传（预检已核）。
        #       失败降级 display_name_warning 不丢放行；pane 落地仍是硬闸。
        if args.display_name is not None:
            apply_display_name(deps, herdr, ctx.get("target_address"),
                               args.display_name, ledger, ledger_path, ctx)

        # 2.47) 学习准备（ADR-0012，#48 阶段二）：LOOP_LEARNING=off 时整段跳过，原有
        #       next_skill 预测/抽取/发送照旧。开启时只读把 --space 解析成 space id（不建
        #       空间、不 select）；id 缺失则本轮 space scoped 学习禁用并告警。学习全程建议性，
        #       任何存储/锁/I/O 故障降级 learning_warning，不阻断主轮。
        learning_on = learning.enabled()
        learning_store = None
        learning_space_id = None
        if learning_on:
            learning_store = learning.LearningStore()
            herdr.require_app()
            learning_space_id = resolve_space_id_for_learning(herdr, args.space)
            if learning_space_id is None:
                ctx["learning_warning"] = {
                    "reason": "space_id_missing",
                    "detail": "--space 未能只读解析出唯一 space id，本轮序列/案例/few-shot 禁用"}

        # 2.5/2.6) 前置发送（ADR-0020 决定 6）：纯函数 prestep_specs 产出次序固定的
        #      「重置 → 切模型」。重置是同一 target 上的字面命令 /new /clear /compact，
        #      切模型是字面 /model <名字>（名字原样透传、不核实生效）；两者都等落定
        #      （idle/done + 屏幕静置）后再发下一步。放行一并授权前置＋主任务：前置按
        #      枚举校验、不抽取、不落 run_record；任一步发不出即整轮停（台账记部分状态）。
        #      刚创建/复活的新鲜会话自动跳过重置发送（不在空会话上空等一次重置落定），
        #      由纯函数记 reset_skipped:"fresh_launch"；切模型前置照跑（ADR-0006 纪律平移）。
        plan = prestep_specs(args, route)
        if plan.reset_skipped is not None:
            ctx["reset_skipped"] = plan.reset_skipped
            ledger.update(reset_skipped=plan.reset_skipped)
            write_json_atomic(ledger_path, ledger)
        for spec in plan.specs:
            if spec.kind == "reset":
                ledger.update(phase="reset_sending")
                write_json_atomic(ledger_path, ledger)
                pre_send, pre_resent = do_send(herdr, args, spec.command, ledger, ledger_path)
                reset_sent = True
                ledger.update(phase="reset_waiting", reset_send=pre_send, reset_resent=pre_resent)
                write_json_atomic(ledger_path, ledger)
                wait_for_reset(deps, herdr, args, deps.monotonic() + args.wall_clock)
                ledger.update(phase="reset_done", reset_done_at=now_iso())
                write_json_atomic(ledger_path, ledger)
            else:
                ledger.update(phase="model_sending")
                write_json_atomic(ledger_path, ledger)
                pre_send, pre_resent = do_send(herdr, args, spec.command, ledger, ledger_path)
                model_sent = True
                ledger.update(phase="model_waiting", model_send=pre_send, model_resent=pre_resent)
                write_json_atomic(ledger_path, ledger)
                wait_for_reset(deps, herdr, args, deps.monotonic() + args.wall_clock)
                ledger.update(phase="model_done", model_done_at=now_iso())
                write_json_atomic(ledger_path, ledger)

        # 3) 主任务发送（墙钟从主任务发送成功起计，不重置）。
        started = deps.monotonic()
        send, resent = do_send(herdr, args, text, ledger, ledger_path)
        baseline = send["turn"]
        ledger.update(phase="sent_waiting", sent_at=now_iso(), send=send, resent=resent)
        write_json_atomic(ledger_path, ledger)

        # 3.5) 学习结算（ADR-0012）：主任务发送成功后立即记近期序列＋结算上一轮同 target
        #      预测。发送前预检失败不会到这里（不消耗待观察）；后续抽取失败仍保留反馈并附在
        #      error JSON。ground truth＝主任务首个非空行（nonempty[0]），重置/切模型前置不算。
        if learning_on:
            lr = learning_store.settle_and_record(args.target, args.release_id,
                                                  learning_space_id, nonempty[0])
            if lr.get("feedback") is not None:
                ctx["next_skill_feedback"] = lr["feedback"]
            if lr.get("warning") is not None:
                ctx["learning_warning"] = lr["warning"]

        # 4) 等待落定（墙钟从主任务发送成功起计）。
        deadline = started + args.wall_clock
        final = wait_for_result(deps, herdr, args, baseline, deadline)

        # 5) 回读（最近 1000 行拼接历史＋当前屏，ADR-0013）＋断轮＋完整性检查。
        read = herdr.readback(args.target)
        screen = read.text
        # 锚点（提示词回声首行前缀）只作信息、不阻断：本步已在 turn>基线＋落定之后，
        # 本轮确已被处理；超过 1000 行历史时回声仍可能不可得，锚点搜不到不代表
        # 回复不完整（PAW 实测多次误判后降级，ADR-0013 维持信息字段）。
        anchor_found = verbatim_present(anchor, screen)
        # 断轮（ADR-0013）：确定性机械切出本轮段；逐字校验只对本轮段。
        turn_text, echo_found, cut_line = cut_turn(screen, anchor)
        # 折叠判据（ADR-0019，#79）：只认实证形态、回声行排除；命中只标记不阻断——
        # 启发式误报代价（丢整轮）远大于漏检代价（result 带 complete:false 供人复核）。
        reasons = ["fold_markers"] if fold_hits(turn_text, text) else []
        complete = not reasons
        elapsed = round(deps.monotonic() - started, 1)

        # 6) 双文件逐字落盘（不加任何头尾）：回读全文（审计，含旧轮）＋本轮段
        #    （抽取器输入＋校验语料）；台账记读取元信息与断轮留痕。
        raw_path.write_text(screen, encoding="utf-8", newline="")
        turn_path.write_text(turn_text, encoding="utf-8", newline="")
        ledger.update(
            phase="read", read_at=now_iso(),
            turn=final.turn, status=final.status_state,
            elapsed_s=elapsed, chars=len(screen),
            echo_found=echo_found, turn_cut_line=cut_line, turn_chars=len(turn_text),
            hc_read={"complete": read.raw.get("complete"), "how": read.raw.get("how"),
                     "why": read.raw.get("why")},
            complete=complete, complete_reason=reasons, anchor_found=anchor_found,
            run_record=str(raw_path), raw_record=str(raw_path), turn_record=str(turn_path),
        )
        write_json_atomic(ledger_path, ledger)

        # 7) 裁剪＋抽取（默认 API 直连，pi 备选）＋逐字校验（裁定 C），合并输出。
        ledger.update(phase="extracting")
        write_json_atomic(ledger_path, ledger)
        # 7.0) 在线 few-shot（ADR-0012）：抽取前选 ≤4 条同 space 案例＋当前近期序列注入既有
        #      抽取调用（不新增模型请求）。锁在此事务内即释放，绝不跨下面的 API/pi 调用。
        #      无历史时注入为空、完全沿用原 prompt（冷启动行为不变）。
        examples_used = 0
        sequence_used = False
        seq_snapshot = []
        if learning_on:
            inj = learning_store.build_injection(learning_space_id, args.target)
            args.learning_injection = inj.get("text", "")
            examples_used = inj.get("examples_used", 0)
            sequence_used = inj.get("sequence_used", False)
            seq_snapshot = inj.get("sequence", [])
            if inj.get("warning") is not None:
                ctx["learning_warning"] = inj["warning"]
        ext = deps.extract(args, turn_path)
        reply = ext.pop("reply")
        # 逐字校验语料＝本轮原料（ADR-0013）：旧轮文本不参与通过判定。
        rewritten = verify_extraction(ext, turn_text, turn_path, raw_path, backend)
        bad_reply_lines = verify_reply(reply, turn_text)
        if bad_reply_lines:
            rewritten["reply"] = closest_fragment(bad_reply_lines[0], turn_text)
        # 干净全文落盘为回看对象；回读全文与本轮原料另存审计。
        record_path.write_text(reply, encoding="utf-8", newline="")
        ledger.update(rewritten=rewritten, run_record=str(record_path),
                      reply_chars=len(reply), reply_bad_lines=len(bad_reply_lines))
        # 7.5) 创建待观察预测（ADR-0012）：每轮成功抽取都创建（含空预测），供下一轮同 target
        #      结算。以预测轮放行 id 去重；案例正文绝不进 result/webhook（只存本地）。
        #      折叠标记轮照创建（complete 旗标随 pending），结算时案例标 excluded（ADR-0019）。
        if learning_on:
            pr = learning_store.record_prediction(
                args.target, args.release_id, learning_space_id,
                ext.get("next_skill", ""), getattr(args, "learning_next_skill_invalid", False),
                reply, seq_snapshot, examples_used, sequence_used, complete=complete)
            if pr.get("warning") is not None:
                ctx["learning_warning"] = pr["warning"]
        result = {
            "ok": True,
            "phase": "done",
            "release_id": args.release_id,
            "task_name": task_name,
            "target": original_target,
            "reset": args.reset,
            "turn": final.turn,
            "status": final.status_state,
            "complete": complete,
            "complete_reason": reasons,
            "anchor_found": anchor_found,
            "elapsed_s": elapsed,
            "chars": len(screen),
            "run_record": str(record_path),
            "raw_record": str(raw_path),
            "turn_record": str(turn_path),
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
                  "workspace", "space_created", "pane_adopted", "gift_cleanup_warning",
                  "reset_skipped", "display_name", "display_name_warning",
                  "space", "space_selected", "space_landed", "space_target_warning",
                  "space_restore", "space_restore_warning",
                  "next_skill_feedback", "learning_warning"):
            if k in ctx:
                result[k] = ctx[k]
        ledger.update(phase="done", done_at=now_iso())
        write_json_atomic(ledger_path, ledger)
        emit_result(deps, result, result_path, notify_path)
        return 0
    except DispatchError as e:
        return error_exit(e)
    except OSError as e:
        # 中途本地 IO 失败顶层兜底（ADR-0024，#111）：整类 OSError（runs 创建、
        # 台账 open＋中途写、回读原料/本轮原料/干净回复落盘、api 读本轮原料等）
        # 映射 io_failed，走同一 error 出口；不重试、不缓冲、不造第二套交付通道。
        # 有现成语义的读取点在各自位置包既有 kind（prompt_unreadable/missing_prompt），
        # 不到这里。duplicate_release 仍是第一闸，本兜底绝不抢占。
        return error_exit(io_failed_error(e, ledger_opened))


if __name__ == "__main__":
    sys.exit(main())
