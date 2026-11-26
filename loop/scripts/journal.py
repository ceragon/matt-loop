#!/usr/bin/env python3
"""journal.py — 台账文件的唯一写入口（ADR-0022 决定 1–5）。

`Journal` 只管磁盘上的过程记录（`runs/<放行id>.json`）：五个方法
`open(initial)`、`phase(name, **fields)`、`fact(key, value)`、`get(key, default)`、
`fail(kind, message)`。不 import 任何兄弟模块、不认识 herdr／学习／结果载荷；
墙钟经构造注入（`Deps.now`），只有 `fail` 自己盖 `failed_at`。

语义（ADR-0022）：
  - `open` 是消耗放行 id 的唯一动作：`runs` 目录在 open 里建、首版落盘、只调一次；
    open 之前整轮零写入。open 前的 `fact` 不写盘（facts 缓冲在 `RunState`）；
    open 前的 `phase` 是编程错误，抛 `RuntimeError`（不是 `DispatchError`）。
  - `phase`／`fact` 每次调用一次原子落盘，`OSError` 原样冒出（中途写失败由 `run`
    顶层映射 `io_failed`，ADR-0024）；`fail` 是唯一的尽力而为写，内部吞 `OSError`。
  - `get` 带 `default` 且台账未开时恒返回它；调用方用哨兵区分「缺键」与合法假值
    （`complete=False`）。
"""

import json
from pathlib import Path


def write_json_atomic(path, obj):
    """`xxx.tmp` → `replace`；原样抛 `OSError`（ADR-0024 零改动）。"""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(path)


class Journal:
    """一份台账文件的状态与写入（构造后先 `open(initial)`，再 `phase`／`fact`）。"""

    def __init__(self, path, now):
        self.path = Path(path)
        self._now = now
        self._data = {}
        self._opened = False

    @property
    def opened(self):
        return self._opened

    def open(self, initial):
        """建 runs 目录并写首版；唯一一次。调用即消耗放行 id。"""
        if self._opened:
            raise RuntimeError("Journal.open 只能调用一次（放行 id 已消耗）")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data = dict(initial)
        write_json_atomic(self.path, self._data)
        self._opened = True

    def phase(self, name, **fields):
        """写 phase（name=None 只补字段、不改 phase）与字段并原子落盘。"""
        if not self._opened:
            raise RuntimeError("Journal.phase 在 open 之前调用：编程错误")
        if name is not None:
            self._data["phase"] = name
        self._data.update(fields)
        write_json_atomic(self.path, self._data)

    def fact(self, key, value):
        """写一条事实并原子落盘；open 之前不写（facts 缓冲在 RunState）。"""
        if not self._opened:
            return
        self._data[key] = value
        write_json_atomic(self.path, self._data)

    def get(self, key, default=None):
        """读一条事实；缺键或未开时返回 default。"""
        if not self._opened:
            return default
        return self._data.get(key, default)

    def fail(self, kind, message):
        """写 error:<kind> 终态（含 failed_at），尽力而为：吞 OSError；未开不写。"""
        if not self._opened:
            return
        try:
            self.phase(f"error:{kind}",
                       error={"kind": kind, "message": message},
                       failed_at=self._now())
        except OSError:
            pass
