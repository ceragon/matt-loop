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
全文 reply 落盘 .md＋抽取选段字段（三档规则链 api→pi→caller，ADR-0005/0026；
caller 档跳过抽取、通知内联 turn_text）→ 合并成
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
  - 工具定位：hc（任何后端都必需；backend=pi 时还有 pi）先 shutil.which，PATH 找不到时自动探常见目录
    （~/.local/bin、homebrew、nvm node bin），后台 job 缺 PATH 也能起。uv 不每轮要求，只在解释器
    <3.10 的自举路径里探（探不到报 python_too_old；#129/#130 的判断——ADR-0005 决定 7
    只授权延后定位 pi）。
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
  - 抽取后端（ADR-0005，ADR-0026 扩三档）：LOOP_EXTRACT_BACKEND=api|pi|caller，
    受理时按规则链判一次：显式 env 恒赢；未设但任一 LOOP_API_* 已设＝api 意图
    （三个不齐报 api_not_configured）；三个全未设探 pi、探不到落 caller。
    api 走 OpenAI 兼容 chat completions（LOOP_API_BASE/KEY/MODEL 全必填无默认）；
    temp 0、max_tokens 默认 32768（LOOP_API_MAX_TOKENS 可覆盖）、超时复用
    --extract-timeout、不重试不流式；代理与 webhook 同源（ADR-0003）；失败只记
    状态码/类名，不读响应体，密钥/URL 零回显。pi 备选路径照旧。轮中不级联降档。
    caller 档跳过抽取与逐字校验：机械步照旧，选段空值＝尚未识别，run_record 指
    本轮原料，通知内联 turn_text（执行者回复首行起的正文，回声剥离是确定性机械步；
    异常带 turn_text_warning：whole_segment/echo_degraded），不产学习案例。

交付（双产物，ADR-0014，#52 阶段一）：stdout 一行 JSON，同一字符串同时写
$TMPDIR/loop/runs/<放行id>.result.json（全量结果，成功与 error 都写，审计与回看对象）；
另写 <放行id>.notify.json（通知投影＋通知记录，逐字记录实际 POST 正文）。成功通知
恒带 7 字段（ok/release_id/target/needs_user/elapsed_s/complete/extract_backend）＋
有内容才带 6 字段（questions/blocked/conclusion/next_skill/rewritten/complete_reason，
缺席即空）；caller 档另带 turn_text＋条件 turn_text_warning；run_record 不进通知
（ADR-0026 瘦身）。error 通知保留发送前完整对象。调用方
（DS Bot/Grok Bot）统一先读 notify.json、需要完整诊断/审计/回看再读 result.json。
run_record 指抽取器裁剪后的干净全文 .md（回看对象；caller 档尚无干净全文，指本轮
原料 .turn.md），raw_record 指回读全文 .raw.md
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
import errno
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
from pathlib import Path

# 版本门必须先于任何兄弟模块 import（#135）：兄弟模块用 3.10+ 语法（如 `str | None`），
# 在 <3.10 的解释器上 import 会先 SyntaxError，永远走不到门里。下面三个函数因此只用
# 标准库、并在兄弟 import 之前定义。

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

# learning.py 与本脚本同目录；无论 dispatch 被直跑还是经 importlib 加载（部分测试不加
# sys.path），都先把脚本目录挂上，保证兄弟模块可导入。
sys.path.insert(0, str(Path(__file__).resolve().parent))
import learning  # noqa: E402
# 兄弟模块（部署单元是 loop/scripts/ 整目录）。依赖方向 dispatch → pipeline →
# {herdr, turn, learning, errors}，无环；pipeline 不 import 本脚本。
import pipeline  # noqa: E402
from errors import DispatchError  # noqa: E402,F401
from herdr import Herdr, HerdrFailure, NotFound  # noqa: E402,F401  （供测试重导出）
from pipeline import (  # noqa: E402,F401  （兼容重导出：既有测试零改动，同 turn 先例）
    ADOPT_POLL_S, ADOPT_TIMEOUT_S, Prestep, PrestepPlan, Route, SpacePlan,
    check_identity, classify, gate_route, has_internal_newline, identity_ok,
    parse_space, plan_space, prestep_specs,
    RESERVED_ID_RE, SESSION_NAME_RE,
)
from turn import (  # noqa: E402,F401
    FOLD_COUNT_RE, FOLD_HINT, SESSION_START_RE,
    closest_fragment, cut_turn, find_session_start_trigger, fold_hits,
    generate_short_task_name, normalize_paste, strip_echo, utf16_units,
    verbatim_present, verify_extraction, verify_reply,
)

