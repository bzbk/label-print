"""标签 spec 契约：解析、校验与规范化。

与前端（ui/）严格一致的请求体::

    {
      "width_mm": 40, "height_mm": 30, "border": false,
      "density": 75, "align": "left", "copies": 1,
      "model": "hprt-m1",
      "items": [
        {"type": "text", "text": "物料 A", "size": 28, "bold": true,
         "align": "left", "x_mm": 2, "y_mm": 2},
        {"type": "barcode", "data": "ABC123", "symbology": "code128",
         "height_mm": 12, "x_mm": 2, "y_mm": 15},
        {"type": "qr", "data": "https://x", "size_mm": 14, "x_mm": 18, "y_mm": 14}
      ]
    }

* ``offset_x_mm`` 纸卷偏移：标签内容在打印头坐标里的左偏移（默认 10mm）。
  M1 纸筒挡板在左侧，40mm 卷筒相对打印头原点向右偏 10mm；50mm 纸用 0。
* ``x_mm`` / ``y_mm`` 可选：给出时按绝对坐标放置，缺省按纵向堆叠布局。
* 数值单位全部是毫米（``size`` 例外，它是 203dpi 像素字号）。
* 未知字段忽略，未知 ``type`` / 超范围数值返回明确的 :class:`SpecError`。

本模块不导入 Pillow，供路由层做快速校验。
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------- 常量
DEFAULT_WIDTH_MM = 40.0
DEFAULT_HEIGHT_MM = 30.0
DEFAULT_DENSITY = 75
DENSITIES = (35, 55, 75, 95)
ALIGNS = ("left", "center", "right")

MIN_WIDTH_MM, MAX_WIDTH_MM = 5.0, 72.0
MIN_HEIGHT_MM, MAX_HEIGHT_MM = 5.0, 300.0
DEFAULT_OFFSET_X_MM = 10.0
MIN_OFFSET_X_MM, MAX_OFFSET_X_MM = 0.0, 20.0
OFFSET_STEP_MM = 0.5
MAX_COPIES = 99
MAX_ITEMS = 200
MAX_SPEC_BYTES = 2 * 1024 * 1024  # 2 MiB，防止超大请求体

# zint 符号体系 -> zint barcode id / 类别。渲染层共用这张表。
SYMBOLOGIES: dict[str, dict[str, Any]] = {
    "code128": {"zint": 20, "linear": True},
    "code128b": {"zint": 20, "linear": True},
    "code128c": {"zint": 20, "linear": True},
    "code39": {"zint": 8, "linear": True},
    "code93": {"zint": 25, "linear": True},
    "ean13": {"zint": 13, "linear": True},
    "ean8": {"zint": 14, "linear": True},
    "upca": {"zint": 34, "linear": True},
    "upce": {"zint": 37, "linear": True},
    "itf14": {"zint": 42, "linear": True},
    "itf": {"zint": 41, "linear": True},
    "codabar": {"zint": 18, "linear": True},
    "pdf417": {"zint": 55, "linear": False},
    "datamatrix": {"zint": 71, "linear": False},
    "qr": {"zint": 58, "linear": False},
    "aztec": {"zint": 92, "linear": False},
    "hanxin": {"zint": 116, "linear": False},
}

ITEM_TYPES = ("text", "barcode", "qr", "gap")


class SpecError(ValueError):
    """标签 spec 不合法（HTTP 400）。"""


# ---------------------------------------------------------------- 小工具
def _as_bool(value: Any, name: str, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return bool(value)
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("true", "1", "yes", "on", ""):
            return low != ""
        if low in ("false", "0", "no", "off"):
            return False
    raise SpecError(f"{name} 必须是布尔值")


def _as_float(value: Any, name: str, default: float,
              lo: float | None = None, hi: float | None = None) -> float:
    if value is None:
        return default
    if isinstance(value, bool):
        raise SpecError(f"{name} 必须是数字")
    try:
        out = float(value)
    except (TypeError, ValueError):
        raise SpecError(f"{name} 必须是数字，收到 {value!r}") from None
    if math.isnan(out) or math.isinf(out):
        raise SpecError(f"{name} 必须是有限数字")
    if lo is not None and out < lo:
        raise SpecError(f"{name} 不能小于 {lo}，收到 {out}")
    if hi is not None and out > hi:
        raise SpecError(f"{name} 不能大于 {hi}，收到 {out}")
    return round(out, 3)


def _as_int(value: Any, name: str, default: int,
            lo: int | None = None, hi: int | None = None) -> int:
    f = _as_float(value, name, float(default), lo, hi)
    if abs(f - round(f)) > 1e-9:
        raise SpecError(f"{name} 必须是整数")
    return int(round(f))


def _as_str(value: Any, name: str, default: str = "", *, allow_empty: bool = True) -> str:
    if value is None:
        value = default
    if isinstance(value, (dict, list, tuple, set)):
        raise SpecError(f"{name} 必须是字符串")
    out = str(value)
    if not allow_empty and not out.strip():
        raise SpecError(f"{name} 不能为空")
    return out


def snap_density(value: Any) -> int:
    """把浓度吸附到 M1 支持的四档（35/55/75/95）。"""
    if value is None:
        return DEFAULT_DENSITY
    raw = _as_int(value, "density", DEFAULT_DENSITY, 1, 100)
    return min(DENSITIES, key=lambda d: (abs(d - raw), d))


def normalize_align(value: Any, default: str = "left") -> str:
    if value is None:
        return default
    align = str(value).strip().lower()
    if align not in ALIGNS:
        raise SpecError(f"align 必须是 {'/'.join(ALIGNS)} 之一，收到 {value!r}")
    return align


# ---------------------------------------------------------------- 数据模型
@dataclass
class LabelSpec:
    """规范化后的标签 spec。"""

    width_mm: float = DEFAULT_WIDTH_MM
    height_mm: float = DEFAULT_HEIGHT_MM
    offset_x_mm: float = DEFAULT_OFFSET_X_MM
    border: bool = False
    density: int = DEFAULT_DENSITY
    align: str = "left"
    copies: int = 1
    model: str | None = None
    auto_learn: bool = True
    form_feed: bool = False
    items: list[dict[str, Any]] = field(default_factory=list)

    @property
    def width_px(self) -> int:
        # 与 render.mm_to_px 一致（203 dpi），这里不引入 Pillow 依赖。
        return max(1, int(round(self.width_mm / 25.4 * 203)))

    @property
    def height_px(self) -> int:
        return max(1, int(round(self.height_mm / 25.4 * 203)))

    @property
    def offset_x_px(self) -> int:
        return max(0, int(round(self.offset_x_mm / 25.4 * 203)))

    @property
    def canvas_width_px(self) -> int:
        """送给打印机的位图宽度 = 纸卷偏移 + 标签宽度（像素）。"""
        return max(1, int(round((self.width_mm + self.offset_x_mm) / 25.4 * 203)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "width_mm": self.width_mm,
            "height_mm": self.height_mm,
            "width_px": self.width_px,
            "height_px": self.height_px,
            "offset_x_mm": self.offset_x_mm,
            "offset_x_px": self.offset_x_px,
            "canvas_width_px": self.canvas_width_px,
            "border": self.border,
            "density": self.density,
            "align": self.align,
            "copies": self.copies,
            "model": self.model,
            "auto_learn": self.auto_learn,
            "form_feed": self.form_feed,
            "items": self.items,
        }


# ---------------------------------------------------------------- 解析
def _parse_items(raw_items: Any) -> list[dict[str, Any]]:
    if raw_items is None:
        return []
    if not isinstance(raw_items, list):
        raise SpecError("items 必须是数组")
    if len(raw_items) > MAX_ITEMS:
        raise SpecError(f"items 数量不能超过 {MAX_ITEMS}，收到 {len(raw_items)}")

    items: list[dict[str, Any]] = []
    for idx, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            raise SpecError(f"items[{idx}] 必须是对象")
        kind = _as_str(raw.get("type"), f"items[{idx}].type", "text").strip().lower()
        if not kind:
            kind = "text"
        if kind not in ITEM_TYPES:
            raise SpecError(
                f"items[{idx}].type 不支持 {kind!r}，可用：{', '.join(ITEM_TYPES)}")

        item: dict[str, Any] = {"type": kind}
        for pos in ("x_mm", "y_mm"):
            if raw.get(pos) is not None:
                item[pos] = _as_float(raw.get(pos), f"items[{idx}].{pos}", 0.0,
                                      -100.0, 2000.0)

        if kind == "text":
            item["text"] = _as_str(raw.get("text"), f"items[{idx}].text", "")
            item["size"] = _as_int(raw.get("size"), f"items[{idx}].size", 24, 4, 400)
            item["bold"] = _as_bool(raw.get("bold"), f"items[{idx}].bold", False)
            item["align"] = normalize_align(raw.get("align"))
            if raw.get("spacing_mm") is not None:
                item["spacing_mm"] = _as_float(raw.get("spacing_mm"),
                                               f"items[{idx}].spacing_mm", 1.0, 0.0, 20.0)
        elif kind == "barcode":
            item["data"] = _as_str(raw.get("data"), f"items[{idx}].data",
                                   allow_empty=False)
            sym = _as_str(raw.get("symbology"), f"items[{idx}].symbology",
                          "code128").strip().lower()
            if sym not in SYMBOLOGIES:
                raise SpecError(
                    f"items[{idx}].symbology 不支持 {sym!r}，"
                    f"可用：{', '.join(sorted(SYMBOLOGIES))}")
            item["symbology"] = sym
            item["height_mm"] = _as_float(raw.get("height_mm"),
                                          f"items[{idx}].height_mm", 12.0, 3.0, 60.0)
            item["show_text"] = _as_bool(raw.get("show_text"),
                                         f"items[{idx}].show_text", True)
            if raw.get("width_mm") is not None:
                item["width_mm"] = _as_float(raw.get("width_mm"),
                                             f"items[{idx}].width_mm", 10.0, 5.0, 72.0)
            item["align"] = normalize_align(raw.get("align"),
                                            "left" if "x_mm" in item else "center")
        elif kind == "qr":
            item["data"] = _as_str(raw.get("data"), f"items[{idx}].data",
                                   allow_empty=False)
            item["size_mm"] = _as_float(raw.get("size_mm"),
                                        f"items[{idx}].size_mm", 14.0, 5.0, 60.0)
            item["align"] = normalize_align(raw.get("align"),
                                            "left" if "x_mm" in item else "center")
        elif kind == "gap":
            item["mm"] = _as_float(raw.get("mm"), f"items[{idx}].mm", 2.0, 0.0, 100.0)

        items.append(item)
    return items


def parse_spec(payload: Any) -> LabelSpec:
    """把请求体（dict 或 JSON 字符串）解析为 :class:`LabelSpec`。"""
    if payload is None:
        raise SpecError("请求体为空：需要标签 spec")
    if isinstance(payload, (bytes, bytearray)):
        try:
            payload = payload.decode("utf-8")
        except UnicodeDecodeError:
            raise SpecError("请求体不是 UTF-8 文本") from None
    if isinstance(payload, str):
        if not payload.strip():
            raise SpecError("请求体为空：需要标签 spec")
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise SpecError(f"请求体不是合法 JSON：{exc}") from None
    if not isinstance(payload, dict):
        raise SpecError("请求体必须是 JSON 对象")

    # 兼容 {"spec": {...}} 包装形式。
    if isinstance(payload.get("spec"), (dict, str)):
        return parse_spec(payload["spec"])

    width = _as_float(payload.get("width_mm"), "width_mm", DEFAULT_WIDTH_MM,
                      MIN_WIDTH_MM, MAX_WIDTH_MM)
    height = _as_float(payload.get("height_mm"), "height_mm", DEFAULT_HEIGHT_MM,
                       MIN_HEIGHT_MM, MAX_HEIGHT_MM)

    offset_x_mm = _as_float(payload.get("offset_x_mm"), "offset_x_mm",
                            DEFAULT_OFFSET_X_MM, MIN_OFFSET_X_MM, MAX_OFFSET_X_MM)
    offset_x_mm = round(offset_x_mm / OFFSET_STEP_MM) * OFFSET_STEP_MM
    offset_x_mm = round(min(MAX_OFFSET_X_MM, max(MIN_OFFSET_X_MM, offset_x_mm)), 3)

    model = payload.get("model")
    model = str(model).strip().lower() if isinstance(model, str) and model.strip() else None

    auto_learn = _as_bool(payload.get("no_auto_learn"), "no_auto_learn", False) is False
    if payload.get("auto_learn") is not None:
        auto_learn = _as_bool(payload.get("auto_learn"), "auto_learn", True)

    return LabelSpec(
        width_mm=width,
        height_mm=height,
        offset_x_mm=offset_x_mm,
        border=_as_bool(payload.get("border"), "border", False),
        density=snap_density(payload.get("density")),
        align=normalize_align(payload.get("align")),
        copies=_as_int(payload.get("copies"), "copies", 1, 1, MAX_COPIES),
        model=model,
        auto_learn=auto_learn,
        form_feed=_as_bool(payload.get("form_feed"), "form_feed", False),
        items=_parse_items(payload.get("items")),
    )
