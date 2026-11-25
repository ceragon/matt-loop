#!/usr/bin/env python3
"""test_dispatch_learning.py — next_skill 机械反馈＋space 隔离在线 few-shot（ADR-0012，#48 阶段二）。

跑法：python3 loop/scripts/test_dispatch_learning.py
缝：learning.py 公开函数/事务（临时目录真文件 I/O）＋分发脚本 CLI 契约（mock hc/app/网络）。
覆盖：
  纯机械：parse_actual_skill / classify / project_next_skill 边界。
  只读 space 解析：resolve_space_id_for_learning 三态（不建空间）。
  Store：待观察创建（含空）/结算判分/统计/孤儿/跨 space 排除/折叠标记排除（ADR-0019）/id 缺失/保留期/去重/
        few-shot 选取（纠错优先、同 target 优先、限量、预算少选不截断）/损坏降级/锁超时/权限。
  e2e：两轮结算反馈、抽取失败仍带反馈、发送前失败不消耗、few-shot 注入、off 关闭、
        存储故障降级不阻断、重置前置不进序列。
"""
import contextlib
import fcntl
import io
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dispatch  # noqa: E402
import learning  # noqa: E402

from test_dispatch_api_extract import (  # noqa: E402
    SCREEN, EXT, FakeResp, BASE, KEY, MODEL,
)
from test_dispatch_reset import FakeApp, FakeHc  # noqa: E402  （FakeHc 带 refuse_nth 旋钮）

CAND = learning.NEXT_SKILL_CANDIDATES


@contextlib.contextmanager
def learning_env(directory, **kw):
    keys = ["LOOP_LEARNING", "LOOP_LEARNING_DIR", "LOOP_EXTRACT_BACKEND",
            "LOOP_API_BASE", "LOOP_API_KEY", "LOOP_API_MODEL"]
    old = {k: os.environ.pop(k, None) for k in keys}
    os.environ.update({"LOOP_LEARNING": "on", "LOOP_LEARNING_DIR": str(directory),
                       "LOOP_EXTRACT_BACKEND": "api", "LOOP_API_BASE": BASE + "/",
                       "LOOP_API_KEY": KEY, "LOOP_API_MODEL": MODEL})
    os.environ.update({k: v for k, v in kw.items() if v is not None})
    try:
        yield
    finally:
        for k in keys:
            os.environ.pop(k, None)
            if old[k] is not None:
                os.environ[k] = old[k]


def iso(days_ago=0):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(timespec="seconds")


def seed_pending(store, target, rid, skill, space="sp1", invalid=False, reply="历史回复",
                 examples_used=0, ts=None, sequence=None, complete=True):
    store.dir.mkdir(parents=True, exist_ok=True)
    pending = []
    if store.pending_path.exists():
        pending = json.loads(store.pending_path.read_text(encoding="utf-8"))
    pending.append({"target": target, "release_id": rid, "space_id": space,
                    "next_skill": skill, "invalid": invalid, "reply": reply,
                    "sequence": sequence or [], "examples_used": examples_used,
                    "sequence_used": False, "ts": ts or iso(), "complete": complete})
    store.pending_path.write_text(json.dumps(pending, ensure_ascii=False), encoding="utf-8")


def seed_case(store, target, space, verdict, reply="历史回复", ts=None, excluded=None,
              invalid=False, predict_rid="p", actual_rid="a", next_skill="implement",
              actual_skill="to-spec", actual_type="candidate"):
    store.dir.mkdir(parents=True, exist_ok=True)
    case = {"target": target, "space_id": space, "predict_release_id": predict_rid,
            "actual_release_id": actual_rid, "ts": ts or iso(), "verdict": verdict,
            "invalid": invalid, "reply": reply, "next_skill": next_skill,
            "actual_skill": actual_skill, "actual_type": actual_type, "sequence": [],
            "examples_used": 0}
    if excluded:
        case["learning_excluded"] = excluded
    with open(store.cases_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(case, ensure_ascii=False) + "\n")


