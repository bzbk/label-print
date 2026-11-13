#!/usr/bin/env python3
"""共风标签打印 —— HTTP API 服务（纯标准库 ``ThreadingHTTPServer``）。

启动::

    cd server
    python3 -m gongfeng_print.server --port 17600

监听默认 ``127.0.0.1:17600``，开启 CORS（默认放行本地 Vite 开发端口 5173/4173，
可用 ``--cors`` 追加来源）。所有接口在打印机不可用 / 参数错误时返回
``{"ok": false, "error": ...}``（HTTP 200，不抛 500），便于前端友好展示。

接口一览
--------
GET  /api/health                 服务健康检查
GET  /api/models                 支持的机型（能力/DPI/默认标签/浓度档）
GET  /api/devices                蓝牙设备列表（标记 M1）
GET  /api/status?model=&addr=    连接并查询打印机状态
GET  /api/preview?spec=<json>    渲染预览（也支持 width_mm/text/barcode/qr 等查询参数）
POST /api/preview                请求体为 spec -> image/png 二进制
POST /api/print                  请求体为 spec（含 copies/density/align）-> 打印
POST /api/learn                  测纸（会走纸）
"""
from __future__ import annotations

import io

# 允许 `python3 gongfeng_print/server.py` 直接运行（相对导入需要包上下文）。
if __package__ in (None, ""):  # pragma: no cover - 仅脚本直跑时触发
    import pathlib
    import sys

    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
    __package__ = "gongfeng_print"

import argparse
import contextlib
import json
import logging
import os
import re
import signal
import socket
import sys
import threading
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

import PIL

from . import APP_NAME, API_PREFIX, __version__
from .printers import (
    DEFAULT_MODEL_ID,
    BackendError,
    UnknownModelError,
    create,
    models_payload,
)
from .printers.base import PrinterBackend
from .render import (
    DPI,
    RenderError,
    fonts_available,
    mm_to_px,
    render_label,
    render_png,
    zint_available,
)
from .spec import (
    MAX_SPEC_BYTES,
    LabelSpec,
    SpecError,
    parse_spec,
)
from .transport.spp import (
    TransportError,
    controller_info,
    find_m1,
    is_m1_name,
    list_devices,
)

LOG = logging.getLogger("gongfeng.server")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 17600
DEFAULT_ORIGINS = {
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:4173",
    "http://127.0.0.1:4173",
}
# 本地任意端口（Vite 端口被占用时会顺延），避免联调时 CORS 莫名失败。
LOCAL_ORIGIN_RE = re.compile(r"^https?://(localhost|127\.0\.0\.1)(:\d{1,5})?$")
MAX_BODY_BYTES = MAX_SPEC_BYTES

EXPOSED_HEADERS = (
    "X-Label-Width-Px, X-Label-Height-Px, X-Label-Width-Mm, "
    "X-Label-Height-Mm, X-Label-Offset-X-Mm, X-Label-Offset-X-Px, "
    "X-Label-Density, X-Label-Model"
)


# ================================================================ 异常
class ApiError(Exception):
    """带 HTTP 状态码的业务错误。"""

    def __init__(self, message: str, status: int = 400, **extra: Any):
        self.status = status
        self.extra = extra
        super().__init__(message)


class DeviceBusyError(ApiError):
    """同一时刻只允许一个打印/测纸/状态任务使用设备。"""

    def __init__(self, busy_with: str = ""):
        super().__init__(
            f"设备忙：正在执行其他任务{f'（{busy_with}）' if busy_with else ''}，请稍后重试",
            status=200, busy=True)