# 成功通知投影白名单（ADR-0014，ADR-0026 决定 4 瘦身）：恒带 7 字段＋有内容才带
# 6 字段（缺席即空），顺序即下列顺序；caller 档另带 turn_text（恒有内容）＋条件
# turn_text_warning（仅异常时）。run_record 整个移出通知，要路径读 result.json。
# error 通知不套白名单、保留发送前完整对象。webhook/webhook_error/
# webhook_error_detail 是 POST 后才产生的发送诊断，只归全量结果，绝不进投影。
NOTIFY_ALWAYS_FIELDS = (
    "ok", "release_id", "target", "needs_user", "elapsed_s", "complete",
    "extract_backend",
)
NOTIFY_CONTENT_FIELDS = (
    "questions", "blocked", "conclusion", "next_skill", "rewritten",
    "complete_reason",
)
# 全部候选字段（恒带＋条件）；投影时条件字段空值缺席，见 notify_projection。
NOTIFY_SUCCESS_FIELDS = NOTIFY_ALWAYS_FIELDS + NOTIFY_CONTENT_FIELDS

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
# 顺序即抽取指令里的字段列举顺序（#133 R6：指令由本常量派生，见 _EXTRACT_INSTRUCTION）；
# 结果载荷的字段序不受影响——reply 被单独取出后，其余五字段的相对顺序不变。
EXTRACT_FIELDS = ("needs_user", "reply", "questions", "conclusion", "blocked", "next_skill")
# 抽取后端（ADR-0005，ADR-0026 扩三档）：api 直连（OpenAI 兼容 chat completions）、
# pi 显式备选、caller 调用方自抽兜底。API 参数全走 env、全必填、无内置默认；
# 密钥与 URL 零回显。
EXTRACT_BACKENDS = ("api", "pi", "caller")
API_MAX_TOKENS_DEFAULT = 32768
# api 意图判据（ADR-0026 决定 1②）：任一已设即视为想走 api，配置不齐大声失败。
API_ENV_NAMES = ("LOOP_API_BASE", "LOOP_API_KEY", "LOOP_API_MODEL")


def resolve_extract_backend(env=None, locate=None):
    """三档规则链（ADR-0026 决定 1），受理时判一次：

    ① LOOP_EXTRACT_BACKEND 显式设了 → 照办（api|pi|caller，非法值退出码 2）；
    ② 未设但任一 LOOP_API_* 已设＝api 意图 → api（配置齐不齐由 api_config 预检）；
    ③ 三个全未设 → locate("pi") 探到 → pi；④ 探不到 → caller（零配置地板）。
    轮中不级联：api/pi 失败照旧 extraction_failed/tool_timeout，不静默换道。
    locate 是工具定位函数（Deps.locate 注入）；不传走模块级 find_tool。
    """
    _env = env if env is not None else os.environ
    v = (_env.get("LOOP_EXTRACT_BACKEND") or "").strip().lower()
    if v:
        if v not in EXTRACT_BACKENDS:
            raise DispatchError("bad_extract_backend",
                                "LOOP_EXTRACT_BACKEND 值非法（只认 api|pi|caller）", [], returncode=2)
        return v
    if any((_env.get(n) or "").strip() for n in API_ENV_NAMES):
        return "api"
    if (locate or find_tool)("pi"):
        return "pi"
    return "caller"


