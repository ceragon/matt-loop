#!/usr/bin/env python3
"""Task name generation: assert the generated name's shape and length only.

任务名由本机自生成，这里直接断言生成结果的形态（loop- + 8 位小写十六进制）
与长度上限，不导入任务名校验函数或只服务它的正则。
"""
import re
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "loop" / "scripts"))
from dispatch import generate_short_task_name

# 期望形态（独立于生成实现）：hc 任务名允许 a-z0-9._-，生成本身只用这 8 位十六进制。
NAME_SHAPE = re.compile(r"^loop-[0-9a-f]{8}$")
NAME_MAX = 32


def test_generate_short():
    name = generate_short_task_name("hex727-prototype-20261120-0820")
    assert name == "loop-b6c44ba9", name
    assert len(name) <= NAME_MAX, name
    assert NAME_SHAPE.match(name), name
    print("generate_short_task_name ok:", name)


def test_long_release_id_yields_short_legal_name():
    # 放行 id 再长，生成的任务名都必须是短且合法的形态。
    name = generate_short_task_name("x" * 200)
    assert len(name) <= NAME_MAX, name
    assert NAME_SHAPE.match(name), name
    print("long release id still yields short legal name ok:", name)


if __name__ == "__main__":
    test_generate_short()
    test_long_release_id_yields_short_legal_name()
    print("all tests passed")
