#!/usr/bin/env python3
"""learning.py — next_skill 机械反馈＋space 隔离在线 few-shot（ADR-0012，#48 阶段二）。

脚本只做机械收集、对齐、统计和限量选取，模型只做语义预测；本模块不调模型、不发网络、
不起子进程（hc/herdr-app 的只读解析由 dispatch 侧完成后把 space id 传进来）。学习复用
每轮既有抽取调用，不新增模型请求。

纯机械事实源：用户实际放行的下一轮主任务首个非空行是唯一 ground truth，按调用方传入的
target 字面值分组结算上一轮预测；target_address 不作长期键，跨 target 不互判。案例池按
解析后的 space id 隔离，跨 space 或折叠标记轮（complete:false，ADR-0019）只结算机械反馈、
案例标 learning_excluded 不入池。

建议性不变量：任何锁失败、超时、权限错误、I/O 错误或记录损坏都降级为 learning_warning，
不新增 error kind、不改退出码、不阻断主轮。本地读改写用短临界区锁，锁绝不跨抽取 API/pi
调用（settle / build_injection / record_prediction 各自独立短事务）。只含计数的全局汇总
可长期保留，删案例正文不回删累计统计。清理由正常读写触发，无后台守护进程。

存储（默认 ${XDG_DATA_HOME:-~/.local/share}/matt-loop/learning/，LOOP_LEARNING_DIR 可覆盖）：
  cases.jsonl     学习案例（含预测轮干净回复全文），每 space 最近 100 条、90 天
  pending.json    待观察预测（含空预测），同受保留期约束
  sequences.json  近期实际 skill 序列（同 space id＋同 target，最多 8 个主任务）
  stats.json      全局累计计数＋最近 20 条滚动窗口＋examples_used 两队列（原子写）
目录 0700、文件 0600。LOOP_LEARNING=off 时 dispatch 完全不读写本模块。
"""

import contextlib
import fcntl
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# 候选集固定七项（ADR-0008）：不从历史自动扩集；扩集须另行明确决策。
NEXT_SKILL_CANDIDATES = ("wayfinder", "grill-with-docs", "to-spec", "to-tickets",
                         "implement", "implement-spec", "prototype")

# 机械护栏（ADR-0012）：保留期 90 天、每 space 最近 100 条、近期序列 8、few-shot 4 条、
# 历史干净回复总预算 16000 字符、滚动窗口 20 条。
RETENTION_DAYS = 90
PER_SPACE_CAP = 100
SEQUENCE_CAP = 8
MAX_EXAMPLES = 4
REPLY_BUDGET = 16000
RECENT_WINDOW = 20
LOCK_TIMEOUT_S = 5.0

# 首个非空行的斜杠命令解析：行首空白容忍，命令名取到空白/参数前。
_SLASH_RE = re.compile(r"^\s*/([A-Za-z0-9][A-Za-z0-9_-]*)")


def enabled(env=None):
    """LOOP_LEARNING=off 关闭；其他值（含未设）默认开启（ADR-0012 §5.5）。

    env 是可注入的环境映射（默认 os.environ；管线传 deps.env，收掉直读洞）。
    """
    _env = os.environ if env is None else env
    return _env.get("LOOP_LEARNING", "").strip().lower() != "off"


def learning_dir(env=None):
    """学习存储目录（ADR-0012 §5.1，ADR-0023 决定 3）：LOOP_LEARNING_DIR → XDG_DATA_HOME →
    `$HOME/.local/share`。env 是可注入环境映射（默认 os.environ；管线传 deps.env），
    用 `env["HOME"]` 取代 `Path.home()`，harness 不写 os.environ 即可指定目录。"""
    _env = os.environ if env is None else env
    override = (_env.get("LOOP_LEARNING_DIR") or "").strip()
    if override:
        return Path(override).expanduser()
    xdg = (_env.get("XDG_DATA_HOME") or "").strip()
    if xdg:
        base = Path(xdg).expanduser()
    else:
        base = Path(_env.get("HOME") or "~").expanduser() / ".local" / "share"
    return base / "matt-loop" / "learning"


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def project_next_skill(raw):
    """闭集宽容投影（ADR-0008 §4）。返回 (skill, invalid)。

    显式 ""＝合法弃答；七项之一＝合法预测。缺失（None）、非字符串、带斜杠、带参数、
    集外值均投影为 ""，但标 invalid=True——学习阶段另记 invalid_prediction，不得把
    格式错误算作正确弃答。形状门不检查本字段，投影不整轮报错。
    """
    if isinstance(raw, str):
        if raw == "":
            return "", False
        if raw in NEXT_SKILL_CANDIDATES:
            return raw, False
    return "", True