# ================================================================ 业务层
class Api:
    """与 HTTP 无关的业务实现（selftest 可直接实例化）。"""

    def __init__(self, *, default_model: str = DEFAULT_MODEL_ID,
                 addr: str | None = None, bt_timeout: float = 20.0,
                 bt_retries: int = 1, log: logging.Logger | None = None):
        self.default_model = default_model
        self.addr = (addr or "").strip().upper() or None
        self.bt_timeout = float(bt_timeout)
        self.bt_retries = int(bt_retries)
        self.log = log or LOG
        self.started_at = time.time()
        self._device_lock = threading.Lock()
        self._busy_with = ""
        self._print_count = 0
        self._last_print: dict[str, Any] | None = None
        # 状态查询缓存：前端状态栏会轮询，避免每次 4~5s 的蓝牙往返把设备锁占满。
        self._status_cache: dict[tuple[str | None, str | None], tuple[float, dict]] = {}
        self._status_ttl = float(os.environ.get("GONG_FENG_STATUS_TTL", "3"))
        self._status_stale_serve = float(os.environ.get("GONG_FENG_STATUS_STALE", "30"))
        self._refresh_lock = threading.Lock()
        self._refresh_inflight = False
        self._refresh_event = threading.Event()
        self._refresh_event.set()  # 当前没有刷新任务
        self._last_addr: str | None = None  # 自动发现成功后复用，省一次 bluetoothctl

    # -- 设备互斥 ---------------------------------------------------
    @contextlib.contextmanager
    def _device_operation(self, name: str, wait: float = 0.0):
        """wait=0 时非阻塞（打印/测纸立刻报忙）；wait>0 时排队等待（状态查询）。"""
        acquired = (self._device_lock.acquire(timeout=wait) if wait > 0
                    else self._device_lock.acquire(blocking=False))
        if not acquired:
            raise DeviceBusyError(self._busy_with)
        self._busy_with = name
        try:
            yield
        finally:
            self._busy_with = ""
            self._device_lock.release()

    def _cache_status(self, key: tuple[str | None, str | None],
                      payload: dict[str, Any]) -> dict[str, Any]:
        self._status_cache[key] = (time.time(), payload)
        return payload

    def _clear_status_cache(self) -> None:
        self._status_cache.clear()

    # -- 通用 -------------------------------------------------------
    def health(self) -> dict[str, Any]:
        lzo_ok, lzo_error = False, None
        try:
            from .printers.hprt_m1 import _lzo_lib

            _lzo_lib()
            lzo_ok = True
        except Exception as exc:  # noqa: BLE001 - 健康检查不应抛错
            lzo_error = str(exc)
        return {
            "ok": True,
            "service": APP_NAME,
            "version": __version__,
            "time": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "uptime_s": round(time.time() - self.started_at, 1),
            "default_model": self.default_model,
            "models": [m.model_id for m in _models()] or [self.default_model],
            "print_count": self._print_count,
            "last_print": self._last_print,
            "deps": {
                "python": sys.version.split()[0],
                "pillow": getattr(PIL, "__version__", "?"),
                "zint": zint_available(),
                "lzo": lzo_ok,
                "fonts": fonts_available(),
                "bluetooth_spp": hasattr(socket, "AF_BLUETOOTH"),
            },
            **({"warnings": [f"LZO 不可用：{lzo_error}"]} if lzo_error else {}),
        }

    def warm_status(self) -> None:
        """服务启动后在后台预热一次状态缓存（不打印、不走纸）。"""
        self.log.info("后台预热打印机状态（不打印）")
        self._spawn_status_refresh(self.default_model, None,
                                   timeout=min(self.bt_timeout, 6.0), retries=0)

    def models(self) -> dict[str, Any]:
        return models_payload()

    def devices(self) -> dict[str, Any]:
        try:
            devices = list_devices()
        except TransportError as exc:
            return {
                "ok": False,
                "error": str(exc),
                "devices": [],
                "m1": None,
                "controller": {"available": False, "error": str(exc)},
            }
        m1 = find_m1(devices)
        controller = controller_info()
        configured = self.addr
        payload: dict[str, Any] = {
            "ok": True,
            "count": len(devices),
            "devices": [d.to_dict() for d in devices],
            "m1": m1.to_dict() if m1 else None,
            "controller": controller,
            "configured_addr": configured,
            "hint": ("打印机需先开机并配对；列表为空时先在系统蓝牙中扫描/配对一次"
                     if not m1 else f"已找到 M1：{m1.name} ({m1.address})"),
        }
        if configured and m1 and configured != m1.address:
            payload["warning"] = (
                f"配置地址 {configured} 与当前 M1 地址 {m1.address} 不一致"
                "（可用 --addr 或 M1_ADDR 指定）")
        return payload

    # -- 后端生命周期 -----------------------------------------------
    def _backend(self, model: str | None, addr: str | None = None,
                 timeout: float | None = None,
                 retries: int | None = None) -> PrinterBackend:
        """实例化后端；把服务级配置（超时/重试/日志）注入后端的同名属性。"""
        target = (addr or self.addr or self._last_addr or "").strip().upper() or None
        try:
            backend = create(model or self.default_model, addr=target)
        except TypeError:  # 后端工厂不接受 addr 参数
            backend = create(model or self.default_model)
        if hasattr(backend, "_timeout"):
            backend._timeout = (self.bt_timeout if timeout is None  # type: ignore[attr-defined]
                                else float(timeout))
        if hasattr(backend, "_retries"):
            backend._retries = (self.bt_retries if retries is None  # type: ignore[attr-defined]
                                else int(retries))
        if hasattr(backend, "_log"):
            backend._log = lambda msg: self.log.info("[bt] %s", msg)  # type: ignore[attr-defined]
        return backend

    def _run_device(self, name: str, model: str | None,
                    addr: str | None, fn, wait: float = 30.0,
                    timeout: float | None = None, retries: int | None = None):
        model_id = (model or self.default_model).strip().lower()
        with self._device_operation(name, wait=wait):
            backend = self._backend(model_id, addr, timeout=timeout,
                                    retries=retries)
            try:
                backend.connect()
                result = fn(backend)
                if backend.addr:
                    self._last_addr = backend.addr
                return backend, result
            finally:
                backend.close()

    # -- 状态 -------------------------------------------------------
    def _status_key(self, model_id: str, addr: str | None) -> tuple[str, str | None]:
        return (model_id,
                (addr or self.addr or self._last_addr or "").strip().upper() or None)

    def _query_status(self, model_id: str, addr: str | None,
                      wait: float = 15.0, timeout: float | None = None,
                      retries: int | None = None) -> dict[str, Any]:
        """真正连一次打印机查状态（未命中缓存时调用）。"""

        def query(backend: PrinterBackend):
            return backend.status()

        try:
            backend, st = self._run_device("status", model_id, addr, query,
                                           wait=wait, timeout=timeout,
                                           retries=retries)
        except (BackendError, TransportError) as exc:
            key = self._status_key(model_id, addr)
            return {
                "ok": False, "model": model_id, "addr": key[1],
                "status": "离线", "errors": [str(exc)], "value": -1,
                "error": str(exc),
            }
        payload = {"model": backend.model_id, "addr": backend.addr}
        payload.update(st.to_dict())
        return payload

    def _spawn_status_refresh(self, model_id: str, addr: str | None,
                              timeout: float | None = None,
                              retries: int | None = None) -> None:
        with self._refresh_lock:
            if self._refresh_inflight:
                return
            self._refresh_inflight = True
            self._refresh_event.clear()

        def worker() -> None:
            try:
                payload = self._query_status(model_id, addr, wait=8.0,
                                             timeout=timeout, retries=retries)
                real_addr = payload.get("addr") or addr
                self._status_cache[self._status_key(model_id, real_addr)] = (
                    time.time(), payload)
            except Exception:  # noqa: BLE001 - 后台刷新失败保留旧缓存
                self.log.debug("后台刷新状态失败", exc_info=True)
            finally:
                with self._refresh_lock:
                    self._refresh_inflight = False
                self._refresh_event.set()

        threading.Thread(target=worker, name="status-refresh", daemon=True).start()

    def render_element(self, payload: dict[str, Any]) -> bytes:
        """渲染单个条码/二维码为 PNG（供前端画布实时预览，与打印同一渲染器）。"""
        from .render import zint_png, zint_available
        if not zint_available():
            raise ApiError("服务端未安装 zint", 500)
        kind = str(payload.get("type") or "barcode").lower()
        data = str(payload.get("data") or "")
        if not data:
            raise ApiError("缺少 data", 400)
        if kind == "qr":
            side_mm = float(payload.get("size_mm") or 14.0)
            target = max(8, int(round(side_mm / 25.4 * 203)))
            img = zint_png("qr", data, target_px=target)
        else:
            sym = str(payload.get("symbology") or "code128").lower()
            width_mm = float(payload.get("width_mm") or 30.0)
            height_mm = float(payload.get("height_mm") or 12.0)
            target = max(8, int(round(width_mm / 25.4 * 203)))
            img = zint_png(sym, data, target_px=target, height_mm=height_mm,
                           show_text=bool(payload.get("show_text", True)))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def print_image(self, data: bytes, *, width_mm: float = 40.0,
                    height_mm: float = 30.0, offset_x_mm: float = 10.0,
                    copies: int = 1, density: int = 75, align: str = "left",
                    addr: str | None = None) -> dict[str, Any]:
        """打印一张上传的图片（PNG/JPEG；SVG 由前端栅格化后上传）。"""
        import io as _io
        from PIL import Image as _Image
        from .render import mm_to_px
        try:
            src = _Image.open(_io.BytesIO(data)).convert("RGB")
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"图片解析失败：{exc}"}
        W, H = mm_to_px(width_mm), mm_to_px(height_mm)
        off = mm_to_px(offset_x_mm)
        canvas = _Image.new("RGB", (off + W, H), "white")
        ratio = min(W / src.width, H / src.height)
        resized = src.resize((max(1, int(src.width * ratio)),
                              max(1, int(src.height * ratio))), _Image.LANCZOS)
        canvas.paste(resized, (off + (W - resized.width) // 2,
                               (H - resized.height) // 2))
        def do_print(backend):
            return backend.print_image(canvas, copies=max(1, min(int(copies), 99)),
                                       density=int(density), align=align,
                                       auto_learn=False, form_feed=False)
        try:
            backend, st = self._run_device("print", None, addr, do_print)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc), "width_px": canvas.width,
                    "height_px": canvas.height}
        return {"ok": st.ok, "status": st.summary, "value": st.value,
                "errors": list(st.errors), "width_px": canvas.width,
                "height_px": canvas.height, "offset_x_mm": offset_x_mm}

    def set_printer_params(self, start_pos_mm: float | None = None,
                           feed_after_pos_mm: float | None = None,
                           addr: str | None = None) -> dict[str, Any]:
        """写入打印机参数（官方 setkey）：207=起始打印位置, 208=定位后走纸长度（可负）。

        12 units/mm；写入会触发固件重新定位，可能走掉几张纸，属正常。
        """
        from .printers import hprt_m1 as M1
        written: list[str] = []
        try:
            backend = self._backend(None, addr)
            try:
                backend.connect()
                if start_pos_mm is not None:
                    val = int(round(float(start_pos_mm) * 12))
                    backend._write(M1.setkey_cmd(207, val, 1), gap=0.15)
                    written.append(f"207={start_pos_mm}mm({val})")
                if feed_after_pos_mm is not None:
                    val = int(round(float(feed_after_pos_mm) * 12))
                    backend._write(M1.setkey_cmd(208, val, 2), gap=0.15)
                    written.append(f"208={feed_after_pos_mm}mm({val})")
                return {"ok": True, "written": written,
                        "addr": getattr(backend, "addr", addr)}
            finally:
                backend.close()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc), "written": written}

    def scan(self, seconds: float = 8.0) -> dict[str, Any]:
        """主动扫描蓝牙设备（App 的"扫描打印机"用）并返回设备列表。"""
        try:
            from .transport.spp import _bluetoothctl
            _bluetoothctl(["scan", "on"], timeout=max(3.0, min(float(seconds), 20.0)))
        except Exception:  # noqa: BLE001
            pass
        payload = self.devices()
        payload["scanned"] = True
        return payload

    def connect_addr(self, addr: str) -> dict[str, Any]:
        """连接指定地址的打印机并握手（App 的"连接"按钮用）。"""
        try:
            backend = self._backend(None, addr)
            try:
                backend.connect()
                st = backend.status()
                return {"ok": True, "addr": getattr(backend, "addr", addr),
                        "model": backend.model_id, "status": st.summary,
                        "value": st.value, "errors": list(st.errors)}
            finally:
                backend.close()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "addr": addr, "error": str(exc)}

    def status(self, *, model: str | None = None,
               addr: str | None = None, force: bool = False) -> dict[str, Any]:
        """查询状态。命中缓存立即返回；缓存过期则返回旧值并在后台刷新。

        这样前端 15s 轮询不会因每次 3~4s 的蓝牙往返而超时；
        ``?refresh=1``（或 ``force=True``）强制同步查询。
        """
        model_id = (model or self.default_model).strip().lower()
        key = self._status_key(model_id, addr)
        cached = self._status_cache.get(key)
        # 只有查询「默认地址」时才复用后台刷新；显式 addr 必须真连该地址查询，
        # 否则离线地址会被误当成“检查中”。
        if (cached is None and not force and addr is None
                and self._refresh_inflight):
            # 预热/后台刷新进行中：短暂等待结果，避免首屏再开一次蓝牙连接
            self._refresh_event.wait(2.0)
            key = self._status_key(model_id, addr)
            cached = self._status_cache.get(key)
            if cached:
                out = dict(cached[1])
                out["cached"] = True
                return out
            return {
                "ok": True, "model": model_id, "status": "检查中",
                "value": -1, "errors": [], "pending": True,
                "error": "", "hint": "正在后台检查打印机状态，请稍后刷新",
            }
        if cached and not force:
            age = time.time() - cached[0]
            if self._status_ttl > 0 and age < self._status_ttl:
                return dict(cached[1])
            if age < self._status_stale_serve:
                self._spawn_status_refresh(model_id, addr)
                stale = dict(cached[1])
                stale.update(cached=True, stale=True,
                             age_s=round(age, 1), refreshing=True)
                return stale
        payload = self._query_status(model_id, addr)
        real_addr = payload.get("addr") or addr or self.addr
        self._status_cache[self._status_key(model_id, real_addr)] = (
            time.time(), payload)
        return dict(payload)

    # -- 测纸 -------------------------------------------------------
    def learn(self, *, model: str | None = None,
              addr: str | None = None) -> dict[str, Any]:
        model_id = (model or self.default_model).strip().lower()

        def do_learn(backend: PrinterBackend):
            return backend.learn()

        try:
            backend, st = self._run_device("learn", model_id, addr, do_learn)
        except (BackendError, TransportError) as exc:
            return {"ok": False, "model": model_id, "error": str(exc),
                    "status": "离线", "errors": [str(exc)], "value": -1}
        self._clear_status_cache()
        status_payload = {"model": backend.model_id, "addr": backend.addr}
        status_payload.update(st.to_dict())
        self._status_cache[self._status_key(model_id, backend.addr)] = (
            time.time(), status_payload)
        payload = dict(status_payload, action="learn")
        return payload

    # -- 几何校验 ---------------------------------------------------
    @staticmethod
    def _check_geometry(spec: LabelSpec, caps) -> list[str]:
        """校验标签宽度 + 纸卷偏移是否超出打印头上限；返回警告列表。"""
        if spec.width_mm > caps.max_width_mm:
            raise ApiError(
                f"标签宽度 {spec.width_mm}mm 超过 {caps.name} 支持的最大纸宽 "
                f"{caps.max_width_mm}mm", 400)
        if spec.offset_x_mm > caps.max_offset_x_mm:
            raise ApiError(
                f"纸卷偏移 {spec.offset_x_mm}mm 超过上限 {caps.max_offset_x_mm}mm", 400)
        if spec.height_mm > caps.max_height_mm:
            raise ApiError(
                f"标签高度 {spec.height_mm}mm 超过上限 {caps.max_height_mm}mm", 400)
        max_px = caps.head_dots + 16  # 40mm 纸 + 10mm 偏移 = 400px 的容差
        canvas_px = mm_to_px(spec.width_mm + spec.offset_x_mm)
        if canvas_px > max_px:
            raise ApiError(
                f"标签宽度({spec.width_mm}mm) + 纸卷偏移({spec.offset_x_mm}mm) "
                f"= {round(spec.width_mm + spec.offset_x_mm, 2)}mm = {canvas_px}px，"
                f"超过 {caps.name} 的位图宽度上限 {max_px}px"
                "（40mm 纸请把 offset_x_mm 设为 10，50mm 纸设为 0）", 400)
        warnings: list[str] = []
        if canvas_px > caps.head_dots:
            warnings.append(
                f"位图宽 {canvas_px}px 超过打印头 {caps.head_dots}dot，"
                "按 400px 容差放行；若右缘缺字请减小 offset_x_mm")
        return warnings

    # -- 渲染 -------------------------------------------------------
    def preview(self, spec: LabelSpec) -> tuple[bytes, dict[str, Any]]:
        model_caps = None
        model_id = (spec.model or self.default_model).strip().lower()
        try:
            model_caps = create(model_id).capabilities
        except UnknownModelError:
            raise
        warnings = self._check_geometry(spec, model_caps)
        try:
            png = render_png(spec)
        except RenderError as exc:
            raise ApiError(str(exc), 400) from None
        img_meta = {
            "model": model_id,
            "width_mm": spec.width_mm,
            "height_mm": spec.height_mm,
            "offset_x_mm": spec.offset_x_mm,
            "offset_x_px": mm_to_px(spec.offset_x_mm),
            "canvas_width_mm": round(spec.width_mm + spec.offset_x_mm, 3),
            "width_px": mm_to_px(spec.width_mm + spec.offset_x_mm),
            "label_width_px": mm_to_px(spec.width_mm),
            "height_px": mm_to_px(spec.height_mm),
            "dpi": DPI,
            "density": spec.density,
            "border": spec.border,
            "items": len(spec.items),
            "warnings": warnings,
        }
        return png, img_meta

    def preview_query(self, query: dict[str, list[str]]) -> tuple[bytes, dict[str, Any]]:
        """GET /api/preview 的查询参数解析。"""
        raw_spec = _first(query, "spec")
        if raw_spec:
            spec = parse_spec(raw_spec)  # parse_qs 已完成百分号解码
            return self.preview(spec)
        # 便捷参数：?text=第一行&text=第二行&barcode=ABC&qr=...&width_mm=40
        payload: dict[str, Any] = {}
        for key in ("width_mm", "height_mm", "density", "align", "border", "model"):
            value = _first(query, key)
            if value is not None:
                payload[key] = value
        items: list[dict[str, Any]] = []
        for value in query.get("text", []):
            if value:
                items.append({"type": "text", "text": value,
                              "size": _first(query, "size") or 24})
        barcode = _first(query, "barcode")
        if barcode:
            items.append({"type": "barcode", "data": barcode,
                          "symbology": _first(query, "symbology") or "code128"})
        qr = _first(query, "qr")
        if qr:
            items.append({"type": "qr", "data": qr})
        if not items and not raw_spec and not payload.get("border"):
            raise ApiError("缺少 spec 查询参数："
                           "示例 /api/preview?spec=%7B%22items%22%3A...%7D", 400)
        payload["items"] = items
        return self.preview(parse_spec(payload))

    # -- 打印 -------------------------------------------------------
    def print_label(self, spec: LabelSpec, *, addr: str | None = None) -> dict[str, Any]:
        model_id = (spec.model or self.default_model).strip().lower()
        image = None  # 延迟到拿到能力后再渲染（宽度校验）
        caps = None
        try:
            caps = create(model_id).capabilities
        except UnknownModelError:
            raise
        warnings = self._check_geometry(spec, caps)
        if not caps.supports_learn and spec.auto_learn:
            spec.auto_learn = False
        try:
            image = render_label(spec)
        except RenderError as exc:
            raise ApiError(str(exc), 400) from None

        def do_print(backend: PrinterBackend):
            # 多份拆成多个单张：每张独立下发一次 PRINT，间隔 0.8s，避免连续打印累积偏移
            n = max(1, min(int(spec.copies or 1), 99))
            st = None
            for _ in range(n):
                st = backend.print_image(
                    image, copies=1, density=spec.density,
                    align=spec.align, auto_learn=False,
                    form_feed=spec.form_feed, trim_bottom_mm=spec.trim_bottom_mm)
                time.sleep(0.8)
            return st

        try:
            backend, st = self._run_device("print", model_id, addr, do_print)
        except (BackendError, TransportError) as exc:
            return {
                "ok": False, "model": model_id, "error": str(exc),
                "status": "离线", "errors": [str(exc)], "value": -1,
                "width_px": image.width, "height_px": image.height,
                "offset_x_mm": spec.offset_x_mm, "copies": spec.copies,
                "warnings": warnings,
            }
        self._print_count += 1
        self._clear_status_cache()
        status_payload = {"model": backend.model_id, "addr": backend.addr}
        status_payload.update(st.to_dict())
        self._status_cache[self._status_key(model_id, backend.addr)] = (
            time.time(), status_payload)
        payload = {
            "model": backend.model_id,
            "addr": backend.addr,
            "width_mm": spec.width_mm,
            "height_mm": spec.height_mm,
            "offset_x_mm": spec.offset_x_mm,
            "offset_x_px": mm_to_px(spec.offset_x_mm),
            "canvas_width_mm": round(spec.width_mm + spec.offset_x_mm, 3),
            "width_px": image.width,
            "label_width_px": mm_to_px(spec.width_mm),
            "height_px": image.height,
            "dpi": DPI,
            "copies": spec.copies,
            "density": spec.density,
            "align": spec.align,
            "border": spec.border,
            "items": len(spec.items),
            "warnings": warnings,
            "print_count": self._print_count,
        }
        payload.update(st.to_dict())
        self._last_print = {"ok": st.ok, "model": backend.model_id,
                            "time": datetime.now().isoformat(timespec="seconds"),
                            "copies": spec.copies}
        return payload


