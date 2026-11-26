#!/usr/bin/env python3
"""errors.py — 分发脚本的底层错误基类（ADR-0020 决定 10）。

只放 `DispatchError`：网关失败类（`HerdrFailure`）要继承它，而网关模块要被
`dispatch.py` 引用，沉到独立模块才不成环。本模块不 import 任何兄弟模块。
"""


class DispatchError(Exception):
    def __init__(self, kind, message, hints=None, returncode=1):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.hints = hints or []
        self.returncode = returncode
