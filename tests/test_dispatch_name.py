#!/usr/bin/env python3
"""Minimal unit tests for task name generation. Do not call real agents."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "loop" / "scripts"))
from dispatch import generate_short_task_name, validate_task_name, TASK_NAME_RE

def test_generate_short():
    name = generate_short_task_name("hex727-prototype-20261120-0820")
    assert len(name) <= 32
    assert TASK_NAME_RE.match(name)
    assert name.startswith("loop-")
    print("generate_short_task_name ok:", name)

def test_validate_rejects_long():
    long_name = "loop-hex727-prototype-20261120-0820"
    try:
        validate_task_name(long_name)
        raise AssertionError("should have rejected")
    except Exception as e:
        assert "bad_task_name" in str(e) or "DispatchError" in type(e).__name__
    print("validate rejects long name ok")

if __name__ == "__main__":
    test_generate_short()
    test_validate_rejects_long()
    print("all tests passed")