def _models():
    from .printers import list_models

    return list_models()


def _first(query: dict[str, list[str]], key: str, default: str | None = None) -> str | None:
    values = query.get(key)
    if not values:
        return default
    return values[0] if values[0] != "" else default


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


# ================================================================ HTTP
class GongfengHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address, handler_class, api: Api,
                 allowed_origins: set[str] | None = None, verbose: bool = False):
        self.api = api
        self.allowed_origins = set(allowed_origins or DEFAULT_ORIGINS)
        self.verbose = verbose
        super().__init__(server_address, handler_class)


class ApiHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = f"{APP_NAME}/{__version__}"

    # -- 基础设施 ---------------------------------------------------
    @property
    def api(self) -> Api:
        return self.server.api  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        if getattr(self.server, "verbose", False):
            LOG.info("%s - %s", self.address_string(), fmt % args)

    def _cors_headers(self) -> None:
        origin = self.headers.get("Origin")
        allowed = getattr(self.server, "allowed_origins", DEFAULT_ORIGINS)
        if origin and (origin in allowed or LOCAL_ORIGIN_RE.match(origin)):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        elif not origin:
            self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers",
                         "Content-Type, Accept, X-Requested-With")
        self.send_header("Access-Control-Expose-Headers", EXPOSED_HEADERS)
        self.send_header("Access-Control-Max-Age", "86400")

    def _send(self, body: bytes, content_type: str, status: int = 200,
              headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._cors_headers()
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self._send(body, "application/json; charset=utf-8", status)

    def _send_png(self, data: bytes, meta: dict[str, Any] | None = None) -> None:
        headers = {"Content-Disposition": 'inline; filename="label.png"'}
        if meta:
            headers["X-Label-Width-Px"] = str(meta.get("width_px", ""))
            headers["X-Label-Height-Px"] = str(meta.get("height_px", ""))
            headers["X-Label-Width-Mm"] = str(meta.get("width_mm", ""))
            headers["X-Label-Height-Mm"] = str(meta.get("height_mm", ""))
            headers["X-Label-Offset-X-Mm"] = str(meta.get("offset_x_mm", ""))
            headers["X-Label-Offset-X-Px"] = str(meta.get("offset_x_px", ""))
            headers["X-Label-Density"] = str(meta.get("density", ""))
            headers["X-Label-Model"] = str(meta.get("model", ""))
        self._send(data, "image/png", 200, headers)

    def _read_body(self, limit: int = MAX_BODY_BYTES) -> bytes:
        raw_len = self.headers.get("Content-Length")
        if raw_len is None:
            return b""
        try:
            length = int(raw_len)
        except ValueError:
            raise ApiError("Content-Length 不合法", 400) from None
        if length < 0:
            raise ApiError("Content-Length 不合法", 400)
        if length > limit:
            raise ApiError(f"请求体过大（{length} > {limit} 字节）", 413)
        data = self.rfile.read(length) if length else b""
        if len(data) < length:
            raise ApiError("请求体不完整", 400)
        return data

    def _read_json(self) -> Any:
        body = self._read_body()
        if not body:
            raise ApiError("请求体为空：需要标签 spec JSON", 400)
        try:
            return json.loads(body.decode("utf-8"))
        except UnicodeDecodeError:
            raise ApiError("请求体不是 UTF-8 文本", 400) from None
        except json.JSONDecodeError as exc:
            raise ApiError(f"请求体不是合法 JSON：{exc}", 400) from None

    # -- 路由 -------------------------------------------------------
    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send(b"", "text/plain; charset=utf-8", 204)

    def do_HEAD(self) -> None:  # noqa: N802
        self._dispatch("HEAD")

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def _dispatch(self, method: str) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query, keep_blank_values=True)
        try:
            if method in ("GET", "HEAD"):
                self._route_get(path, query)
            elif method == "POST":
                self._route_post(path, query)
            else:
                raise ApiError(f"不支持的 HTTP 方法 {method}", 405)
        except UnknownModelError as exc:
            self._send_json({
                "ok": False, "error": str(exc),
                "supported_models": exc.supported,
            }, 400)
        except DeviceBusyError as exc:
            self._send_json({"ok": False, "error": str(exc), "busy": True}, 200)
        except ApiError as exc:
            payload = {"ok": False, "error": str(exc), **exc.extra}
            self._send_json(payload, exc.status)
        except SpecError as exc:
            self._send_json({"ok": False, "error": str(exc)}, 400)
        except RenderError as exc:
            self._send_json({"ok": False, "error": str(exc)}, 400)
        except (BackendError, TransportError) as exc:
            self._send_json({"ok": False, "error": str(exc)}, 200)
        except (BrokenPipeError, ConnectionResetError):
            LOG.debug("客户端断开连接")
        except Exception as exc:  # noqa: BLE001 - API 不允许 500
            LOG.error("处理 %s %s 时异常：%s", method, self.path,
                      traceback.format_exc())
            self._send_json({
                "ok": False,
                "error": f"内部错误：{type(exc).__name__}: {exc}",
            }, 200)

    # -- GET --------------------------------------------------------
    def _route_get(self, path: str, query: dict[str, list[str]]) -> None:
        if path == "/":
            self._send(_LANDING_HTML, "text/html; charset=utf-8")
        elif path in ("/api", "/api/"):
            self._send_json({
                "ok": True, "service": APP_NAME, "version": __version__,
                "endpoints": _ENDPOINTS,
            })
        elif path == "/api/health":
            self._send_json(self.api.health())
        elif path == "/api/models":
            self._send_json(self.api.models())
        elif path == "/api/devices":
            self._send_json(self.api.devices())
        elif path == "/api/scan":
            seconds = float(_first(query, "seconds") or 8.0)
            self._send_json(self.api.scan(seconds))
        elif path == "/api/connect":
            payload = self._read_json()
            addr = (payload or {}).get("addr") if isinstance(payload, dict) else None
            if not addr:
                raise ApiError("缺少 addr（打印机蓝牙地址）", 400)
            self._send_json(self.api.connect_addr(str(addr)))
        elif path == "/api/status":
            self._send_json(self.api.status(
                model=_first(query, "model"), addr=_first(query, "addr"),
                force=_truthy(_first(query, "refresh"))))
        elif path == "/api/preview":
            png, meta = self.api.preview_query(query)
            self._send_png(png, meta)
        elif path == "/api/print":
            raise ApiError("请用 POST /api/print 提交标签 spec", 405)
        elif path == "/api/learn":
            raise ApiError("请用 POST /api/learn 触发测纸", 405)
        else:
            raise ApiError(f"未知接口：GET {path}", 404)

    # -- POST -------------------------------------------------------
    def _route_post(self, path: str, query: dict[str, list[str]]) -> None:
        if path == "/api/preview":
            payload = self._read_json()
            spec = parse_spec(payload)
            png, meta = self.api.preview(spec)
            self._send_png(png, meta)
        elif path == "/api/print":
            payload = self._read_json()
            if not isinstance(payload, dict):
                raise ApiError("请求体必须是 JSON 对象（标签 spec）", 400)
            spec = parse_spec(payload)
            addr = payload.get("addr") if isinstance(payload.get("addr"), str) else None
            self._send_json(self.api.print_label(spec, addr=addr))
        elif path == "/api/learn":
            body = self._read_body()
            payload: dict[str, Any] = {}
            if body:
                try:
                    payload = json.loads(body.decode("utf-8")) or {}
                except (UnicodeDecodeError, json.JSONDecodeError):
                    payload = {}
                if not isinstance(payload, dict):
                    payload = {}
            model = payload.get("model") if payload else None
            addr = payload.get("addr") if payload else None
            if not model:
                model = _first(query, "model")
            if not addr:
                addr = _first(query, "addr")
            self._send_json(self.api.learn(model=model, addr=addr))
        elif path == "/api/connect":
            payload = self._read_json()
            addr = (payload or {}).get("addr") if isinstance(payload, dict) else None
            if not addr:
                raise ApiError("缺少 addr（打印机蓝牙地址）", 400)
            self._send_json(self.api.connect_addr(str(addr)))
        elif path == "/api/printer/params":
            payload = self._read_json()
            if not isinstance(payload, dict):
                raise ApiError("请求体必须是 JSON 对象", 400)
            def _f(name):
                v = payload.get(name)
                return None if v is None else float(v)
            self._send_json(self.api.set_printer_params(
                start_pos_mm=_f("start_pos_mm"),
                feed_after_pos_mm=_f("feed_after_pos_mm"),
                addr=payload.get("addr")))
        elif path == "/api/print/image":
            body = self._read_body()
            if not body:
                raise ApiError("请求体为空（需上传 PNG/JPEG 二进制）", 400)
            q = {k: _first(query, k) for k in
                 ("width_mm", "height_mm", "offset_x_mm", "copies", "density", "align", "addr")}
            self._send_json(self.api.print_image(
                body,
                width_mm=float(q["width_mm"] or 40.0),
                height_mm=float(q["height_mm"] or 30.0),
                offset_x_mm=float(q["offset_x_mm"] or 10.0),
                copies=int(float(q["copies"] or 1)),
                density=int(float(q["density"] or 75)),
                align=q["align"] or "left",
                addr=q["addr"]))
        elif path == "/api/element/render":
            payload = self._read_json()
            if not isinstance(payload, dict):
                raise ApiError("请求体必须是 JSON 对象", 400)
            png = self.api.render_element(payload)
            self._send(png, "image/png")
        elif path == "/api/status":
            self._send_json(self.api.status(
                model=_first(query, "model"), addr=_first(query, "addr"),
                force=_truthy(_first(query, "refresh"))))
        else:
            raise ApiError(f"未知接口：POST {path}", 404)


