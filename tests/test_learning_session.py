#!/usr/bin/env python3
"""test_learning_session.py — 学习会话纯新增（⑤，ADR-0023 决定 1–4/8）。

跑法：python3 tests/test_learning_session.py
缝：`learning.py` 的公开面——`learning_dir(env)`、`LearningStore(now=…)`、
`LearningSession` 四动词、`Disabled` 空对象、`learning.open(env, now, fact)`；
断言只认外部行为（存储落盘内容、`fact` 写入、返回文本），不碰事务内部结构。
  1 learning_dir 吃 env：LOOP_LEARNING_DIR → XDG_DATA_HOME → HOME；不直读 os.environ。
  2 LearningStore 的 now 注入：四处事件时间戳（序列/统计窗口/案例/待观察）走注入墙钟。
  3 LearningSession.begin：space id 取自回调；None 写 space_id_missing（detail 原样）。
  4 LearningSession.settle：结算反馈与告警经 fact 写入（ledger=False）。
  5 LearningSession.inject：返回注入文本，元数据存会话实例（sequence/examples/sequence_used）。
  6 LearningSession.record：签名瘦身——预测/无效标记/reply/注入元数据取自会话与 Extraction。
  7 Disabled：begin 不调回调（关闭时零 ls/零 require_app）、settle/record 空操作、inject 返回 ""。
  8 parity：inspect.signature 逐动词比对 LearningSession 与 Disabled（ADR-0023 决定 4）。
  9 open：off → Disabled，on → LearningSession。
  10 故障注入：权限/IO 错误、锁超时、记录损坏 → 动词不抛、learning_warning 落 ctx。
"""
import fcntl
import inspect
import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

from datetime import datetime, timedelta, timezone

import harness  # noqa: F401  （把 loop/scripts 放进 sys.path）
import learning

# 固定「注入的」墙钟值：取昨天（在 90 天保留期内，不触发 _prune 的 cutoff）。
NOW = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds")


def facts_recorder():
    """fact 写入口的探针：记录键值、值来源与 ledger 参数（会话只写 ledger=False）。"""
    facts = {}
    calls = []

    def fact(key, value, *, ledger=True):
        facts[key] = value
        calls.append((key, ledger))

    return facts, fact, calls


def session_at(store, fact, target="t99", release_id="r1"):
    return learning.LearningSession(store, fact, target, release_id)


def store_at(directory, now=NOW, lock_timeout=learning.LOCK_TIMEOUT_S):
    return learning.LearningStore(Path(directory), lock_timeout=lock_timeout,
                                  now=lambda: now)


def test_learning_dir_takes_env():
    with tempfile.TemporaryDirectory() as td:
        explicit = Path(td) / "explicit"
        assert learning.learning_dir({"LOOP_LEARNING_DIR": str(explicit)}) == explicit

        xdg = Path(td) / "xdg"
        assert (learning.learning_dir({"XDG_DATA_HOME": str(xdg)})
                == xdg / "matt-loop" / "learning")

        home = Path(td) / "home"
        assert (learning.learning_dir({"HOME": str(home)})
                == home / ".local" / "share" / "matt-loop" / "learning")
    print("1 learning_dir(env): LOOP_LEARNING_DIR / XDG_DATA_HOME / HOME ok")


def test_store_now_injection_drives_all_four_event_timestamps():
    with tempfile.TemporaryDirectory() as td:
        store = store_at(Path(td) / "learn")
        store.record_prediction("t99", "r1", "sp1", "to-spec", False, "回复", [], 0, False)
        store.settle_and_record("t99", "r2", "sp1", "/to-spec x")
        pending = json.loads(store.pending_path.read_text(encoding="utf-8"))
        sequences = json.loads(store.sequences_path.read_text(encoding="utf-8"))
        stats = json.loads(store.stats_path.read_text(encoding="utf-8"))
        cases = [json.loads(line) for line in
                 store.cases_path.read_text(encoding="utf-8").splitlines() if line]
        assert pending == [], "上一轮待观察被结算消耗"
        assert sequences["sp1"]["t99"][0]["ts"] == NOW, sequences
        assert stats["recent"][-1]["ts"] == NOW, stats
        assert cases[0]["ts"] == NOW, cases
        # 待观察时间戳（第四处）单独验：再记一轮看 pending 的 ts。
        store.record_prediction("t99", "r3", "sp1", "", False, "回复", [], 0, False)
        pending = json.loads(store.pending_path.read_text(encoding="utf-8"))
        assert pending[0]["ts"] == NOW, pending
    print("2 LearningStore(now=...): all four event ts use injected clock ok")


