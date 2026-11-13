"""打印机后端包：抽象基类 + 注册表 + 各机型实现。

导入本包即完成内置机型注册；新增机型在这里 import 一行即可。
"""
from .base import (
    STATUS_BIT_NAMES,
    BackendError,
    Capabilities,
    PrinterBackend,
    PrinterStatus,
    UnknownModelError,
    UnsupportedOperationError,
)
from .registry import (
    DEFAULT_MODEL_ID,
    capabilities_of,
    create,
    is_registered,
    list_models,
    models_payload,
    register,
    register_backend,
    registered_models,
)

# --- 内置机型 ---
from .hprt_m1 import HprtM1Backend  # noqa: E402  (需要在 register 之后导入)

register(HprtM1Backend.capabilities.model_id, HprtM1Backend)

__all__ = [
    "STATUS_BIT_NAMES",
    "BackendError",
    "Capabilities",
    "DEFAULT_MODEL_ID",
    "HprtM1Backend",
    "PrinterBackend",
    "PrinterStatus",
    "UnknownModelError",
    "UnsupportedOperationError",
    "capabilities_of",
    "create",
    "is_registered",
    "list_models",
    "models_payload",
    "register",
    "register_backend",
    "registered_models",
]
