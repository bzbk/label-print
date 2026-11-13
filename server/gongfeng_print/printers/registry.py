"""机型注册表：model_id -> 后端工厂。

用法::

    from .registry import register, create, list_models
    from .hprt_m1 import HprtM1Backend

    register("hprt-m1", HprtM1Backend)      # 类本身可作工厂
    backend = create("hprt-m1")             # 实例化（不连接）

新增机型不修改本文件，只需在 ``printers/__init__.py`` 里 import 新模块。
"""
from __future__ import annotations

from typing import Callable

from .base import BackendError, Capabilities, PrinterBackend, UnknownModelError

BackendFactory = Callable[[], PrinterBackend]

_factories: dict[str, BackendFactory] = {}
DEFAULT_MODEL_ID = "hprt-m1"


def register(model_id: str, factory: BackendFactory) -> BackendFactory:
    """注册机型工厂；重复注册会覆盖（便于测试注入 fake backend）。"""
    key = model_id.strip().lower()
    if not key:
        raise ValueError("model_id 不能为空")
    _factories[key] = factory
    return factory


def register_backend(model_id: str):
    """类装饰器：``@register_backend("hprt-m1")``。"""

    def deco(cls):
        register(model_id, cls)
        return cls

    return deco


def registered_models() -> list[str]:
    return sorted(_factories)


def is_registered(model_id: str | None) -> bool:
    return bool(model_id) and model_id.strip().lower() in _factories


def create(model_id: str | None = None, **kwargs) -> PrinterBackend:
    """实例化指定机型后端（优先用 kwargs 传参）；未知机型抛 UnknownModelError。"""
    key = (model_id or DEFAULT_MODEL_ID).strip().lower()
    factory = _factories.get(key)
    if factory is None:
        raise UnknownModelError(key, registered_models())
    try:
        return factory(**kwargs) if kwargs else factory()
    except TypeError:
        # 工厂可能是不接受 kwargs 的类/闭包
        return factory()


def capabilities_of(model_id: str | None = None) -> Capabilities:
    return create(model_id).capabilities


def list_models() -> list[Capabilities]:
    caps: list[Capabilities] = []
    for model_id in registered_models():
        try:
            caps.append(create(model_id).capabilities)
        except BackendError:
            continue
    return caps


def models_payload() -> dict:
    caps = list_models()
    return {
        "ok": True,
        "default_model": DEFAULT_MODEL_ID,
        "count": len(caps),
        "models": [c.to_dict() for c in caps],
        "spec_defaults": {
            "width_mm": 40.0,
            "height_mm": 30.0,
            "offset_x_mm": 10.0,
            "border": False,
            "density": 75,
            "align": "left",
            "copies": 1,
        },
        "item_types": ["text", "barcode", "qr", "gap"],
    }