def parse_actual_skill(first_line):
    """机械解析主任务首个非空行 → (actual_skill, actual_type)。

    actual_type ∈ candidate（七项之一）/ other_command（集外斜杠命令）/ plain_text（无斜杠）。
    candidate 与 other_command 都记命令名（去斜杠）；plain_text 的 actual_skill 为 None。
    重置/切模型前置不进这里（dispatch 只对主任务首行调用）。不保存参数或首行全文。
    """
    m = _SLASH_RE.match(first_line or "")
    if not m:
        return None, "plain_text"
    name = m.group(1)
    if name in NEXT_SKILL_CANDIDATES:
        return name, "candidate"
    return name, "other_command"


def classify(predicted, invalid, actual_skill, actual_type):
    """机械判分（ADR-0012 §2）。返回 verdict ∈ hit / miss / correct_abstain。

    实际属七项：合法非空且同 skill 才 hit，空与无效均 miss。
    实际非七项：合法空预测才 correct_abstain；无效预测不算正确弃答（miss）。
    """
    if actual_type == "candidate":
        if not invalid and predicted and predicted == actual_skill:
            return "hit"
        return "miss"
    if not invalid and predicted == "":
        return "correct_abstain"
    return "miss"


def _new_counters():
    return {"settled": 0, "candidate_actual": 0, "hit": 0, "covered": 0,
            "noncandidate_actual": 0, "correct_abstain": 0, "invalid": 0,
            "sequence_used": 0}


def _bump(counters, sample):
    counters["settled"] += 1
    if sample["actual_type"] == "candidate":
        counters["candidate_actual"] += 1
        if sample["verdict"] == "hit":
            counters["hit"] += 1
        if sample["covered"]:
            counters["covered"] += 1
    else:
        counters["noncandidate_actual"] += 1
        if sample["verdict"] == "correct_abstain":
            counters["correct_abstain"] += 1
    if sample["invalid"]:
        counters["invalid"] += 1
    if sample.get("sequence_used"):
        counters["sequence_used"] += 1  # 近期序列使用情况另记（ADR-0012 §2）


def _rates(counters):
    """由计数算三项指标（分母为 0 给 None，不宣称因果、不设硬门槛）。"""
    ca = counters["candidate_actual"]
    nca = counters["noncandidate_actual"]
    return {
        "samples": counters["settled"],
        "candidate_actual": ca,
        "noncandidate_actual": nca,
        "hit": counters["hit"],
        "covered": counters["covered"],
        "correct_abstain": counters["correct_abstain"],
        "invalid_prediction": counters["invalid"],
        "sequence_used": counters.get("sequence_used", 0),
        "skill_hit_rate": (counters["hit"] / ca) if ca else None,
        "coverage": (counters["covered"] / ca) if ca else None,
        "empty_correct_rate": (counters["correct_abstain"] / nca) if nca else None,
    }


def _stats_projection(stats):
    """统计三面投影：全量＋最近 20 窗口（恒带样本数）＋两队列。

    结算反馈与审计读模型（read_stats）共用同一份投影，避免两处各写一遍。
    """
    return {
        "total": _rates(stats["total"]),
        "recent20": _rates(_window_counters(stats["recent"])),
        "cohorts": {k: _rates(v) for k, v in stats["cohorts"].items()},
    }


def _warn(reason, detail):
    return {"reason": reason, "detail": detail}