def test_begin_resolves_space_id_or_warns():
    with tempfile.TemporaryDirectory() as td:
        facts, fact, calls = facts_recorder()
        store = store_at(Path(td) / "learn")
        s = session_at(store, fact)
        s.begin(lambda: "sp1")
        assert s.space_id == "sp1"
        assert facts == {}, "解析成功不写 ctx"

        facts, fact, calls = facts_recorder()
        s = session_at(store_at(Path(td) / "learn2"), fact)
        s.begin(lambda: None)
        assert s.space_id is None
        assert facts["learning_warning"] == {
            "reason": "space_id_missing",
            "detail": "--space 未能只读解析出唯一 space id，本轮序列/案例/few-shot 禁用"}
        assert calls == [("learning_warning", False)], calls
    print("3 begin: space id from callback; None writes space_id_missing (detail verbatim) ok")


def test_settle_writes_feedback_and_warning_through_fact():
    with tempfile.TemporaryDirectory() as td:
        store = store_at(Path(td) / "learn")
        store.record_prediction("t99", "r0", "sp1", "to-spec", False, "上轮回复", [], 0, True)
        facts, fact, calls = facts_recorder()
        s = session_at(store, fact, release_id="r1")
        s.begin(lambda: "sp1")
        s.settle("/to-spec x")
        fb = facts["next_skill_feedback"]
        assert fb["verdict"] == "hit" and fb["prev_release_id"] == "r0", fb
        assert all(ledger is False for _key, ledger in calls), calls
        assert ("next_skill_feedback", False) in calls

        # 无上一轮预测：不写 feedback（不制造空值）；space id 缺失：写告警。
        facts, fact, calls = facts_recorder()
        s2 = session_at(store_at(Path(td) / "learn2"), fact, release_id="r2")
        s2.begin(lambda: None)
        s2.settle("普通文本")
        assert "next_skill_feedback" not in facts, facts
        assert facts["learning_warning"]["reason"] == "space_id_missing", facts
    print("4 settle: feedback + warning via fact(ledger=False), no empty feedback ok")


def test_inject_returns_text_and_holds_injection_metadata():
    with tempfile.TemporaryDirectory() as td:
        store = store_at(Path(td) / "learn")
        # 序列 ≥2 条（冷启动护栏）才会注入序列块；两条都算前轮。
        store.settle_and_record("t99", "r0a", "sp1", "/wayfinder x")
        store.settle_and_record("t99", "r0b", "sp1", "/to-tickets x")
        case = {"target": "t99", "space_id": "sp1", "predict_release_id": "p0",
                "actual_release_id": "a0", "ts": NOW, "verdict": "hit", "invalid": False,
                "reply": "历史干净回复", "next_skill": "implement",
                "actual_skill": "implement", "actual_type": "candidate", "sequence": [],
                "examples_used": 0}
        with open(store.cases_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(case, ensure_ascii=False) + "\n")
        facts, fact, calls = facts_recorder()
        s = session_at(store, fact, release_id="r1")
        s.begin(lambda: "sp1")
        text = s.inject()
        assert "历史干净回复" in text and "当前近期 skill 序列" in text, text
        assert s.examples_used == 1, s.examples_used
        assert s.sequence_used is True, s.sequence_used
        assert s.sequence == ["wayfinder", "to-tickets"], s.sequence
        assert facts == {}, "无告警不写 ctx"

        # 未调用 inject 时的初值（[]/0/False）由决定 6 钉住。
        fresh = session_at(store, fact)
        assert (fresh.sequence, fresh.examples_used, fresh.sequence_used) == ([], 0, False)
    print("5 inject: returns text, holds sequence/examples/sequence_used on session ok")


def test_record_uses_held_metadata_and_extraction():
    with tempfile.TemporaryDirectory() as td:
        store = store_at(Path(td) / "learn")
        facts, fact, calls = facts_recorder()
        s = session_at(store, fact, release_id="r1")
        s.begin(lambda: "sp1")
        # 不经 inject：初值 []/0/False 进待观察（决定 6）。
        s.record(SimpleNamespace(fields={"next_skill": "to-spec"}, reply="本轮干净回复",
                                 next_skill_invalid=False), complete=True)
        pending = json.loads(store.pending_path.read_text(encoding="utf-8"))
        assert pending[0]["next_skill"] == "to-spec", pending
        assert pending[0]["reply"] == "本轮干净回复", pending
        assert pending[0]["sequence"] == [], pending
        assert pending[0]["examples_used"] == 0, pending
        assert pending[0]["sequence_used"] is False, pending

        # 经 inject 后记录：注入元数据随待观察持久化。
        store2 = store_at(Path(td) / "learn2")
        store2.settle_and_record("t99", "r0", "sp1", "/wayfinder x")
        s2 = session_at(store2, fact, release_id="r2")
        s2.begin(lambda: "sp1")
        s2.inject()
        s2.record(SimpleNamespace(fields={"next_skill": ""}, reply="R",
                                  next_skill_invalid=True), complete=False)
        pending = json.loads(store2.pending_path.read_text(encoding="utf-8"))
        assert pending[0]["invalid"] is True, pending
        assert pending[0]["sequence"] == ["wayfinder"], pending
        assert pending[0]["complete"] is False, pending
    print("6 record(extraction, complete): slim signature via held metadata ok")


