#!/usr/bin/env python3
"""test_dispatch_turn_cut.py — 长回复历史回读、断轮与可读抽取失败诊断（#50，ADR-0013）。

跑法：python3 tests/test_dispatch_turn_cut.py
主测试缝＝唯一 harness.run（CLI 契约，T5d/ADR-0021）：FakeHerdr 假世界摆屏幕，
env / extract / http_open / run_cmd 注入，断言只认 rc、stdout JSON、台账、落盘产物
（raw/turn/run/notify）与假世界日志（hc_calls 核对 --lines 1000）。

覆盖（规格五组＋折叠判据组）：
  1 断轮：❯ 回声优先／回复引用锚点不误切／无 ❯ 退化锚点最后出现行／无锚点整段。
  2 作用域：折叠与逐字校验只对本轮段；折叠命中只标记 complete:false 不阻断（ADR-0019）。
  3 双文件与结果：raw/turn/run 三产物各指正确内容；台账 echo_found/切点/段字符数。
  4 错误载荷：extraction_rewritten 逐字段整行 hint、双路径、按实际后端取文案。
  5 回归：回读请求 --lines 1000。
  6 折叠判据（ADR-0019，#79）：实证形态两分支、U+2026 码位、回声排除、降级契约全链。

T5d（ADR-0021，#99）：完整轮用例迁 FakeHerdr＋harness.run；slice8 噪声护栏（其余纯文本
300 字符摘录）并入 test_command_parse（命令层错误映射缝，Q3）；fold_hits 纯函数直调
保留原位。R（#104）：共享常量与 world/api_env/FakeResp 迁 helpers 单一出处。
"""
import json
import tempfile
import types
from pathlib import Path

import harness
from harness import dispatch
from helpers import PROMPT, ANCHOR, world, api_env, FakeResp, extraction

OLD = "旧轮回复第一行\n旧轮回复第二行"
ECHO = "❯ " + ANCHOR + " …"
NEW = "本轮回复第一行\n本轮回复第二行"


def run_round(screen, rid, ext, prompt=PROMPT, mock_extract=True,
              env=None, http_open=None, run_cmd=None):
    """经唯一 harness.run 跑一轮完整 dispatch；mock_extract=True 时抽取依赖为返回
    给定 ext 的桩并记录其输入路径与内容；False 时用真实抽取入口 dispatch.extract
    （调用方自备 http_open/run_cmd 桩与 env）。返回假世界供 hc_calls 断言。"""
    with tempfile.TemporaryDirectory() as td:
        pf = Path(td) / "prompt.md"
        pf.write_text(prompt, encoding="utf-8")
        ld = Path(td) / ".loop"
        argv = ["--target", "t99", "--release-id", rid,
                "--prompt-file", str(pf), "--loop-dir", str(ld),
                "--space", "main",
                "--wall-clock", "5", "--check-in", "1",
                "--settle-seconds", "0.01", "--settle-checks", "2"]
        w = world(screen)
        captured = {}

        def recording_extract(deps, request, path, injection):
            captured["extract_path"] = str(path)
            captured["extract_input"] = Path(path).read_text(encoding="utf-8")
            return extraction(ext)

        r = harness.run(argv, world=w, env=env,
                        extract=recording_extract if mock_extract else dispatch.extract,
                        http_open=http_open, run_cmd=run_cmd)
        files = {}
        for suffix in (".raw.md", ".turn.md", ".md", ".notify.json"):
            p = ld / "runs" / (rid + suffix)
            if p.exists():
                files[suffix] = p.read_text(encoding="utf-8")
        return r.rc, r.result, r.ledger, files, captured, w


def slice1_echo_cut():
    """多轮历史带 ❯ 回声：只保留本轮（含回声行），双文件逐字，台账如实。"""
    screen = OLD + "\n" + ECHO + "\n" + NEW
    turn = ECHO + "\n" + NEW
    ext = {"needs_user": False, "questions": [],
           "conclusion": "本轮回复第一行", "blocked": "", "reply": NEW}
    rc, res, led, files, cap, w = run_round(screen, "tc-echo-1", ext)
    assert rc == 0 and res["ok"], res
    assert files[".raw.md"] == screen, "回读全文须逐字含旧轮"
    assert files[".turn.md"] == turn, "本轮原料须逐字只含断轮段（含回声行）"
    assert files[".md"] == NEW
    assert res["raw_record"].endswith("tc-echo-1.raw.md"), res
    assert res["turn_record"].endswith("tc-echo-1.turn.md"), res
    assert res["run_record"].endswith("tc-echo-1.md"), res
    assert led["echo_found"] is True, led
    assert led["turn_cut_line"] == 3, led
    assert led["turn_chars"] == len(turn), led
    assert led["chars"] == len(screen), led
    assert cap["extract_path"] == res["turn_record"], "抽取器输入须是本轮原料"
    assert cap["extract_input"] == turn
    assert res["anchor_found"] is True
    reads = [a for a in w.hc_calls if a[0] == "read"]
    assert reads, "须有 read 调用"
    for a in reads:
        assert a[a.index("--lines") + 1] == "1000", a
    print("1 echo cut: turn-only segment, dual files, ledger, --lines 1000 ok")