class _LearningError(Exception):
    """内部降级信号：锁/超时/权限/I/O/损坏，统一转 learning_warning，不外抛。"""

    def __init__(self, reason, detail):
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


class LearningStore:
    """本地学习存储的短临界区事务集合。每个公开方法自捕异常、降级 warning，绝不外抛。"""

    def __init__(self, directory=None, lock_timeout=LOCK_TIMEOUT_S, now=None):
        self.dir = Path(directory) if directory is not None else learning_dir()
        self.lock_timeout = lock_timeout
        # 事件时间戳经注入（ADR-0023 决定 3）：默认现行 now_iso；锁等待仍是真实时间。
        self._now = now or now_iso
        self.cases_path = self.dir / "cases.jsonl"
        self.pending_path = self.dir / "pending.json"
        self.sequences_path = self.dir / "sequences.json"
        self.stats_path = self.dir / "stats.json"

    # ---- 锁与文件 I/O（短临界区，不跨抽取调用）----

    @contextlib.contextmanager
    def _locked(self):
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            os.chmod(self.dir, 0o700)
        except OSError as e:
            raise _LearningError("io_error", f"学习目录不可写：{type(e).__name__}")
        lock_path = self.dir / ".lock"
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        except OSError as e:
            raise _LearningError("io_error", f"锁文件不可建：{type(e).__name__}")
        try:
            deadline = time.monotonic() + self.lock_timeout
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise _LearningError("lock_timeout", "学习锁等待超时")
                    time.sleep(0.05)
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(fd)

    def _read_json(self, path, default):
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise _LearningError("corrupt_record", f"{path.name} 损坏：{type(e).__name__}")

    def _atomic_write(self, path, text, label=None):
        """tmp→chmod 0600→replace 的原子写；失败统一降级 io_error。

        label 只用于告警文案（cases 的既有文案不带扩展名）；缺省取文件名。
        """
        tmp = path.with_suffix(path.suffix + ".tmp")
        try:
            fd = os.open(tmp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
            os.chmod(tmp, 0o600)
            tmp.replace(path)
        except OSError as e:
            raise _LearningError(
                "io_error", f"写 {label or path.name} 失败：{type(e).__name__}")

    def _write_json(self, path, obj):
        self._atomic_write(path, json.dumps(obj, ensure_ascii=False) + "\n")

    def _read_cases(self):
        if not self.cases_path.exists():
            return []
        cases = []
        try:
            text = self.cases_path.read_text(encoding="utf-8")
        except OSError as e:
            raise _LearningError("io_error", f"读 cases 失败：{type(e).__name__}")
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue  # 单行损坏跳过（机械宽容），整文件不可读才降级
            if isinstance(obj, dict):
                cases.append(obj)
        return cases

    def _write_cases(self, cases):
        self._atomic_write(
            self.cases_path,
            "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases),
            label="cases")

    # ---- 保留期清理（正常读写触发，无后台进程）----

    def _prune(self, cases, pending, sequences):
        cutoff = (datetime.now(timezone.utc)
                  - timedelta(days=RETENTION_DAYS)).isoformat(timespec="seconds")

        def cap_by_space(items):
            items = [it for it in items if (it.get("ts") or "") >= cutoff]
            by_space = {}
            for it in items:
                by_space.setdefault(it.get("space_id"), []).append(it)
            kept = []
            for lst in by_space.values():
                lst.sort(key=lambda it: it.get("ts") or "")
                kept.extend(lst[-PER_SPACE_CAP:])
            return kept

        cases = cap_by_space(cases)
        pending = cap_by_space(pending)
        for sp in list(sequences):
            for tg in list(sequences[sp]):
                events = [e for e in sequences[sp][tg] if (e.get("ts") or "") >= cutoff]
                if events:
                    sequences[sp][tg] = events[-SEQUENCE_CAP:]
                else:
                    del sequences[sp][tg]
            if not sequences[sp]:
                del sequences[sp]
        return cases, pending, sequences

    def _load_all(self):
        cases = self._read_cases()
        pending = self._read_json(self.pending_path, [])
        sequences = self._read_json(self.sequences_path, {})
        stats = self._read_json(self.stats_path, {})
        if not isinstance(pending, list):
            raise _LearningError("corrupt_record", "pending.json 形状损坏")
        if not isinstance(sequences, dict):
            raise _LearningError("corrupt_record", "sequences.json 形状损坏")
        if not isinstance(stats, dict):
            raise _LearningError("corrupt_record", "stats.json 形状损坏")
        stats.setdefault("total", _new_counters())
        stats.setdefault("recent", [])
        stats.setdefault("cohorts", {"examples_0": _new_counters(),
                                     "examples_gt0": _new_counters()})
        return cases, pending, sequences, stats

    def _save_all(self, cases, pending, sequences, stats):
        cases, pending, sequences = self._prune(cases, pending, sequences)
        self._write_cases(cases)
        self._write_json(self.pending_path, pending)
        self._write_json(self.sequences_path, sequences)
        self._write_json(self.stats_path, stats)

    # ---- 公开事务 ----

    def settle_and_record(self, target, release_id, space_id, first_line):
        """主任务发送成功后立即调用：记近期序列＋结算上一轮同 target 预测＋更新统计。

        发送前预检失败不会到这里（不消耗待观察）。返回 {feedback, warning}；无上一轮
        预测时 feedback 为 None。space_id 缺失时禁用本轮 space  scoped 学习（序列/案例/
        few-shot）并告警，但 target 维度的机械反馈仍结算。
        """
        try:
            with self._locked():
                cases, pending, sequences, stats = self._load_all()
                actual_skill, actual_type = parse_actual_skill(first_line)
                warning = None
                if space_id:
                    seq = sequences.setdefault(space_id, {}).setdefault(target, [])
                    if not any(e.get("release_id") == release_id for e in seq):
                        skill = actual_skill if actual_type != "plain_text" else "plain_text"
                        seq.append({"skill": skill, "release_id": release_id,
                                    "ts": self._now()})
                        # 截断统一由 _save_all → _prune 按 SEQUENCE_CAP 做。
                else:
                    warning = _warn("space_id_missing", "space id 缺失，本轮序列/案例学习禁用")

                feedback = None
                mine = [p for p in pending
                        if p.get("target") == target and not p.get("orphaned")]
                if mine:
                    mine.sort(key=lambda p: p.get("ts") or "")
                    newest = mine[-1]
                    for older in mine[:-1]:
                        older["orphaned"] = True  # 异常多条只对齐最近一条
                    pending.remove(newest)
                    feedback = self._settle_one(cases, stats, newest, target,
                                                release_id, space_id,
                                                actual_skill, actual_type)
                self._save_all(cases, pending, sequences, stats)
                return {"feedback": feedback, "warning": warning}
        except _LearningError as e:
            return {"feedback": None, "warning": _warn(e.reason, e.detail)}
        except Exception as e:  # 兜底：任何未预期异常也不连累主轮
            return {"feedback": None, "warning": _warn("io_error", type(e).__name__)}

    def _settle_one(self, cases, stats, pred, target, release_id, space_id,
                    actual_skill, actual_type):
        predicted = pred.get("next_skill", "")
        invalid = bool(pred.get("invalid"))
        verdict = classify(predicted, invalid, actual_skill, actual_type)
        covered = (not invalid) and predicted != ""
        sample = {"verdict": verdict, "invalid": invalid, "covered": covered,
                  "actual_type": actual_type,
                  "examples_used": pred.get("examples_used", 0),
                  "sequence_used": bool(pred.get("sequence_used"))}
        _bump(stats["total"], sample)
        cohort = "examples_0" if pred.get("examples_used", 0) == 0 else "examples_gt0"
        _bump(stats["cohorts"].setdefault(cohort, _new_counters()), sample)
        stats["recent"].append({
            "ts": self._now(), "verdict": verdict, "invalid": invalid, "covered": covered,
            "actual_type": actual_type, "examples_used": pred.get("examples_used", 0),
            "sequence_used": bool(pred.get("sequence_used")),
        })
        stats["recent"] = stats["recent"][-RECENT_WINDOW:]

        cross_space = bool(space_id and pred.get("space_id")
                           and space_id != pred.get("space_id"))
        # 案例入池的排除原因（两处共用同一判定）：跨 space 先于折叠标记。
        exclusion = "space_changed" if cross_space else (
            "incomplete_turn" if pred.get("complete") is False else None)
        feedback = {
            "prev_release_id": pred.get("release_id"),
            "predicted_skill": predicted,
            "predicted_invalid": invalid,
            "actual_skill": actual_skill,
            "actual_type": actual_type,
            "verdict": verdict,
        }
        if exclusion:
            feedback["learning_excluded"] = exclusion
        # 案例：space id 缺失时不入池（无法确认隔离）；跨 space 或折叠标记轮入池但标 excluded。
        if space_id:
            case = {
                "target": target,
                "space_id": pred.get("space_id"),
                "predict_release_id": pred.get("release_id"),
                "actual_release_id": release_id,
                "ts": self._now(),
                "verdict": verdict,
                "invalid": invalid,
                "reply": pred.get("reply", ""),
                "next_skill": predicted,
                "actual_skill": actual_skill,
                "actual_type": actual_type,
                "sequence": pred.get("sequence", []),
            }
            if exclusion:
                case["learning_excluded"] = exclusion
            if not any(c.get("predict_release_id") == case["predict_release_id"]
                       and c.get("actual_release_id") == release_id for c in cases):
                cases.append(case)
        # 统计三面随反馈呈出（ADR-0012 §2）：全量＋最近 20 窗口（恒带样本数）＋两队列。
        # 只含计数，不含案例正文（案例正文绝不进 result/webhook）。
        feedback["stats"] = _stats_projection(stats)
        return feedback

    def build_injection(self, space_id, target):
        """抽取前调用：选 ≤4 条同 space 案例＋当前近期序列，拼注入文本。

        返回 {text, examples_used, sequence_used, sequence, warning}。无历史时 text 为空，
        完全沿用原 prompt（冷启动行为不变）。锁在本事务内即释放，不跨随后的抽取调用。
        """
        try:
            if not space_id:
                return {"text": "", "examples_used": 0, "sequence_used": False,
                        "sequence": [], "warning": _warn("space_id_missing",
                                                         "space id 缺失，few-shot 禁用")}
            with self._locked():
                cases = self._read_cases()
                sequences = self._read_json(self.sequences_path, {})
                seq = sequences.get(space_id, {}).get(target, [])
                seq_skills = [e.get("skill") for e in seq]
                selected = self._select(cases, space_id, target)
                # 冷启动护栏（ADR-0012 §4.3）：序列含本轮，仅 1 条＝无前轮历史，注入它只会
                # 改动 prompt 而无预测信息——故只有 ≥2 条（至少一个前轮）才注入序列块。
                # 快照仍存完整序列（含本轮）供案例；sequence_used 记是否真的注入。
                show_seq = seq_skills if len(seq_skills) >= 2 else []
                text = _render(selected, show_seq)
                return {"text": text, "examples_used": len(selected),
                        "sequence_used": bool(show_seq), "sequence": seq_skills,
                        "warning": None}
        except _LearningError as e:
            return {"text": "", "examples_used": 0, "sequence_used": False,
                    "sequence": [], "warning": _warn(e.reason, e.detail)}
        except Exception as e:
            return {"text": "", "examples_used": 0, "sequence_used": False,
                    "sequence": [], "warning": _warn("io_error", type(e).__name__)}

    def _select(self, cases, space_id, target):
        pool = [c for c in cases
                if c.get("space_id") == space_id
                and not c.get("learning_excluded")
                and not c.get("invalid")]
        corrections = [c for c in pool if c.get("verdict") == "miss"]
        others = [c for c in pool if c.get("verdict") in ("hit", "correct_abstain")]

        def key(c):
            # reverse=True 下大者靠前：同 target 给 1（优先）、时间戳大者（较新）靠前。
            return (1 if c.get("target") == target else 0, c.get("ts") or "")

        # 同 target 优先、时间倒序；优先最近纠错，补足命中与正确弃答。
        corrections.sort(key=key, reverse=True)
        others.sort(key=key, reverse=True)
        selected = []
        budget = REPLY_BUDGET
        for c in corrections + others:
            if len(selected) >= MAX_EXAMPLES:
                break
            reply = c.get("reply", "")
            if len(reply) <= budget:  # 超限少选，不截断单条正文
                selected.append(c)
                budget -= len(reply)
        return selected

    def record_prediction(self, target, release_id, space_id, next_skill, invalid,
                          reply, sequence, examples_used, sequence_used, complete=True):
        """成功抽取后调用：创建待观察记录（含空预测）。以预测轮放行 id 去重。

        complete=False（折叠标记轮，ADR-0019）照创建待观察，旗标随 pending；结算时
        案例标 learning_excluded=incomplete_turn 不入 few-shot，反馈照旧结算。

        space id 缺失时禁用本轮学习（ADR-0012）：不持久化待观察（干净回复不入存储），
        返回 space_id_missing 告警。target 维度的上一轮反馈结算不受影响（见 settle）。
        """
        try:
            if not space_id:
                return {"warning": _warn("space_id_missing",
                                         "space id 缺失，本轮不创建待观察")}
            with self._locked():
                cases, pending, sequences, stats = self._load_all()
                if not any(p.get("release_id") == release_id for p in pending):
                    pending.append({
                        "target": target, "release_id": release_id,
                        "space_id": space_id, "next_skill": next_skill,
                        "invalid": bool(invalid), "reply": reply,
                        "sequence": list(sequence or []),
                        "examples_used": examples_used,
                        "sequence_used": bool(sequence_used), "ts": self._now(),
                        "complete": bool(complete),
                    })
                self._save_all(cases, pending, sequences, stats)
                return {"warning": None}
        except _LearningError as e:
            return {"warning": _warn(e.reason, e.detail)}
        except Exception as e:
            return {"warning": _warn("io_error", type(e).__name__)}

    def read_stats(self):
        """读取统计快照（测试/审计用）：全量＋最近 20 窗口＋两队列。"""
        try:
            with self._locked():
                stats = self._read_json(self.stats_path, {})
                stats.setdefault("total", _new_counters())
                stats.setdefault("recent", [])
                stats.setdefault("cohorts", {"examples_0": _new_counters(),
                                             "examples_gt0": _new_counters()})
                return _stats_projection(stats)
        except Exception:
            return {"total": _rates(_new_counters()),
                    "recent20": _rates(_new_counters()), "cohorts": {}}


# ---------------------------------------------------------------- 学习会话（ADR-0023）


# `begin` 拿不到 space id 时的唯一文案（与基线 main 内联的那条逐字一致，ADR-0023 决定 5）。
_SPACE_ID_MISSING = {
    "reason": "space_id_missing",
    "detail": "--space 未能只读解析出唯一 space id，本轮序列/案例/few-shot 禁用",
}


class LearningSession:
    """一轮学习接入的四个动词（ADR-0023 决定 1/2/6）：`LearningStore` 之上的一层薄包装。

    新增的只有三件事：持有 space id、持有注入元数据、把事务结果搬进 `fact`。事务逻辑与
    存储格式不动；动词永不外抛（吸收仍在 `LearningStore` 的每方法 catch-all 里），也
    不返回 feedback / warning——只在值非空时写 `fact(..., ledger=False)`，后写覆盖先写。
    会话一轮一个、不复用。
    """

    def __init__(self, store, fact, target, release_id):
        self._store = store
        self._fact = fact
        self._target = target
        self._release_id = release_id
        self.space_id = None
        # inject() 存下的注入元数据（决定 6）；未调用或失败时取现状初值。
        self.sequence = []
        self.examples_used = 0
        self.sequence_used = False

    def begin(self, resolve_space):
        """解析 space id；回调返回 None 时写 space_id_missing 并记 None。"""
        self.space_id = resolve_space()
        if self.space_id is None:
            self._warn(dict(_SPACE_ID_MISSING))

    def settle(self, first_line):
        """主任务发送成功后结算上一轮同 target 预测。"""
        result = self._store.settle_and_record(
            self._target, self._release_id, self.space_id, first_line)
        if result.get("feedback") is not None:
            self._fact("next_skill_feedback", result["feedback"], ledger=False)
        if result.get("warning") is not None:
            self._warn(result["warning"])

    def inject(self):
        """抽取前取注入文本；元数据存会话实例供 record 消费。"""
        result = self._store.build_injection(self.space_id, self._target)
        self.sequence = result.get("sequence", [])
        self.examples_used = result.get("examples_used", 0)
        self.sequence_used = result.get("sequence_used", False)
        if result.get("warning") is not None:
            self._warn(result["warning"])
        return result.get("text", "")

    def record(self, extraction, complete):
        """成功抽取后创建待观察（含空预测）：预测/无效标记/reply 取自 Extraction。"""
        result = self._store.record_prediction(
            self._target, self._release_id, self.space_id,
            extraction.fields.get("next_skill", ""), extraction.next_skill_invalid,
            extraction.reply, self.sequence, self.examples_used, self.sequence_used,
            complete=complete)
        if result.get("warning") is not None:
            self._warn(result["warning"])

    def _warn(self, warning):
        self._fact("learning_warning", warning, ledger=False)


class Disabled:
    """`LOOP_LEARNING=off` 的空对象（ADR-0023 决定 4）：四动词全是空操作。

    `begin` 不调回调，所以关闭时零 `ls`、零 `require_app`；`inject()` 返回 ""。
    与 `LearningSession` 不共享基类（鸭子类型），防漂移靠 parity 测试。
    """

    def begin(self, resolve_space):
        pass

    def settle(self, first_line):
        pass

    def inject(self):
        return ""

    def record(self, extraction, complete):
        pass


def open(env=None, now=None, fact=None, *, target, release_id):
    """构造一轮的学习接入（ADR-0023 决定 3/4）：off → `Disabled`，否则 `LearningSession`。

    env / now / fact 是三条注入缝（`deps.env` / `deps.now` / `state.fact`）；target 与
    release_id 是本轮的配对键（会话持有，四个动词不再各传一遍）。
    """
    if not enabled(env):
        return Disabled()
    return LearningSession(
        LearningStore(directory=learning_dir(env), now=now), fact, target, release_id)


def _window_counters(recent):
    c = _new_counters()
    for s in recent:
        _bump(c, s)
    return c


def _render(selected, seq_skills):
    """拼注入文本：历史近期序列＋历史干净回复＋当时预测＋实际 skill；当前近期序列。

    无案例且无序列时返回 ""（冷启动沿用原 prompt）。序列是紧凑元数据，不占回复预算。
    """
    if not selected and not seq_skills:
        return ""
    parts = []
    if seq_skills:
        parts.append("【当前近期 skill 序列】（同 space 同 target，oldest→newest，含本轮）\n"
                     + " → ".join(seq_skills))
    if selected:
        lines = ["【历史参考案例】（同 space 历史轮，仅供 next_skill 预测参考，"
                 "不覆盖当前回复与当前近期序列）"]
        for i, c in enumerate(selected, 1):
            seq = c.get("sequence") or []
            actual = c.get("actual_skill")
            actual_desc = actual if c.get("actual_type") == "candidate" else (
                "plain_text" if c.get("actual_type") == "plain_text" else (actual or ""))
            lines.append(f"案例 {i}（{c.get('verdict')}）：")
            if seq:
                lines.append("历史近期序列：" + " → ".join(seq))
            lines.append("历史干净回复：\n" + c.get("reply", ""))
            lines.append(f"当时预测：{c.get('next_skill') or '（空）'}｜实际 skill："
                         f"{actual_desc}（{c.get('actual_type')}）")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)