# ================================================================ 入口
_ENDPOINTS = [
    "GET  /api/health",
    "GET  /api/models",
    "GET  /api/devices",
    "GET  /api/status?model=hprt-m1&addr=..",
    "GET  /api/preview?spec=<urlencoded json>",
    "POST /api/preview",
    "POST /api/print",
    "POST /api/learn",
]

_LANDING_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>共风标签打印 · 打印服务</title>
<style>
body{font-family:system-ui,-apple-system,"Noto Sans CJK SC",sans-serif;
max-width:760px;margin:48px auto;padding:0 20px;color:#1f2328;line-height:1.65}
code{background:#f2f4f7;padding:2px 6px;border-radius:4px;font-size:.92em}
li{margin:6px 0} h1{font-size:1.6rem} footer{color:#666;margin-top:32px;font-size:.9em}
</style></head><body>
<h1>共风标签打印 · 打印服务</h1>
<p>服务已启动（<code>__SERVICE__</code>）。这是后端 API 服务，
前端设计器请访问 <code>http://localhost:5173</code>。</p>
<h2>接口</h2><ul>__ENDPOINTS__</ul>
<p>示例：<a href="/api/health">/api/health</a> ·
<a href="/api/models">/api/models</a> ·
<a href="/api/devices">/api/devices</a> ·
<a href="/api/preview?spec=%7B%22width_mm%22%3A40%2C%22height_mm%22%3A30%2C%22items%22%3A%5B%7B%22type%22%3A%22text%22%2C%22text%22%3A%22HELLO%22%2C%22size%22%3A36%2C%22bold%22%3Atrue%7D%5D%7D">/api/preview 示例</a></p>
<footer>__VERSION__ · 汉印 M1（ESC/POLI over 蓝牙 SPP）</footer>
</body></html>"""


def _render_landing() -> bytes:
    items = "".join(f"<li><code>{e}</code></li>" for e in _ENDPOINTS)
    html = (_LANDING_HTML
            .replace("__ENDPOINTS__", items)
            .replace("__SERVICE__", f"{APP_NAME} v{__version__}")
            .replace("__VERSION__", f"{APP_NAME} v{__version__}"))
    return html.encode("utf-8")


# 缓存渲染后的首页字节
_LANDING_HTML = _render_landing()  # type: ignore[assignment]


def create_server(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, *,
                  api: Api | None = None, allowed_origins: set[str] | None = None,
                  verbose: bool = False) -> GongfengHTTPServer:
    """创建（但不启动）HTTP 服务；selftest 用 port=0 起临时端口。"""
    api = api or Api()
    server = GongfengHTTPServer((host, port), ApiHandler, api,
                                allowed_origins=allowed_origins, verbose=verbose)
    LOG.debug("服务绑定 %s:%s", host, server.server_address[1])
    return server


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m gongfeng_print.server",
        description="共风标签打印后端服务（汉印 M1 / 蓝牙 SPP / 纯标准库 HTTP）")
    parser.add_argument("--host", default=DEFAULT_HOST,
                        help=f"监听地址（默认 {DEFAULT_HOST}；局域网访问用 0.0.0.0）")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"监听端口（默认 {DEFAULT_PORT}）")
    parser.add_argument("--model", default=DEFAULT_MODEL_ID,
                        help=f"默认机型（默认 {DEFAULT_MODEL_ID}）")
    parser.add_argument("--addr", default=None,
                        help="M1 蓝牙地址（默认自动发现，或读环境变量 M1_ADDR）")
    parser.add_argument("--bt-timeout", type=float, default=20.0,
                        help="RFCOMM 连接超时秒数（默认 20）")
    parser.add_argument("--bt-retries", type=int, default=1,
                        help="蓝牙连接重试次数（默认 1）")
    parser.add_argument("--cors", action="append", default=[], metavar="ORIGIN",
                        help="额外允许的 CORS 来源，可重复（默认放行本地 5173/4173）")
    parser.add_argument("--verbose", action="store_true", help="打印访问日志")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S")
    origins = set(DEFAULT_ORIGINS)
    for origin in args.cors or []:
        origins.add(origin.strip().rstrip("/"))
    api = Api(default_model=args.model, addr=args.addr,
              bt_timeout=args.bt_timeout, bt_retries=args.bt_retries)
    try:
        server = create_server(args.host, args.port, api=api,
                               allowed_origins=origins, verbose=args.verbose)
    except OSError as exc:
        LOG.error("无法监听 %s:%s：%s", args.host, args.port, exc)
        print(f"无法监听 {args.host}:{args.port}：{exc}", file=sys.stderr)
        return 1

    if not os.environ.get("GONG_FENG_NO_WARM"):
        api.warm_status()

    host, port = server.server_address[:2]
    print(f"{APP_NAME} v{__version__} 已启动：http://{host}:{port}")
    print(f"  机型：{args.model}  蓝牙地址：{args.addr or '自动发现 M1-xxxx'}")
    print("  接口：GET  /api/health /api/models /api/devices /api/status /api/preview")
    print("        POST /api/preview /api/print /api/learn")
    print("  CORS 允许：", ", ".join(sorted(origins)))
    print("  Ctrl+C 停止")
    sys.stdout.flush()

    stopping = threading.Event()

    def _stop(_signum, _frame):
        if stopping.is_set():
            return
        stopping.set()
        print("\n正在停止服务…", flush=True)
        threading.Thread(target=server.shutdown, daemon=True).start()

    with contextlib.suppress(ValueError, OSError):
        signal.signal(signal.SIGINT, _stop)
        signal.signal(signal.SIGTERM, _stop)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:  # pragma: no cover
        pass
    finally:
        server.server_close()
        LOG.info("服务已停止")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
