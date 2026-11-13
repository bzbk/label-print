"""共风标签打印 —— 服务端核心包。

模块划分::

    spec.py             标签 spec 契约（解析/校验/规范化）
    render.py           渲染（Pillow + zint，毫米画布）
    transport/spp.py    蓝牙经典 SPP 传输（设备发现/握手/XOR 写入）
    printers/base.py    后端抽象：Capabilities / PrinterStatus / PrinterBackend
    printers/registry.py机型注册表
    printers/hprt_m1.py 汉印 M1（ESC/POLI）
    server.py           HTTP 服务（标准库 ThreadingHTTPServer）

启动::

    cd server
    python3 -m gongfeng_print.server --port 17600
"""
from __future__ import annotations

__version__ = "0.1.0"
APP_NAME = "gongfeng-label-server"
API_PREFIX = "/api"

__all__ = ["__version__", "APP_NAME", "API_PREFIX"]