def slice2_echo_priority():
    """回复正文引用锚点但无 ❯ 前缀：仍从真正的回声行切，不误切回复开头。"""
    quote = "回复引用：" + ANCHOR
    screen = ECHO + "\n" + quote + "\n" + NEW
    ext = {"needs_user": False, "questions": [],
           "conclusion": "本轮回复第一行", "blocked": "",
           "reply": quote + "\n" + NEW}
    rc, res, led, files, cap, w = run_round(screen, "tc-echo-prio", ext)
    assert rc == 0 and res["ok"], res
    assert files[".turn.md"] == screen, "回声行在首行时本轮段＝全文（含引用行）"
    assert led["turn_cut_line"] == 1 and led["echo_found"] is True, led
    assert quote in files[".md"], "引用锚点的回复行须保留"
    print("2 echo priority over later anchor quote ok")


def slice3_fallback_last_anchor():
    """无 ❯ 形态回声：退化为含锚点的最后一行。"""
    screen = ANCHOR + "\n旧回复\n" + ANCHOR + "\n" + NEW
    turn = ANCHOR + "\n" + NEW
    ext = {"needs_user": False, "questions": [],
           "conclusion": "本轮回复第一行", "blocked": "", "reply": NEW}
    rc, res, led, files, cap, w = run_round(screen, "tc-fallback", ext)
    assert rc == 0 and res["ok"], res
    assert files[".turn.md"] == turn, files
    assert led["echo_found"] is True and led["turn_cut_line"] == 3, led
    assert led["turn_chars"] == len(turn), led
    print("3 no-echo fallback to last anchor line ok")


def slice4_no_anchor_whole():
    """锚点完全找不到：整段使用，echo_found 假，切点/字符数如实表达整段。"""
    screen = "没有锚点的回复\n第二行"
    ext = {"needs_user": False, "questions": [],
           "conclusion": "", "blocked": "", "reply": screen}
    rc, res, led, files, cap, w = run_round(screen, "tc-noanchor", ext)
    assert rc == 0 and res["ok"], res
    assert files[".turn.md"] == screen == files[".raw.md"], files
    assert led["echo_found"] is False, led
    assert led["turn_cut_line"] == 1, led
    assert led["turn_chars"] == led["chars"] == len(screen), led
    assert res["anchor_found"] is False
    assert cap["extract_input"] == screen
    print("4 no anchor: whole segment, echo_found=False ok")


def slice5_fold_scope():
    """折叠判据作用域（ADR-0019）：只对本轮段；命中只标记 complete:false，抽取照常。"""
    dummy = {"needs_user": False, "questions": [], "conclusion": "", "blocked": "", "reply": ""}
    # a 旧轮含折叠、本轮干净 → complete:true
    screen = "旧轮\n… +36 lines\n" + ECHO + "\n" + NEW
    ext = {"needs_user": False, "questions": [],
           "conclusion": "本轮回复第一行", "blocked": "", "reply": NEW}
    rc, res, led, files, cap, w = run_round(screen, "tc-fold-old", ext)
    assert rc == 0 and res["ok"] and res["complete"], res
    assert "… +36 lines" in files[".raw.md"] and "… +36 lines" not in files[".turn.md"]
    # b 本轮含折叠 → 标记不阻断，抽取照跑，result/台账带标记
    screen = ECHO + "\n" + NEW + "\n… +25 lines (~2 KB)"
    rc, res, led, files, cap, w = run_round(screen, "tc-fold-turn", dummy)
    assert rc == 0 and res["ok"], res
    assert res["complete"] is False and res["complete_reason"] == ["fold_markers"], res
    assert led["complete"] is False and led["complete_reason"] == ["fold_markers"], led
    assert cap["extract_input"] == files[".turn.md"], "命中不阻断抽取"
    assert "… +25 lines (~2 KB)" in files[".turn.md"]
    # c 整段使用（无锚点）＋旧轮折叠 → 诚实标记，不阻断
    screen = "旧轮\n… +36 lines\n无锚点回复"
    rc, res, led, files, cap, w = run_round(screen, "tc-fold-whole", dummy)
    assert rc == 0 and res["ok"] and res["complete"] is False, res
    assert led["echo_found"] is False
    print("5 fold scope: old-turn pass / turn flag / whole-segment honest flag ok")