def run_round(td, rid, prompt, ext_obj, ldir, target="t99", space="main",
              extra_argv=(), fail_extract=False, refuse_nth=None, screen=None):
    pf = Path(td) / (rid + ".txt")
    pf.write_text(prompt, encoding="utf-8")
    ld = Path(td) / ".loop"
    argv = ["--target", target, "--release-id", rid, "--prompt-file", str(pf),
            "--loop-dir", str(ld), "--space", space,
            "--wall-clock", "5", "--check-in", "1",
            "--settle-seconds", "0.01", "--settle-checks", "2", *extra_argv]
    fake = FakeHc(refuse_nth=refuse_nth) if refuse_nth else FakeHc()
    if screen is not None:
        fake.screen = screen
    dispatch.hc_json = fake
    dispatch.app_json = FakeApp()
    dispatch.check_structure_and_tools = lambda: None
    dispatch.shutil.which = lambda name: "/fake/" + name
    dispatch.time.sleep = lambda s: None
    captured = {}
    payload = json.dumps(ext_obj, ensure_ascii=False)

    def fake_open(req, timeout, proxies=None):
        captured["body"] = json.loads(req.data.decode("utf-8"))
        if fail_extract:
            raise urllib_error()
        return FakeResp({"choices": [{"message": {"content": payload}}]})

    dispatch.webhook_open = fake_open
    buf = io.StringIO()
    with learning_env(ldir):
        with contextlib.redirect_stdout(buf):
            rc = dispatch.main(argv)
    res = json.loads(buf.getvalue().strip())
    return rc, res, ld, captured


def urllib_error():
    import urllib.error
    return urllib.error.URLError(ConnectionRefusedError())