def api_config(env=None):
    """读 API 三个 env＋可选 max_tokens；缺必填项报 api_not_configured
    （只列变量名，不带值），max_tokens 非法报 bad_api_max_tokens。启动预检用。
    ADR-0026 收窄：只在 api 意图明确（显式选 api 或任一变量已设）时调用；
    纯未配置由规则链下探 pi/caller，不到这里。"""
    _env = env if env is not None else os.environ
    vals = {n: (_env.get(n) or "").strip() for n in API_ENV_NAMES}
    missing = [n for n in API_ENV_NAMES if not vals[n]]
    if missing:
        raise DispatchError(
            "api_not_configured",
            "抽取后端=api 但 env 缺失：" + ", ".join(missing),
            ["配齐三个变量（见 webhook.env.example 抽取配置节）后重试",
             "或设 LOOP_EXTRACT_BACKEND=pi/caller 显式换档；清空 LOOP_API_* 则规则链自动下探"],
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


def hc_json(hc_bin, args, timeout_s, run=None):
    """跑一条 hc 命令并解析 --json 输出；非零退出抛 HerdrFailure。

    run 是命令执行器（Deps.run_cmd 注入）；不传走模块级 run_cmd。
    """
    # --json 必须紧跟子命令：send 用 `--` 保护以 - 开头的提示词文本，
    # `--` 之后的一切都被 clap 当位置参数，选项放后面会被吞掉。
    p = (run or run_cmd)([hc_bin, args[0], "--json", *args[1:]], timeout_s)
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


def app_json(app_bin, args, timeout_s, run=None):
    """跑一条 herdr-app 命令并解析 JSON 输出；非零退出抛 HerdrFailure。
    所有 herdr-app 调用经此单一咽喉（与 hc_json 同为可注入桩点）。
    herdr-app 的 ls/status/do 原生输出 JSON，不插 --json；do 的错误形状是
    {"error": "..."}＋退出码 1。绝不用裸 herdr-app 作探针（它会打开窗口）。
    run 是命令执行器（Deps.run_cmd 注入）；不传走模块级 run_cmd。"""
    p = (run or run_cmd)([app_bin, *args], timeout_s)
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


def _proxy_diagnostic(url, resolve=None):
    """与 webhook_proxy_resolution 同源：只报是否配置、是否实际命中、来源
    （env/system/bypass/none/unknown），不输出代理地址、端口、用户名或密码。
    resolve 是代理决议函数（Deps.proxy_resolution 注入）；不传走模块级默认。"""
    try:
        proxies, source = (resolve or webhook_proxy_resolution)(url)
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


def webhook_error_detail(exc, url, resolve=None):
    """把 webhook 失败整理成脱敏诊断对象。只收类名、数字、布尔与枚举字符串：
    不读 str(e)/repr(e)/响应体，不记录字符串 reason（可能带主机/地址）、
    URL、密钥或请求头。未知项直接省略，代理状态未知标 "unknown"。
    resolve 是代理决议函数（Deps.proxy_resolution 注入）；不传走模块级默认。"""
    reason = getattr(exc, "reason", None)
    detail = {
        "type": type(exc).__name__,
        "is_timeout": isinstance(exc, TimeoutError) or isinstance(reason, TimeoutError),
        "proxy": _proxy_diagnostic(url, resolve),
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
    """通知投影（ADR-0014，ADR-0026 决定 4）：从完整结果选出唤醒载荷。
    成功（ok=True）恒带 NOTIFY_ALWAYS_FIELDS 7 字段（结果缺某字段则不带，不补造）；
    NOTIFY_CONTENT_FIELDS 6 字段有内容才带（空 []/""/{} 缺席，缺席即空）；
    caller 档另带 turn_text（恒有内容）＋条件 turn_text_warning（仅异常时携带）。
    run_record 不进通知。error 不套白名单，原样返回发送前完整对象（含
    kind/message/hints 与全部部分状态）。投影在 POST 前构造，故不含 webhook/
    webhook_error/webhook_error_detail 等发送后才产生的诊断——那些只归全量结果。"""
    if not result.get("ok"):
        return result
    out = {k: result[k] for k in NOTIFY_ALWAYS_FIELDS if k in result}
    for k in NOTIFY_CONTENT_FIELDS:
        if k in result and result[k]:
            out[k] = result[k]
    if result.get("extract_backend") == "caller":
        if "turn_text" in result:
            out["turn_text"] = result["turn_text"]
        if result.get("turn_text_warning"):
            out["turn_text_warning"] = result["turn_text_warning"]
    return out


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
    proxies, _source = deps.proxy_resolution(url)
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
    detail = webhook_error_detail(primary_exc, url, deps.proxy_resolution)
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


def _extraction(fields, meta):
    """parse_extraction 的六字段 → 管线 Extraction（ADR-0020 决定 13）。

    reply 单列；其余五字段按原序进 fields；next_skill_invalid 来自 parse_extraction
    的 meta（取代 args 暗渡）。
    """
    reply = fields["reply"]
    rest = {k: v for k, v in fields.items() if k != "reply"}
    return pipeline.Extraction(fields=rest, reply=reply,
                               next_skill_invalid=meta.get("next_skill_invalid", False))


ECHO_REFERENCE_LABEL = "【本轮发送的提示词原文】"
ECHO_REFERENCE_NOTE = "（供逐字比对，认出回声及其软换行续行）"


def echo_reference(request):
    """回声参照块（#127）：抽取期重读本轮主任务提示词原文，粘贴归一后与实际发送物同源。

    发送前 open_ledger 已按受理阶段的 sha 复核钉住同一性（中途变文件报 prompt_changed
    拒发）；这里是抽取期重读，读失败映射既有 prompt_unreadable（ADR-0024 决定 2 的
    「提示词读取」先例），不新增 error kind。参照只供认出回声及其软换行续行，不是
    逐字校验语料（ADR-0013 补记）；api 消息里本轮原料仍居末尾，pi 侧原料是 @文件附件，
    仍是唯一校验语料。"""
    try:
        text = Path(request.prompt_file).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise DispatchError("prompt_unreadable", f"读不到提示词文件：{e}", [])
    return ECHO_REFERENCE_LABEL + ECHO_REFERENCE_NOTE + "\n" + normalize_paste(text)


# 字段列举由 EXTRACT_FIELDS 派生（#133 R6），改一处不会漏另一处；字面与旧版逐字一致。
_EXTRACT_INSTRUCTION = (
    "按系统提示词裁剪并抽取，只输出一个 JSON 对象，"
    f"含 {'、'.join(EXTRACT_FIELDS)} 六个字段。"
)


def _extract_prompt_path():
    """抽取提示词路径；缺失映射既有 missing_prompt（ADR-0024）。两后端共用。"""
    extract_md = Path(__file__).resolve().parent.parent / "prompts" / "extract.md"
    if not extract_md.is_file():
        raise DispatchError("missing_prompt", f"找不到抽取提示词：{extract_md}", [])
    return extract_md


def _extract_sections(request, injection, delivery_note):
    """两后端共用的消息段：投递说法＋指令→few-shot 注入（如有）→回声参照（#127）。

    delivery_note 是本轮原料的投递说法：api 内联在末尾、pi 走 @文件附件（ADR-0013）；
    原料正文本身不进返回值。
    """
    sections = [delivery_note + _EXTRACT_INSTRUCTION]
    if injection:
        sections.append(injection)
    sections.append(echo_reference(request))
    return sections


def _extraction_from_output(output):
    """抽取器原始输出 → 六字段＋Extraction（两后端共用尾段）。"""
    meta = {}
    ext = parse_extraction(output, meta)
    return _extraction(ext, meta)


def extract_via_pi(deps, request, turn_path, injection):
    extract_md = _extract_prompt_path()
    # 在线 few-shot（ADR-0012）与回声参照（#127）同 api 侧共用；原料只走 @文件附件。
    instruction = "\n\n".join(_extract_sections(
        request, injection, "本轮原料已随消息附上（@文件）。"))
    instruction += f'\n本轮原料落盘路径：{turn_path}'
    argv = [
        deps.pi_bin, "-p",
        "--no-tools", "--no-session", "--no-extensions", "--no-skills",
        "--no-prompt-templates", "--no-context-files", "--no-mcp",
        "--append-system-prompt", str(extract_md),
    ]
    if request.pi_model:
        argv += ["--model", request.pi_model]
    argv += ["--", f"@{turn_path}", instruction]
    p = deps.run_cmd(argv, request.timing.extract_timeout)
    if p.returncode != 0:
        raise DispatchError(
            "extraction_failed",
            f"pi 退出码 {p.returncode}",
            ["原文已落盘，可人工重做抽取", (p.stderr or p.stdout).strip()[:300]],
        )
    # 研究表明 pi 模型失败时退出码可能仍为 0，所以无论退出码都要校验输出。
    return _extraction_from_output(p.stdout)


def extract_via_api(deps, request, turn_path, injection):
    """直连 API 裁剪＋抽取（ADR-0005）：OpenAI 兼容 chat completions，
    system=extract.md，user=指令＋内联本轮原料（ADR-0013）。temp 0、不流式、不重试、
    不发 response_format；超时复用 --extract-timeout。代理决议与出口和
    webhook 同一套（ADR-0003）。失败只记状态码/类名，不读响应体，密钥/URL 零回显。"""
    base, key, model, max_tokens = api_config(deps.env)
    extract_md = _extract_prompt_path()
    # 本轮原料读失败不包：归 io_failed 顶层兜底（ADR-0024，#111）。
    turn_text = Path(turn_path).read_text(encoding="utf-8")
    # 抽取提示词读失败映射既有 missing_prompt（ADR-0024，#111）：is_file 检查后
    # 中途读失败（权限/磁盘故障）保持既有语义，不新增同义 kind。
    try:
        system_prompt = extract_md.read_text(encoding="utf-8")
    except OSError as e:
        raise DispatchError("missing_prompt", f"读不到抽取提示词：{extract_md}：{e}", [])
    # 在线 few-shot（ADR-0012）与回声参照（#127）都插在指令与本轮原料之间，
    # 本轮原料仍居消息末尾；无历史时为空、沿用原 prompt（冷启动行为不变）。
    sections = _extract_sections(
        request, injection, "本轮原料已内联在本消息末尾。")
    sections.append(turn_text)
    user_content = "\n\n".join(sections)
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
    proxies, _ = deps.proxy_resolution(url)
    try:
        with deps.http_open(req, request.timing.extract_timeout, proxies) as r:
            payload = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise DispatchError("extraction_failed", f"API HTTP {e.code}",
                            ["原文已落盘，可人工重做抽取"])
    except Exception as e:
        reason = getattr(e, "reason", None)
        if isinstance(e, TimeoutError) or isinstance(reason, TimeoutError):
            raise DispatchError("tool_timeout",
                                f"API 抽取超护栏 {request.timing.extract_timeout} 秒",
                                ["原文已落盘，可人工重做抽取"])
        raise DispatchError("extraction_failed", f"API {type(e).__name__}",
                            ["原文已落盘，可人工重做抽取"])
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise DispatchError("extraction_invalid",
                            "API 响应缺 choices[0].message.content",
                            ["原文已落盘，可人工重做抽取"])
    return _extraction_from_output(content)


def extract(deps, request, turn_path, injection):
    """抽取入口：按三档规则链分叉（ADR-0026；api 直连，pi 备选）。
    两种后端都读本轮原料（ADR-0013），不再读含旧轮的回读全文。
    caller 档在管线里跳过抽取（caller_inline），不会到这里；规则链是 env＋locate
    的确定性函数，此处分叉与受理定档同值，轮中不级联。
    签名 `(deps, request, turn_path, injection)`：与 Deps.extract 的可注入面同形。"""
    if resolve_extract_backend(deps.env, deps.locate) == "api":
        return extract_via_api(deps, request, turn_path, injection)
    return extract_via_pi(deps, request, turn_path, injection)


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
    """外部世界的注入面（ADR-0016 决定 3；P2 收口残留洞）：hc_json / app_json 命令层、
    monotonic + sleep 时钟、只读 env、locate、http_open、run_cmd、proxy_resolution、
    extract、抽取后端预检。默认实现在**调用时**才解析模块级函数（不在构造时绑定）。
    hc_bin / app_bin / pi_bin 由管线在原有解析点填入，调用链不再逐层透传。
    管线（pipeline.py）不 import 本脚本，只按鸭子类型读这些方法。"""

    def __init__(self, *, hc_json=None, app_json=None, monotonic=None, sleep=None,
                 env=None, locate=None, http_open=None, run_cmd=None, extract=None,
                 proxy_resolution=None, now=None):
        self._hc_json = hc_json
        self._app_json = app_json
        self._monotonic = monotonic
        self._sleep = sleep
        self._env = env
        self._locate = locate
        self._http_open = http_open
        self._run_cmd = run_cmd
        self._extract = extract
        self._proxy_resolution = proxy_resolution
        self._now = now
        self.hc_bin = None
        self.app_bin = None
        self.pi_bin = None

    def hc_json(self, args, timeout_s):
        if self._hc_json is not None:
            return self._hc_json(self.hc_bin, args, timeout_s)
        return hc_json(self.hc_bin, args, timeout_s, self.run_cmd)

    def app_json(self, args, timeout_s):
        if self._app_json is not None:
            return self._app_json(self.app_bin, args, timeout_s)
        return app_json(self.app_bin, args, timeout_s, self.run_cmd)

    def monotonic(self):
        return (self._monotonic or time.monotonic)()

    def sleep(self, s):
        return (self._sleep or time.sleep)(s)

    def now(self):
        """墙钟（UTC ISO 秒精度）：Journal 构造时取（ADR-0022 决定 7），⑤ 共用。"""
        return (self._now or pipeline.now_iso)()

    @property
    def env(self):
        return self._env if self._env is not None else types.MappingProxyType(os.environ)

    def locate(self, name, probe_dirs=None):
        return (self._locate or find_tool)(name, probe_dirs)

    def http_open(self, req, timeout, proxies):
        return (self._http_open or webhook_open)(req, timeout, proxies)

    def run_cmd(self, argv, timeout_s):
        return (self._run_cmd or run_cmd)(argv, timeout_s)

    def proxy_resolution(self, url):
        return (self._proxy_resolution or webhook_proxy_resolution)(url)

    def resolve_extract_backend(self):
        return resolve_extract_backend(self.env, self.locate)

    def api_config(self):
        return api_config(self.env)

    def extract(self, request, turn_path, injection):
        return (self._extract or extract)(self, request, turn_path, injection)


def request_from_args(args):
    """CLI Namespace → 管线 Request（ADR-0020 决定 1）：argparse 只留在此层。"""
    return pipeline.Request(
        target=args.target,
        release_id=args.release_id,
        prompt_file=args.prompt_file,
        reset=args.reset,
        target_model=args.target_model,
        launch=args.launch,
        cwd=args.cwd,
        display_name=args.display_name,
        space=args.space,
        rebuild=args.rebuild,
        expect_proc=args.expect_proc,
        loop_dir=args.loop_dir,
        pi_model=args.pi_model,
        timing=pipeline.Timing(
            wall_clock=args.wall_clock,
            check_in=args.check_in,
            settle_seconds=args.settle_seconds,
            settle_checks=args.settle_checks,
            adopt_timeout=args.adopt_timeout,
            adopt_poll=args.adopt_poll,
            extract_timeout=args.extract_timeout,
        ),
    )


def main(argv=None, *, deps=None):
    """CLI 薄壳（ADR-0020 决定 1）：parse_args → Request → run → emit_result → 退出码。"""
    args = parse_args(argv)
    deps = deps if deps is not None else Deps()
    outcome = pipeline.run(request_from_args(args), deps)
    emit_result(deps, outcome.payload, outcome.result_path, outcome.notify_path)
    return outcome.returncode


if __name__ == "__main__":
    sys.exit(main())