def slice6_verbatim_scope():
    """只在旧轮出现的文本不能让本轮改写字段或回复行通过逐字校验。"""
    old_only = "旧轮独有结论句"
    screen = old_only + "\n" + ECHO + "\n" + NEW
    ext = {"needs_user": False, "questions": ["本轮回复第一行"],
           "conclusion": old_only, "blocked": "",
           "reply": old_only + "\n" + NEW}
    rc, res, led, files, cap, w = run_round(screen, "tc-verbatim-scope", ext)
    assert rc == 0 and res["ok"], res
    assert old_only in files[".raw.md"] and old_only not in files[".turn.md"]
    assert set(res["rewritten"]) == {"conclusion", "reply"}, res["rewritten"]
    turn_lines = [ln for ln in (ECHO + "\n" + NEW).splitlines() if ln.strip()]
    assert res["rewritten"]["conclusion"] in turn_lines, res["rewritten"]
    assert res["rewritten"]["reply"] in turn_lines, res["rewritten"]
    print("6 verbatim scope: old-turn text cannot pass rewritten fields/reply ok")


def slice7_error_payload():
    """extraction_rewritten：逐字段整行 hint（长行零截断）＋双路径＋按实际后端取文案。"""
    long_line = "本轮结论：" + "长" * 400   # 远超旧 300 字符护栏
    screen = ECHO + "\n" + long_line
    ext = {"needs_user": False, "questions": ["瞎编的问题"],
           "conclusion": "本轮结论：短", "blocked": "", "reply": "瞎编的回复"}
    for backend, env in (("pi", None), ("api", api_env())):
        rc, res, led, files, cap, w = run_round(screen, "tc-payload-" + backend, ext, env=env)
        assert rc == 1 and res["error"]["kind"] == "extraction_rewritten", res
        assert f"{backend} 在改写而不是抽取" in res["error"]["message"], res["error"]["message"]
        assert "rewritten" not in res["error"], "不新增结构化错误字段"
        hints = res["error"]["hints"]
        field_hints = [h for h in hints if "最接近的原文整行" in h]
        assert len(field_hints) == 2, hints   # conclusion + questions[0]
        assert any(h.startswith("conclusion ") for h in field_hints), hints
        assert any(h.startswith("questions[0] ") for h in field_hints), hints
        # 长行原样整行出现、零截断（hint 以完整长行收尾）
        assert any(h.endswith(long_line) for h in field_hints), "长行被截断"
        assert any(("tc-payload-%s.turn.md" % backend) in h for h in hints), hints
        assert any(("tc-payload-%s.raw.md" % backend) in h for h in hints), hints
        # 不再出现被切开的序列化 JSON 片段
        assert not any('"conclusion":' in h for h in hints), hints
    print("7 error payload: per-field full-line hints, dual paths, backend message ok")


def slice9_backend_turn_input():
    """api 与 pi 两后端真实抽取输入都是本轮原料，不含旧轮（决策 11）。"""
    screen = OLD + "\n" + ECHO + "\n" + NEW
    turn = ECHO + "\n" + NEW
    ext = {"needs_user": False, "questions": [], "conclusion": "本轮回复第一行",
           "blocked": "", "reply": NEW, "next_skill": ""}
    payload = {"choices": [{"message": {"content": json.dumps(ext, ensure_ascii=False)}}]}
    # api：内联 user 内容含本轮段、不含旧轮（真实抽取链经 harness 残留洞吸收密闭跑）
    cap = {}

    def fake_open(req, timeout, proxies):
        cap["body"] = json.loads(req.data.decode("utf-8"))
        return FakeResp(payload)

    rc, res, led, files, _, _ = run_round(screen, "tc-api-input", ext, mock_extract=False,
                                          env=api_env(), http_open=fake_open)
    assert rc == 0 and res["ok"] and res["extract_backend"] == "api", res
    user = cap["body"]["messages"][1]["content"]
    assert NEW in user and ECHO in user, "本轮段须内联"
    assert OLD not in user, "旧轮不得进入 api 抽取输入"
    # pi：@附件指本轮原料文件，内容逐字＝本轮段
    pi_cap = {}

    def fake_run_cmd(argv, timeout):
        pi_cap["argv"] = argv
        return types.SimpleNamespace(returncode=0, stdout=json.dumps(ext, ensure_ascii=False), stderr="")

    rc, res, led, files, _, _ = run_round(screen, "tc-pi-input", ext, mock_extract=False,
                                          env={"LOOP_EXTRACT_BACKEND": "pi"}, run_cmd=fake_run_cmd)
    assert rc == 0 and res["ok"] and res["extract_backend"] == "pi", res
    assert "@" + res["turn_record"] in pi_cap["argv"], pi_cap["argv"]
    attached = files[".turn.md"]   # run_round 已在临时目录内逐字读出
    assert attached == turn and OLD not in attached
    assert any("本轮原料落盘路径" in a for a in pi_cap["argv"])
    print("9 backend input: api inline & pi @file both turn-only ok")


