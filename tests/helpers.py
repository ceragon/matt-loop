"""helpers.py — 测试目录唯一共享纯助手模块（R，ADR-0021 决定 1/6，#104）。

收 T4 起的重复助手去重债：共享常量单一出处＋逐字重复组（去重前按 AST 归一化核对）。
命名统一：world / argv / ext / api_env 构造器。变体不强并——带场景旋钮的 _world
（turn / status / not_found / question / screen=None 等）与带额外参数的 _argv 留本地。

纯模块纪律：零全局副作用、不直接 import dispatch（经 fake_herdr→harness 的传递导入
属 ADR-0016 决定 9 授权）、不写 os.environ；被棘轮扫描天然干净，不进白名单。
环境一律经 harness env 注入（Deps.env）。

AST 核对备注（实施时实测）：
  - world：×4 组（next_skill/reset/webhook/webhook_diag）与 ×2 组（api_extract/
    no_summary）去 docstring 后函数体逐字相同，收为一个构造器；anchor_path/turn_cut
    的 _world(screen) 经显式 screen 参数吸收（默认 SCREEN，行为不变）。
  - argv：×2 组（anchor_path/no_summary）逐字；api_extract 的 _argv 仅多 *extra，
    经显式 extra 参数吸收。
  - ext：×4 组（reset/reset_discipline/split_required/target_model）函数体逐字相同，
    但自由变量 EXT 不同（reset 六字段、其余五字段旧形状）——AST 相同不等于语义相同，
    故收为构造器、ext 源经显式参数绑定，各文件本地 `_ext = ext(EXT)` 保住原形状。
  - EXT5：五字段旧形状字面量在 6 文件逐字相同（api_extract/next_skill/learning/
    target_model/reset_discipline/split_required），收为第二常量单一出处，各文件
    别名 EXT 引用。形状本身须保住：next_skill 缺字段用例依赖键缺失（parse_extraction
    对缺失记 invalid=True、显式 "" 记 False）；其余文件的 extract 桩直回 dict，
    五字段/六字段决定 result 形状，换形状即行为变化。
  - api_env：×4 组（api_extract/learning/next_skill/turn_cut）逐字，BASE/KEY/MODEL
    取值各处相同；LOOP_API_MAX_TOKENS 钉空串（T4 密闭先例）原样保留。
  - FakeResp（payload 版）：api_extract 删除清单列名、活用例仍用，迁本模块单一出处
    （next_skill/learning/turn_cut 三份逐字相同）；webhook/notify 的 b"ok" 版是
    另一形状，留本地。
"""
import json
from pathlib import Path

from fake_herdr import FakeHerdr
from harness import pipeline

PROMPT = "请实现某个功能\n第二行说明"
ANCHOR = PROMPT.splitlines()[0][:24]
SCREEN = ANCHOR + "\n已完成该功能。"
# 六字段正典形状（含 next_skill）。
EXT = {"needs_user": False, "questions": [],
       "conclusion": "", "blocked": "", "reply": SCREEN, "next_skill": ""}
# 五字段旧形状（无 next_skill 键）：另一逐字重复组 ×6 的单一出处，见模块 docstring。
EXT5 = {"needs_user": False, "questions": [],
        "conclusion": "", "blocked": "", "reply": SCREEN}
LAUNCH = "claude --dangerously-skip-permissions"
NAME = "claude-matt-loop"
DISPLAY = "LoopEng_测试任务"
KEY = "sk-test-SECRET-VALUE"
BASE = "https://gw.example/v1"
MODEL = "test-model"


def world(screen=SCREEN):
    """既有 idle agent t99＋main 空间 pane 已在（领养提前返回）＋给定屏幕（默认稳定屏幕）。"""
    w = FakeHerdr()
    w.add_terminal(address="t99", name="claude-test", labels={"loop": "1"})
    sp = w.add_space(name="main")
    w.add_tab(sp, terminals=["t99"])
    w.screen("t99", screen)
    return w


def argv(td, rid, extra=()):
    """写提示词文件并装配基础 argv（t99/main 空间、虚拟墙钟 5、快速落定）。"""
    pf = Path(td) / "prompt.md"
    pf.write_text(PROMPT, encoding="utf-8")
    return ["--target", "t99", "--release-id", rid,
            "--prompt-file", str(pf), "--loop-dir", str(Path(td) / ".loop"),
            "--space", "main", "--wall-clock", "5", "--check-in", "1",
            "--settle-seconds", "0.01", "--settle-checks", "2", *extra]


def extraction(source):
    """把六/五字段 ext 源转成管线 Extraction（Deps.extract 的注入面契约，P2）。
    reply 单列；其余字段按源序进 fields；格式标记默认 False。"""
    src = dict(source)
    reply = src.pop("reply", "")
    return pipeline.Extraction(fields=src, reply=reply, next_skill_invalid=False)


def ext(source):
    """extract 桩构造器：返回给定 ext 的副本。ext 源经显式参数绑定（六字段/五字段
    旧形状各文件本地绑定），不共享自由变量。注入面与 Deps.extract 同形：
    (deps, request, turn_path, injection) → pipeline.Extraction。"""
    return lambda deps, request, turn_path, injection: extraction(source)


def api_env(**kw):
    """api 后端环境映射（经 harness env 注入，不写 os.environ）。
    LOOP_API_MAX_TOKENS 钉 ""（api_config 视空串为默认 32768）：harness 作用域内写入
    覆盖宿主可能导出的同名键，保住「默认 max_tokens」断言的隔离（退出恢复）。T4 先例。"""
    d = {"LOOP_EXTRACT_BACKEND": "api", "LOOP_API_BASE": BASE + "/",
         "LOOP_API_KEY": KEY, "LOOP_API_MODEL": MODEL, "LOOP_API_MAX_TOKENS": ""}
    d.update(kw)
    return d


class FakeResp:
    """http_open 桩响应：payload 序列化为 JSON 字节，上下文管理器形状。"""

    def __init__(self, payload):
        self._b = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False