def main():
    # ---- 纯机械 ----
    assert learning.parse_actual_skill("/to-spec #7") == ("to-spec", "candidate")
    assert learning.parse_actual_skill("  /wayfinder#7") == ("wayfinder", "candidate")
    assert learning.parse_actual_skill("/implement-spec x") == ("implement-spec", "candidate")
    assert learning.parse_actual_skill("/code-review x") == ("code-review", "other_command")
    assert learning.parse_actual_skill("普通任务文本") == (None, "plain_text")
    assert learning.parse_actual_skill("\n\n  /prototype handoff.md") == ("prototype", "candidate")
    assert learning.classify("to-spec", False, "to-spec", "candidate") == "hit"
    assert learning.classify("implement", False, "to-spec", "candidate") == "miss"
    assert learning.classify("", False, "to-spec", "candidate") == "miss"
    assert learning.classify("", True, "to-spec", "candidate") == "miss"
    assert learning.classify("", False, None, "plain_text") == "correct_abstain"
    assert learning.classify("", False, "code-review", "other_command") == "correct_abstain"
    assert learning.classify("", True, None, "plain_text") == "miss"  # 无效不算正确弃答
    assert learning.classify("implement", False, None, "plain_text") == "miss"
    print("1 parse_actual_skill / classify boundaries ok")

    # ---- 只读 space 解析（不建空间）----
    ls = {"spaces": [{"id": "sp1", "name": "main", "folder": "/proj/a"},
                     {"id": "sp2", "name": "proj", "folder": "/proj/b"},
                     {"id": "sp3", "name": "dup", "folder": "/proj/c"},
                     {"id": "sp4", "name": "dup", "folder": "/proj/d"}]}
    dispatch.app_json = lambda app_bin, args, ts: ls
    assert dispatch.resolve_space_id_for_learning("/fake/app", "main") == "sp1"
    assert dispatch.resolve_space_id_for_learning("/fake/app", "sp2") == "sp2"
    assert dispatch.resolve_space_id_for_learning("/fake/app", "/proj/b") == "sp2"
    assert dispatch.resolve_space_id_for_learning("/fake/app", "/proj/none") is None
    assert dispatch.resolve_space_id_for_learning("/fake/app", "dup") is None  # 重名
    assert dispatch.resolve_space_id_for_learning("/fake/app", "sp999") is None
    assert dispatch.resolve_space_id_for_learning("/fake/app", "missing") is None
    print("2 resolve_space_id_for_learning three-state read-only ok")

    # ---- Store：待观察＋结算＋统计 ----
    with tempfile.TemporaryDirectory() as td:
        store = learning.LearningStore(Path(td) / "learn")
        # 无上一轮 → feedback None，但序列记录
        r = store.settle_and_record("t99", "r1", "sp1", "/wayfinder #7")
        assert r["feedback"] is None and r["warning"] is None, r
        seqs = json.loads(store.sequences_path.read_text(encoding="utf-8"))
        assert seqs["sp1"]["t99"][-1]["skill"] == "wayfinder"
        # 创建待观察（含空预测）
        store.record_prediction("t99", "r1", "sp1", "to-spec", False, "回复A", ["wayfinder"], 0, True)
        store.record_prediction("t99", "r1", "sp1", "to-spec", False, "回复A", ["wayfinder"], 0, True)  # 去重
        pending = json.loads(store.pending_path.read_text(encoding="utf-8"))
        assert len(pending) == 1, pending
        # 结算 hit
        r = store.settle_and_record("t99", "r2", "sp1", "/to-spec #7")
        fb = r["feedback"]
        assert fb["verdict"] == "hit" and fb["predicted_skill"] == "to-spec"
        assert fb["actual_skill"] == "to-spec" and fb["prev_release_id"] == "r1", fb
        assert fb["stats"]["recent20"]["samples"] == 1
        assert fb["stats"]["recent20"]["skill_hit_rate"] == 1.0
        assert fb["stats"]["total"]["samples"] == 1
        assert "examples_0" in fb["stats"]["cohorts"]
        # 案例已建、待观察已消耗
        cases = store._read_cases()
        assert len(cases) == 1 and cases[0]["reply"] == "回复A"
        assert json.loads(store.pending_path.read_text(encoding="utf-8")) == []
        stats = store.read_stats()
        assert stats["total"]["samples"] == 1 and stats["total"]["hit"] == 1
        print("3 store: pending create/dedup, settle hit, case + stats ok")

    # ---- 空预测正确弃答＋无效预测 ----
    with tempfile.TemporaryDirectory() as td:
        store = learning.LearningStore(Path(td) / "learn")
        seed_pending(store, "t99", "p1", "", reply="R")
        r = store.settle_and_record("t99", "a1", "sp1", "普通文本")
        assert r["feedback"]["verdict"] == "correct_abstain"
        seed_pending(store, "t99", "p2", "", invalid=True, reply="R")
        r = store.settle_and_record("t99", "a2", "sp1", "普通文本")
        assert r["feedback"]["verdict"] == "miss"  # 无效不算正确弃答
        assert r["feedback"]["predicted_invalid"] is True
        stats = store.read_stats()
        assert stats["total"]["invalid_prediction"] == 1
        assert stats["total"]["correct_abstain"] == 1
        assert stats["total"]["empty_correct_rate"] == 0.5  # 2 非候选样本，1 正确弃答
        print("4 store: correct abstain vs invalid prediction ok")

    # ---- 孤儿：异常多条只对齐最近一条 ----
    with tempfile.TemporaryDirectory() as td:
        store = learning.LearningStore(Path(td) / "learn")
        seed_pending(store, "t99", "old", "implement", ts=iso(2))
        seed_pending(store, "t99", "new", "to-spec", ts=iso(0))
        r = store.settle_and_record("t99", "a1", "sp1", "/to-spec x")
        assert r["feedback"]["prev_release_id"] == "new"
        assert r["feedback"]["verdict"] == "hit"
        pending = json.loads(store.pending_path.read_text(encoding="utf-8"))
        assert len(pending) == 1 and pending[0]["release_id"] == "old"
        assert pending[0]["orphaned"] is True
        print("5 store: orphaned older pending, align newest ok")

    # ---- 跨 space：反馈结算、案例标 excluded 不入池 ----
    with tempfile.TemporaryDirectory() as td:
        store = learning.LearningStore(Path(td) / "learn")
        seed_pending(store, "t99", "p1", "to-spec", space="sp1", reply="旧space回复")
        r = store.settle_and_record("t99", "a1", "sp2", "/to-spec x")
        assert r["feedback"]["verdict"] == "hit"
        assert r["feedback"]["learning_excluded"] == "space_changed"
        cases = store._read_cases()
        assert cases[0]["learning_excluded"] == "space_changed"
        # sp2 池里选不到这条（它属 sp1 且 excluded）
        inj = store.build_injection("sp2", "t99")
        assert inj["examples_used"] == 0
        inj1 = store.build_injection("sp1", "t99")
        assert inj1["examples_used"] == 0  # excluded 也不入 sp1 池
        print("6 store: cross-space feedback settles, case excluded from pools ok")

    # ---- 折叠标记轮：反馈照旧结算、案例标 excluded 不入池（ADR-0019） ----
    with tempfile.TemporaryDirectory() as td:
        store = learning.LearningStore(Path(td) / "learn")
        seed_pending(store, "t99", "p1", "to-spec", space="sp1",
                     reply="疑似截断回复", complete=False)
        r = store.settle_and_record("t99", "a1", "sp1", "/to-spec x")
        assert r["feedback"]["verdict"] == "hit"
        assert r["feedback"]["learning_excluded"] == "incomplete_turn"
        cases = store._read_cases()
        assert cases[0]["learning_excluded"] == "incomplete_turn"
        inj = store.build_injection("sp1", "t99")
        assert inj["examples_used"] == 0
        print("6b store: incomplete-turn feedback settles, case excluded ok")

    # ---- space id 缺失：禁用 space 学习但反馈仍结算 ----
    with tempfile.TemporaryDirectory() as td:
        store = learning.LearningStore(Path(td) / "learn")
        seed_pending(store, "t99", "p1", "to-spec", space="sp1")
        r = store.settle_and_record("t99", "a1", None, "/to-spec x")
        assert r["warning"]["reason"] == "space_id_missing"
        assert r["feedback"]["verdict"] == "hit"  # target 维度仍结算
        assert store._read_cases() == []  # 无 space id 不入池
        seqs = json.loads(store.sequences_path.read_text(encoding="utf-8")) if store.sequences_path.exists() else {}
        assert seqs == {}  # 序列未记
        # space id 缺失轮也不创建待观察（干净回复不持久化）
        r2 = store.record_prediction("t99", "a1", None, "implement", False, "本轮回复", [], 0, False)
        assert r2["warning"]["reason"] == "space_id_missing"
        assert not store.pending_path.exists() or json.loads(store.pending_path.read_text()) == []
        print("7 store: space id missing disables space learning, feedback still settles ok")

    # ---- few-shot 选取：纠错优先、同 target 优先、限量 4、预算少选不截断 ----
    with tempfile.TemporaryDirectory() as td:
        store = learning.LearningStore(Path(td) / "learn")
        # 纠错（miss）＋命中＋弃答；异 target 纠错故意更新（ts 较新），验同 target 优先压过时间
        seed_case(store, "other", "sp1", "miss", reply="纠错远", ts=iso(0), predict_rid="c1", actual_rid="c1a")
        seed_case(store, "t99", "sp1", "miss", reply="纠错近", ts=iso(1), predict_rid="c2", actual_rid="c2a")
        seed_case(store, "t99", "sp1", "hit", reply="命中", ts=iso(0), predict_rid="h1", actual_rid="h1a")
        seed_case(store, "t99", "sp1", "correct_abstain", reply="弃答", ts=iso(0),
                  predict_rid="a1", actual_rid="a1a", actual_type="plain_text", actual_skill=None)
        seed_case(store, "t99", "sp2", "miss", reply="异space", predict_rid="x1", actual_rid="x1a")
        inj = store.build_injection("sp1", "t99")
        assert inj["examples_used"] == 4, inj
        assert "纠错近" in inj["text"] and "异space" not in inj["text"]
        # 同 target 纠错（较旧）须排在异 target 纠错（较新）之前——target 优先压过时间倒序
        assert inj["text"].index("纠错近") < inj["text"].index("纠错远")
        # 纠错排在命中前
        assert inj["text"].index("纠错近") < inj["text"].index("命中")
        # 预算：一条超大回复独占预算则跳过、仍选小的
        store2 = learning.LearningStore(Path(td) / "learn2")
        seed_case(store2, "t99", "sp1", "miss", reply="x" * (learning.REPLY_BUDGET + 1),
                  ts=iso(0), predict_rid="big", actual_rid="biga")
        seed_case(store2, "t99", "sp1", "miss", reply="小回复", ts=iso(1), predict_rid="sm", actual_rid="sma")
        inj2 = store2.build_injection("sp1", "t99")
        assert inj2["examples_used"] == 1 and "小回复" in inj2["text"]
        assert "x" * 100 not in inj2["text"]  # 超大未截断入内
        print("8 store: few-shot correction-first / same-target / cap4 / budget skip ok")

    # ---- 保留期：90 天＋每 space 100 条；统计不回删 ----
    with tempfile.TemporaryDirectory() as td:
        store = learning.LearningStore(Path(td) / "learn")
        seed_case(store, "t99", "sp1", "hit", reply="过期", ts=iso(100), predict_rid="old", actual_rid="olda")
        for i in range(120):
            seed_case(store, "t99", "sp1", "hit", reply=f"c{i}", ts=iso(0),
                      predict_rid=f"p{i}", actual_rid=f"a{i}")
        # 先累计一条统计
        seed_pending(store, "t99", "pp", "to-spec")
        store.settle_and_record("t99", "aa", "sp1", "/to-spec x")
        before = store.read_stats()["total"]["samples"]
        # 触发清理（record_prediction 走 _save_all）
        store.record_prediction("t99", "trigger", "sp1", "", False, "R", [], 0, False)
        cases = store._read_cases()
        assert all(c.get("reply") != "过期" for c in cases)  # 90 天外删除
        sp1 = [c for c in cases if c.get("space_id") == "sp1"]
        assert len(sp1) <= learning.PER_SPACE_CAP, len(sp1)
        after = store.read_stats()["total"]["samples"]
        assert after == before  # 删案例不回删累计统计
        print("9 store: retention 90d + per-space cap, stats not rolled back ok")

    # ---- 损坏降级＋锁超时＋权限 ----
    with tempfile.TemporaryDirectory() as td:
        store = learning.LearningStore(Path(td) / "learn")
        store.dir.mkdir(parents=True)
        store.pending_path.write_text("{不是 JSON", encoding="utf-8")
        r = store.settle_and_record("t99", "a1", "sp1", "/to-spec x")
        assert r["warning"]["reason"] == "corrupt_record", r
        assert r["feedback"] is None
        # cases 单行损坏跳过、好行仍用
        store2 = learning.LearningStore(Path(td) / "learn2")
        store2.dir.mkdir(parents=True)
        store2.cases_path.write_text("{bad line\n", encoding="utf-8")
        seed_case(store2, "t99", "sp1", "hit", reply="好案例")
        inj = store2.build_injection("sp1", "t99")
        assert inj["examples_used"] == 1 and inj["warning"] is None
        # 锁超时
        store3 = learning.LearningStore(Path(td) / "learn3", lock_timeout=0.2)
        store3.dir.mkdir(parents=True)
        fd = os.open(store3.dir / ".lock", os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            r = store3.settle_and_record("t99", "a1", "sp1", "/to-spec x")
            assert r["warning"]["reason"] == "lock_timeout", r
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        # 权限：目录 0700、文件 0600
        store4 = learning.LearningStore(Path(td) / "learn4")
        store4.record_prediction("t99", "r1", "sp1", "to-spec", False, "R", [], 0, False)
        assert (store4.dir.stat().st_mode & 0o777) == 0o700
        assert (store4.pending_path.stat().st_mode & 0o777) == 0o600
        print("10 store: corrupt degrade / lock timeout / perms 0700-0600 ok")

    # ---- enabled() ----
    with learning_env(Path("/tmp/unused")):
        assert learning.enabled() is True
    os.environ["LOOP_LEARNING"] = "off"
    try:
        assert learning.enabled() is False
    finally:
        os.environ.pop("LOOP_LEARNING", None)
    print("11 enabled(): off disables, other values enable ok")

    # ---- 冷启动：首轮（序列仅含本轮）不注入，prompt 不变；第二轮才注入序列 ----
    with tempfile.TemporaryDirectory() as td:
        store = learning.LearningStore(Path(td) / "learn")
        store.settle_and_record("t99", "r1", "sp1", "普通文本")  # 首轮记序列（1 条）
        inj1 = store.build_injection("sp1", "t99")
        assert inj1["text"] == "" and inj1["sequence_used"] is False, inj1  # 冷启动沿用原 prompt
        assert inj1["sequence"] == ["plain_text"]  # 快照仍含本轮
        store.settle_and_record("t99", "r2", "sp1", "/to-spec x")  # 第二轮（2 条）
        inj2 = store.build_injection("sp1", "t99")
        assert inj2["sequence_used"] is True and "当前近期 skill 序列" in inj2["text"], inj2
        print("11b cold-start: first round no injection, second round injects sequence ok")

    # ---- sequence_used 另记进统计 ----
    with tempfile.TemporaryDirectory() as td:
        store = learning.LearningStore(Path(td) / "learn")
        seed_pending(store, "t99", "p1", "to-spec", reply="R")
        # 手动给 pending 标 sequence_used=True
        pending = json.loads(store.pending_path.read_text(encoding="utf-8"))
        pending[0]["sequence_used"] = True
        store.pending_path.write_text(json.dumps(pending), encoding="utf-8")
        store.settle_and_record("t99", "a1", "sp1", "/to-spec x")
        stats = store.read_stats()
        assert stats["total"]["sequence_used"] == 1, stats
        print("11c sequence_used counted separately in stats ok")

    # ---- e2e：两轮结算反馈＋few-shot 注入 ----
    with tempfile.TemporaryDirectory() as td:
        ldir = Path(td) / "learn"
        ext1 = dict(EXT, next_skill="to-spec")
        rc, res1, _, cap1 = run_round(td, "e1", "请实现功能", ext1, ldir)
        assert rc == 0 and res1["ok"] and res1["next_skill"] == "to-spec", res1
        assert "next_skill_feedback" not in res1  # 首轮无上一轮
        # 冷启动：首轮抽取 prompt 不含历史注入（完全沿用原 prompt）
        assert "历史参考案例" not in cap1["body"]["messages"][1]["content"]
        assert "当前近期 skill 序列" not in cap1["body"]["messages"][1]["content"]
        # 第二轮照用预测 → hit 反馈
        rc, res2, _, cap2 = run_round(td, "e2", "/to-spec #7", dict(EXT, next_skill="implement"), ldir)
        assert rc == 0 and res2["ok"], res2
        fb = res2["next_skill_feedback"]
        assert fb["verdict"] == "hit" and fb["prev_release_id"] == "e1", fb
        # 第三轮：few-shot 注入（e1→e2 已结案例）
        rc, res3, _, cap3 = run_round(td, "e3", "/to-tickets #9", dict(EXT, next_skill=""), ldir)
        assert rc == 0 and res3["ok"], res3
        user_msg = cap3["body"]["messages"][1]["content"]
        assert "历史参考案例" in user_msg and "当前近期 skill 序列" in user_msg
        assert SCREEN in user_msg  # 历史干净回复注入
        print("12 e2e: two-round feedback + few-shot injection ok")

    # ---- e2e：折叠标记轮照常出 result，下轮反馈带 incomplete_turn、案例不入 few-shot ----
    with tempfile.TemporaryDirectory() as td:
        ldir = Path(td) / "learn"
        fold_screen = SCREEN + "\n… +36 lines"
        rc, res1, _, _ = run_round(td, "fold1", "请实现功能", dict(EXT, next_skill="to-spec"),
                                   ldir, screen=fold_screen)
        assert rc == 0 and res1["ok"], res1
        assert res1["complete"] is False and res1["complete_reason"] == ["fold_markers"], res1
        rc, res2, _, cap2 = run_round(td, "fold2", "/to-spec #7", dict(EXT, next_skill=""), ldir)
        assert rc == 0 and res2["ok"], res2
        fb = res2["next_skill_feedback"]
        assert fb["verdict"] == "hit" and fb["learning_excluded"] == "incomplete_turn", fb
        assert "历史参考案例" not in cap2["body"]["messages"][1]["content"]
        print("12b e2e: fold-flagged round settles feedback, case excluded from few-shot ok")

    # ---- e2e：抽取失败仍带反馈（发送后）----
    with tempfile.TemporaryDirectory() as td:
        ldir = Path(td) / "learn"
        run_round(td, "f1", "请实现功能", dict(EXT, next_skill="to-spec"), ldir)
        rc, res, _, _ = run_round(td, "f2", "/to-spec #7", dict(EXT), ldir, fail_extract=True)
        assert rc == 1 and not res["ok"], res
        assert res["error"]["kind"] == "extraction_failed", res
        assert res["next_skill_feedback"]["verdict"] == "hit"  # 发送后结算保留
        print("13 e2e: extraction failure after send still carries feedback ok")

    # ---- e2e：发送前失败不消耗待观察 ----
    with tempfile.TemporaryDirectory() as td:
        ldir = Path(td) / "learn"
        run_round(td, "g1", "请实现功能", dict(EXT, next_skill="to-spec"), ldir)
        rc, res, _, _ = run_round(td, "g2", "/to-spec #7", dict(EXT), ldir, refuse_nth=1)
        assert rc == 1 and res["error"]["kind"] == "send_refused", res
        assert "next_skill_feedback" not in res  # 发送前失败不结算
        pending = json.loads((ldir / "pending.json").read_text(encoding="utf-8"))
        assert any(p["release_id"] == "g1" for p in pending)  # 待观察仍在
        print("14 e2e: pre-send failure does not consume pending ok")

    # ---- e2e：off 关闭＋存储故障降级 ----
    with tempfile.TemporaryDirectory() as td:
        ldir = Path(td) / "learn"
        pf = Path(td) / "off.txt"
        pf.write_text("请实现功能", encoding="utf-8")
        dispatch.hc_json = FakeHc()
        dispatch.app_json = FakeApp()
        dispatch.check_structure_and_tools = lambda: None
        dispatch.shutil.which = lambda name: "/fake/" + name
        dispatch.time.sleep = lambda s: None
        dispatch.webhook_open = lambda req, timeout, proxies=None: FakeResp(
            {"choices": [{"message": {"content": json.dumps(dict(EXT, next_skill="to-spec"))}}]})
        old = {k: os.environ.pop(k, None) for k in ("LOOP_LEARNING", "LOOP_LEARNING_DIR")}
        os.environ.update({"LOOP_LEARNING": "off", "LOOP_LEARNING_DIR": str(ldir),
                           "LOOP_EXTRACT_BACKEND": "api", "LOOP_API_BASE": BASE + "/",
                           "LOOP_API_KEY": KEY, "LOOP_API_MODEL": MODEL})
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = dispatch.main(["--target", "t99", "--release-id", "off1",
                                    "--prompt-file", str(pf), "--loop-dir", str(Path(td) / ".loop"),
                                    "--space", "main", "--wall-clock", "5", "--check-in", "1",
                                    "--settle-seconds", "0.01", "--settle-checks", "2"])
            res = json.loads(buf.getvalue().strip())
            assert rc == 0 and res["ok"] and res["next_skill"] == "to-spec", res
            assert "next_skill_feedback" not in res and "learning_warning" not in res
            assert not ldir.exists()  # off 不碰学习目录
        finally:
            for k in ("LOOP_LEARNING", "LOOP_LEARNING_DIR", "LOOP_EXTRACT_BACKEND",
                      "LOOP_API_BASE", "LOOP_API_KEY", "LOOP_API_MODEL"):
                os.environ.pop(k, None)
            for k, v in old.items():
                if v is not None:
                    os.environ[k] = v
        # 存储故障：LOOP_LEARNING_DIR 指向一个文件 → mkdir 失败 → 降级 warning，主轮仍 ok
        blocker = Path(td) / "blocker"
        blocker.write_text("x", encoding="utf-8")
        rc, res, _, _ = run_round(td, "deg1", "请实现功能", dict(EXT, next_skill="to-spec"),
                                  blocker / "sub")
        assert rc == 0 and res["ok"], res
        assert res["learning_warning"]["reason"] == "io_error", res
        print("15 e2e: off disables learning; storage failure degrades without blocking ok")

    # ---- e2e：重置前置不进序列、不算 ground truth ----
    with tempfile.TemporaryDirectory() as td:
        ldir = Path(td) / "learn"
        rc, res, _, _ = run_round(td, "h1", "普通任务文本", dict(EXT, next_skill=""), ldir,
                                  extra_argv=("--reset", "clear"))
        assert rc == 0 and res["ok"], res
        seqs = json.loads((ldir / "sequences.json").read_text(encoding="utf-8"))
        events = seqs["sp1"]["t99"]
        assert len(events) == 1 and events[0]["skill"] == "plain_text", events
        print("16 e2e: reset pre-step not in sequence, main first-line is ground truth ok")

    print("ALL LEARNING TESTS PASSED")


if __name__ == "__main__":
    main()