def slice10_fold_criterion():
    """折叠判据纯函数（ADR-0019，#79）：两独立分支、U+2026 码位、单复数、回声排除、箭头回归。"""
    fh = dispatch.fold_hits
    P = "请实现某个功能"
    # 分支一：行首（允许前导空白）、单复数、KB 后缀
    assert fh("… +36 lines", P)
    assert fh("     … +36 lines", P)
    assert fh("… +25 lines (~2 KB)", P)
    assert fh("… +1 line", P)
    # 分支一不命中：行中散文、三个 ASCII 点（码位回归）、非数字 N
    assert not fh("如上文 … +36 lines 所示", P)
    assert not fh("... +36 lines", P)
    assert not fh("… +N lines", P)
    # 分支二：两种实证形态（⎿ 变体左括号后另有内容，字面不带左括号）
    assert fh("› Message from @X (ctrl+o to expand)", P)
    assert fh("⎿ Backgrounded agent (↓ to manage · ctrl+o to expand)", P)
    # #79 回归：那两个箭头字符（U+25B2/U+25BC）无折叠语义，行中行首都不命中
    assert not fh("回复引用 \u25b2\u25bc 折叠判据", P)
    assert not fh("\u25b2 行首也不命中", P)
    assert not fh("\u25bc", P)
    # 回声排除：与提示词某行去首尾空白后精确相同的行不参与；非精确相同不排除
    prompt = "第一行\n… +36 lines\n第三行"
    assert not fh("… +36 lines", prompt)
    assert not fh("  … +36 lines  ", prompt)
    assert fh("… +37 lines", prompt), "不同行不被排除"
    assert fh("… +36 lines (~2 KB)", prompt), "非精确相同不排除"
    print("10 fold criterion: two branches, U+2026, plural, echo exclusion, arrow regression ok")


def slice11_fold_demotion_contract():
    """降级契约（ADR-0019，#79）：notify 带标记；抽取失败 error 仍带标记；hc 自报 complete 无关。"""
    # a 命中轮：result/台账/notify 三处齐带 complete:false，抽取照跑
    screen = ECHO + "\n" + NEW + "\n… +36 lines"
    ext = {"needs_user": False, "questions": [],
           "conclusion": "本轮回复第一行", "blocked": "", "reply": NEW, "next_skill": ""}
    rc, res, led, files, cap, w = run_round(screen, "tc-fold-notify", ext)
    assert rc == 0 and res["ok"], res
    assert res["complete"] is False and res["complete_reason"] == ["fold_markers"], res
    assert led["complete"] is False and led["complete_reason"] == ["fold_markers"], led
    assert led["hc_read"]["complete"] is True, "hc 自报 complete 与折叠判据无关"
    assert cap["extract_input"] == files[".turn.md"], "命中不阻断抽取"
    notify = json.loads(files[".notify.json"])
    assert set(notify) == set(dispatch.NOTIFY_SUCCESS_FIELDS), notify
    assert notify["complete"] is False and notify["complete_reason"] == ["fold_markers"], notify
    # b 命中＋抽取失败：error 对象仍带完整性标记（不丢在台账里）
    ext_bad = {"needs_user": False, "questions": [],
               "conclusion": "原文中不存在的句子", "blocked": "", "reply": NEW}
    rc, res, led, files, cap, w = run_round(screen, "tc-fold-extfail", ext_bad)
    assert rc == 1 and res["error"]["kind"] == "extraction_rewritten", res
    assert res["complete"] is False and res["complete_reason"] == ["fold_markers"], res
    print("11 fold demotion: result/ledger/notify flags, error carries flag, hc complete irrelevant ok")


def main():
    slice1_echo_cut()
    slice2_echo_priority()
    slice3_fallback_last_anchor()
    slice4_no_anchor_whole()
    slice5_fold_scope()
    slice6_verbatim_scope()
    slice7_error_payload()
    # slice8 噪声护栏已并入 test_command_parse（T5d，Q3：FakeHerdr 说 dict 造不出的故障）。
    slice9_backend_turn_input()
    slice10_fold_criterion()
    slice11_fold_demotion_contract()
    print("ALL TURN-CUT TESTS PASSED")


if __name__ == "__main__":
    main()
