"""``python3 -m gongfeng_print`` 等价于 ``python3 -m gongfeng_print.server``。"""
from __future__ import annotations

from .server import main

if __name__ == "__main__":
    raise SystemExit(main())