def test_disabled_is_noop_and_does_not_call_callback():
    with tempfile.TemporaryDirectory() as td:
        facts, fact, calls = facts_recorder()
        d = learning.Disabled()
        invoked = []
        d.begin(lambda: invoked.append("resolved"))
        d.settle("首行")
        assert d.inject() == ""
        d.record(SimpleNamespace(fields={}, reply="", next_skill_invalid=False), True)
        assert invoked == [], "Disabled.begin 不调回调（关闭时零 ls / 零 require_app）"
        assert facts == {} and calls == [], "空对象不写 ctx"
    print("7 Disabled: begin does not call resolver, other verbs no-op, inject '' ok")


def test_parity_signatures_between_session_and_disabled():
    for verb in ("begin", "settle", "inject", "record"):
        a = inspect.signature(getattr(learning.LearningSession, verb))
        b = inspect.signature(getattr(learning.Disabled, verb))
        assert a == b, (verb, a, b)
    print("8 parity: four verb signatures identical between LearningSession and Disabled ok")


def test_open_returns_disabled_or_session_by_env():
    with tempfile.TemporaryDirectory() as td:
        _facts, fact, _calls = facts_recorder()
        assert isinstance(learning.open({"LOOP_LEARNING": "off"}, lambda: NOW, fact,
                                        target="t99", release_id="r1"),
                          learning.Disabled)
        opened = learning.open({"LOOP_LEARNING": "on",
                                "LOOP_LEARNING_DIR": str(Path(td) / "learn")},
                               lambda: NOW, fact, target="t99", release_id="r1")
        assert isinstance(opened, learning.LearningSession)
        assert opened._target == "t99" and opened._release_id == "r1"
    print("9 open: off -> Disabled, on -> LearningSession with round keys ok")


def test_fault_injection_degrades_without_raising():
    # 权限/IO 错误：存储目录路径被普通文件占住 → mkdir 失败 → io_error。
    with tempfile.TemporaryDirectory() as td:
        blocker = Path(td) / "blocker"
        blocker.write_text("x", encoding="utf-8")
        facts, fact, calls = facts_recorder()
        s = session_at(store_at(blocker / "sub"), fact, release_id="r1")
        s.begin(lambda: "sp1")
        s.record(SimpleNamespace(fields={"next_skill": ""}, reply="R",
                                 next_skill_invalid=False), True)
        assert facts["learning_warning"]["reason"] == "io_error", facts
        assert calls == [("learning_warning", False)], calls

    # 锁超时：另一 fd 持有 flock，动词等锁超时 → lock_timeout。
    with tempfile.TemporaryDirectory() as td:
        store = store_at(Path(td) / "learn", lock_timeout=0.1)
        store.dir.mkdir(parents=True)
        fd = os.open(store.dir / ".lock", os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            facts, fact, _calls = facts_recorder()
            s = session_at(store, fact, release_id="r1")
            s.begin(lambda: "sp1")
            s.settle("/to-spec x")
            assert facts["learning_warning"]["reason"] == "lock_timeout", facts
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    # 记录损坏：pending.json 不是 JSON → corrupt_record。
    with tempfile.TemporaryDirectory() as td:
        store = store_at(Path(td) / "learn")
        store.dir.mkdir(parents=True)
        store.pending_path.write_text("{不是 JSON", encoding="utf-8")
        facts, fact, _calls = facts_recorder()
        s = session_at(store, fact, release_id="r1")
        s.begin(lambda: "sp1")
        s.settle("/to-spec x")
        assert facts["learning_warning"]["reason"] == "corrupt_record", facts
    print("10 fault injection: io_error / lock_timeout / corrupt_record degrade via warning ok")


def main():
    test_learning_dir_takes_env()
    test_store_now_injection_drives_all_four_event_timestamps()
    test_begin_resolves_space_id_or_warns()
    test_settle_writes_feedback_and_warning_through_fact()
    test_inject_returns_text_and_holds_injection_metadata()
    test_record_uses_held_metadata_and_extraction()
    test_disabled_is_noop_and_does_not_call_callback()
    test_parity_signatures_between_session_and_disabled()
    test_open_returns_disabled_or_session_by_env()
    test_fault_injection_degrades_without_raising()
    print("ALL LEARNING SESSION TESTS PASSED")


if __name__ == "__main__":
    main()
