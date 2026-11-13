"""打印机后端抽象：能力描述、状态模型、统一接口。

新增机型只需：

1. 在 ``gongfeng_print/printers/`` 下实现 :class:`PrinterBackend` 的子类；
2. 用 ``registry.register("model-id", factory)`` 注册（或类装饰器）；
3. 在 ``printers/__init__.py`` 里 import 该模块触发注册。

上层（HTTP API / 渲染 / 并发锁）只依赖这里的接口，不感知具体传输与指令集。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar

from PIL import Image


class BackendError(RuntimeError):
    """打印操作失败（设备离线、状态异常、指令不支持等）。"""


class UnknownModelError(BackendError):
    """请求了未注册的机型。"""

    def __init__(self, model_id: str, supported: list[str]):
        self.model_id = model_id
        self.supported = supported
        super().__init__(
            f"不支持的机型 {model_id!r}；当前已注册：{', '.join(supported) or '（无）'}")


class UnsupportedOperationError(BackendError):
    """该机型不支持某个操作（例如无测纸能力）。"""


# ------------------------------------------------------------- 能力
@dataclass(frozen=True)
class Capabilities:
    """机型的静态能力描述（GET /api/models 直接返回）。"""

    model_id: str
    name: str
    vendor: str = "HPRT 汉印"
    dpi: int = 203
    head_dots: int = 384                 # 打印头点数
    max_width_mm: float = 50.0           # 支持的最大标签纸宽
    printable_width_mm: float = 48.0     # 打印头实际可打印宽度
    max_height_mm: float = 300.0
    default_offset_x_mm: float = 0.0     # 默认纸卷偏移（打印头坐标中的内容左偏移）
    max_offset_x_mm: float = 20.0        # 允许的最大纸卷偏移
    default_label: dict[str, float] = field(
        default_factory=lambda: {"width_mm": 40.0, "height_mm": 30.0})
    densities: tuple[int, ...] = (35, 55, 75, 95)
    aligns: tuple[str, ...] = ("left", "center", "right")
    supports_learn: bool = True          # 测纸/定位
    supports_status: bool = True
    supports_copies: bool = True
    supports_form_feed: bool = True      # 打完走纸到撕纸位
    supports_border: bool = True
    connection: str = "bluetooth-spp"
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "name": self.name,
            "vendor": self.vendor,
            "dpi": self.dpi,
            "head_dots": self.head_dots,
            "max_width_mm": self.max_width_mm,
            "printable_width_mm": self.printable_width_mm,
            "max_height_mm": self.max_height_mm,
            "default_offset_x_mm": self.default_offset_x_mm,
            "max_offset_x_mm": self.max_offset_x_mm,
            "default_label": dict(self.default_label),
            "densities": list(self.densities),
            "aligns": list(self.aligns),
            "capabilities": {
                "learn": self.supports_learn,
                "status": self.supports_status,
                "copies": self.supports_copies,
                "form_feed": self.supports_form_feed,
                "border": self.supports_border,
            },
            "connection": self.connection,
            "notes": list(self.notes),
        }


# ------------------------------------------------------------- 状态
STATUS_BIT_NAMES: dict[int, str] = {
    0: "缺纸", 1: "过热", 2: "过冷", 3: "低电", 4: "开盖", 5: "低压", 6: "定位失败",
}


@dataclass
class PrinterStatus:
    """一次状态查询的结果（跨机型通用）。"""

    value: int = -1
    errors: tuple[str, ...] = ("无响应",)
    raw: bytes = b""
    printing: bool = False
    pooli_sta: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.value >= 0 and not self.errors

    @property
    def locate_fail(self) -> bool:
        return bool(self.value >= 0 and (self.value & (1 << 6)))

    @property
    def summary(self) -> str:
        if self.value < 0:
            return "无响应"
        if not self.errors:
            return "正常"
        return ",".join(self.errors)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "status": self.summary,
            "value": self.value,
            "value_hex": f"0x{self.value:04x}" if self.value >= 0 else None,
            "errors": list(self.errors),
            "printing": self.printing,
            "pooli_sta": self.pooli_sta,
            "raw_hex": self.raw.hex(" "),
            **({"extra": self.extra} if self.extra else {}),
        }


# ------------------------------------------------------------- 后端接口
class PrinterBackend(ABC):
    """所有机型后端的统一接口。

    生命周期：``connect()`` -> ``status()/learn()/print_image()`` -> ``close()``。
    连接是显式、短时的：HTTP 层对每个请求 connect/close，并用进程内互斥锁串行化，
    避免多个请求同时占用同一台蓝牙打印机。
    """

    capabilities: ClassVar[Capabilities]

    @property
    def model_id(self) -> str:
        return self.capabilities.model_id

    @abstractmethod
    def connect(self, addr: str | None = None) -> "PrinterBackend":
        """连接设备（幂等）。失败抛 :class:`BackendError`。"""

    @abstractmethod
    def status(self, **kwargs) -> PrinterStatus:
        """查询打印机状态。"""

    @abstractmethod
    def learn(self, **kwargs) -> PrinterStatus:
        """测纸/定位（会走纸）。"""

    @abstractmethod
    def print_image(self, image: Image.Image, *,
                    copies: int = 1, density: int = 75, align: str = "left",
                    auto_learn: bool = True, form_feed: bool = False) -> PrinterStatus:
        """打印一张已渲染好的位图（调用前需 connect）。返回打印完成后的状态。"""

    @abstractmethod
    def close(self) -> None:
        """关闭连接（幂等，不抛异常）。"""

    def __enter__(self) -> "PrinterBackend":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()
