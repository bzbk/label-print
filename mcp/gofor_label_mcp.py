#!/usr/bin/env python3
"""共风标签打印 · MCP server（stdio JSON-RPC 2.0）。

把标签打印能力暴露给任意 MCP 客户端（DSH / Claude Desktop / Cursor …）。

工具
----
  label_models       支持的打印机机型与能力
  label_devices      系统已知蓝牙设备（找 M1）
  label_status       打印机状态（缺纸/开盖/低电/定位失败）
  label_learn        测纸/定位（会走纸）
  label_preview      只渲染 PNG，不打印（省纸）
  label_print        渲染并打印标签
  label_print_image  打印图片文件

运行::

    python3 mcp/gofor_label_mcp.py        # 从仓库任意位置
"""
from __future__ import annotations

import contextlib
import json
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "server"))

from gongfeng_print import printers, render, spec as spec_mod  # noqa: E402
from gongfeng_print.transport import spp  # noqa: E402

SERVER_INFO = {"name": "gofor-label", "version": "1.0.0"}
PROTOCOL_VERSION = "2024-11-05"
_REAL_STDOUT = sys.stdout

API_BASE = os.environ.get("GOFOR_LABEL_API", "http://127.0.0.1:17600")


def _http_up(timeout: float = 0.8) -> bool:
    """后端服务是否在线：在线时 MCP 走 HTTP，避免两个进程同时占用蓝牙 SPP。"""
    import urllib.request
    try:
        with urllib.request.urlopen(API_BASE + "/api/health", timeout=timeout) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def _http_json(path: str, payload: dict | None = None, timeout: float = 120):
    import urllib.request
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(API_BASE + path, data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode() or "{}")


def _http_png(path: str, timeout: float = 60) -> bytes:
    import urllib.request
    with urllib.request.urlopen(API_BASE + path, timeout=timeout) as r:
        return r.read()


def _proxy_http(name: str, args: dict):
    """把工具调用代理到后端 REST API；不支持的返回 None。"""
    import urllib.parse
    try:
        if name == "label_models":
            return _http_json("/api/models", timeout=15)
        if name == "label_devices":
            return _http_json("/api/devices", timeout=15)
        if name == "label_status":
            q = urllib.parse.urlencode({k: args[k] for k in ("model", "addr") if args.get(k)})
            return _http_json("/api/status" + (("?" + q) if q else ""), timeout=30)
        if name == "label_learn":
            return _http_json("/api/learn", {}, timeout=120)
        if name in ("label_preview", "label_print"):
            spec_obj = _spec_from_args(args).to_dict()
            if name == "label_preview":
                q = urllib.parse.urlencode({"spec": json.dumps(spec_obj, ensure_ascii=False)})
                png = _http_png("/api/preview?" + q, timeout=60)
                out = args.get("output_path") or os.path.join(HERE, "preview.png")
                with open(out, "wb") as f:
                    f.write(png)
                return {"ok": True, "path": out, "size": [spec_obj["width_px"] + spec_obj["offset_x_px"],
                        spec_obj["height_px"]], "width_mm": spec_obj["width_mm"],
                        "height_mm": spec_obj["height_mm"],
                        "offset_x_mm": spec_obj["offset_x_mm"], "spec": spec_obj,
                        "via": "http"}
            res = _http_json("/api/print", spec_obj, timeout=180)
            res.setdefault("via", "http")
            return res
    except Exception:  # noqa: BLE001
        return None
    return None



@contextlib.contextmanager
def quiet():
    """库代码里的 print/日志不能污染 JSON-RPC 通道。"""
    saved = sys.stdout
    sys.stdout = sys.stderr
    try:
        yield
    finally:
        sys.stdout = saved


def send(msg: dict) -> None:
    _REAL_STDOUT.write(json.dumps(msg, ensure_ascii=False) + "\n")
    _REAL_STDOUT.flush()


# ---------------------------------------------------------------- helpers
def _backend(model: str | None = None, addr: str | None = None):
    b = printers.create(model or printers.DEFAULT_MODEL_ID)
    if addr and hasattr(b, "addr"):
        b.addr = addr  # type: ignore[attr-defined]
    return b


def _with_backend(fn, model=None, addr=None):
    b = _backend(model, addr)
    try:
        b.connect()
        return fn(b)
    finally:
        with contextlib.suppress(Exception):
            b.close()


def _spec_from_args(args: dict):
    """接受完整 spec，也接受 text_lines/barcode/qr 便捷字段。"""
    if isinstance(args.get("spec"), (dict, str)):
        return spec_mod.parse_spec(args["spec"])

    payload: dict = {
        "width_mm": args.get("width_mm", 40),
        "height_mm": args.get("height_mm", 30),
        "border": bool(args.get("border", False)),
        "density": args.get("density", 75),
        "align": args.get("align", "left"),
        "copies": args.get("copies", 1),
        "offset_x_mm": args.get("offset_x_mm", 10),
        "offset_y_mm": args.get("offset_y_mm", 0),
        "auto_learn": not args.get("no_auto_learn", False),
        "form_feed": bool(args.get("form_feed", False)),
        "items": [],
    }
    if args.get("model"):
        payload["model"] = args["model"]
    for line in args.get("text_lines") or []:
        if isinstance(line, str):
            payload["items"].append({"type": "text", "text": line,
                                     "size": args.get("font_size", 24)})
        else:
            payload["items"].append({
                "type": "text", "text": str(line.get("text", "")),
                "size": int(line.get("size", args.get("font_size", 24))),
                "bold": bool(line.get("bold", False)),
                "align": line.get("align", "left"),
                **({"x_mm": line["x_mm"]} if "x_mm" in line else {}),
                **({"y_mm": line["y_mm"]} if "y_mm" in line else {}),
            })
    if args.get("barcode"):
        bc = args["barcode"] if isinstance(args["barcode"], dict) else {"data": args["barcode"]}
        payload["items"].append({"type": "barcode", "data": str(bc.get("data", "")),
                                 "symbology": bc.get("symbology", "code128"),
                                 "height_mm": bc.get("height_mm", 12),
                                 "show_text": bc.get("show_text", True)})
    if args.get("qr"):
        qr = args["qr"] if isinstance(args["qr"], dict) else {"data": args["qr"]}
        payload["items"].append({"type": "qr", "data": str(qr.get("data", "")),
                                 "size_mm": qr.get("size_mm", 14)})
    return spec_mod.parse_spec(payload)


def _render(spec_obj) -> "object":
    return render.render_label(spec_obj)


# ---------------------------------------------------------------- tools
def tool_models(args: dict) -> dict:
    return printers.models_payload()


def tool_devices(args: dict) -> dict:
    devices = spp.list_devices()
    payload = [{"address": d.address, "name": d.name} for d in devices]
    m1 = spp.find_m1(devices)
    return {"ok": True, "count": len(payload), "devices": payload,
            "printer": {"address": m1.address, "name": m1.name} if m1 else None,
            "hint": "打印机需开机；列表为空时先在系统蓝牙里扫描/配对一次（bluetoothctl devices）"}


def tool_status(args: dict) -> dict:
    with quiet():
        st = _with_backend(lambda b: b.status(), args.get("model"), args.get("addr"))
    return {"ok": st.ok, "status": st.summary, "value": st.value,
            "errors": st.errors, "printing": st.printing,
            "raw_hex": st.raw.hex(" ") if getattr(st, "raw", None) else ""}


def tool_learn(args: dict) -> dict:
    with quiet():
        st = _with_backend(lambda b: b.learn(), args.get("model"), args.get("addr"))
    return {"ok": st.ok, "status": st.summary, "value": st.value, "errors": st.errors}


def tool_preview(args: dict) -> dict:
    spec_obj = _spec_from_args(args)
    with quiet():
        img = _render(spec_obj)
    out = args.get("output_path") or os.path.join(HERE, "preview.png")
    img.save(out)
    return {"ok": True, "path": out, "size": list(img.size),
            "width_mm": spec_obj.width_mm, "height_mm": spec_obj.height_mm,
            "offset_x_mm": getattr(spec_obj, "offset_x_mm", None),
            "spec": spec_obj.to_dict()}


def tool_print(args: dict) -> dict:
    spec_obj = _spec_from_args(args)
    with quiet():
        img = _render(spec_obj)

        def do(b):
            return b.print_image(img, copies=spec_obj.copies, density=spec_obj.density,
                                 align=spec_obj.align, auto_learn=spec_obj.auto_learn,
                                 form_feed=spec_obj.form_feed)

        st = _with_backend(do, spec_obj.model, args.get("addr"))
    return {"ok": st.ok, "status": st.summary, "value": st.value, "errors": st.errors,
            "copies": spec_obj.copies, "size_px": list(img.size),
            "width_mm": spec_obj.width_mm, "height_mm": spec_obj.height_mm,
            "offset_x_mm": getattr(spec_obj, "offset_x_mm", None)}


def tool_print_image(args: dict) -> dict:
    path = args.get("image_path") or args.get("path")
    if not path or not os.path.exists(path):
        return {"ok": False, "error": f"图片不存在: {path!r}"}
    from PIL import Image

    with quiet():
        img = Image.open(path).convert("RGB")
        target_w = int(float(args.get("width_mm", 40)) / 25.4 * 203)
        target_h = int(float(args.get("height_mm", 30)) / 25.4 * 203)
        offset = int(float(args.get("offset_x_mm", 10)) / 25.4 * 203)
        canvas = Image.new("RGB", (target_w + offset, target_h), "white")
        ratio = min(target_w / img.width, target_h / img.height)
        resized = img.resize((max(1, int(img.width * ratio)),
                              max(1, int(img.height * ratio))), Image.LANCZOS)
        canvas.paste(resized, (offset + (target_w - resized.width) // 2,
                               (target_h - resized.height) // 2))

        def do(b):
            return b.print_image(canvas, copies=int(args.get("copies", 1)),
                                 density=int(args.get("density", 75)),
                                 auto_learn=not args.get("no_auto_learn", False))

        st = _with_backend(do, args.get("model"), args.get("addr"))
    return {"ok": st.ok, "status": st.summary, "failures": st.errors}


TOOLS = [
    {"name": "label_models", "description": "列出支持的打印机机型与能力（含默认标签尺寸/偏移/浓度）。",
     "inputSchema": {"type": "object", "properties": {}}, "handler": tool_models},
    {"name": "label_devices", "description": "列出系统已知蓝牙设备，并指出哪台是 M1 打印机。",
     "inputSchema": {"type": "object", "properties": {}}, "handler": tool_devices},
    {"name": "label_status", "description": "查询打印机状态（缺纸/过热/低电/开盖/定位失败等）。",
     "inputSchema": {"type": "object", "properties": {
         "model": {"type": "string", "description": "机型 id，默认 hprt-m1"},
         "addr": {"type": "string", "description": "蓝牙地址，可省略自动发现"}}},
     "handler": tool_status},
    {"name": "label_learn", "description": "测纸/定位：测量标签间隙（会走纸数张）。仅在定位失败或换纸后使用。",
     "inputSchema": {"type": "object", "properties": {
         "model": {"type": "string"}, "addr": {"type": "string"}}}, "handler": tool_learn},
    {"name": "label_preview", "description": "渲染标签为 PNG 预览，不打印（省纸）。确认版式后再调用 label_print。",
     "inputSchema": {"type": "object", "properties": {
         "spec": {"type": "object", "description": "完整标签 spec（见 README）"},
         "text_lines": {"type": "array", "description": "文本行：字符串或 {text,size,bold,align,x_mm,y_mm}"},
         "barcode": {"type": "object", "description": "{data, symbology, height_mm}"},
         "qr": {"type": "object", "description": "{data, size_mm}"},
         "width_mm": {"type": "number", "default": 40},
         "height_mm": {"type": "number", "default": 30},
         "offset_x_mm": {"type": "number", "default": 10},
         "offset_y_mm": {"type": "number", "default": 0, "description": "垂直微调 mm（负=上移）"},
         "border": {"type": "boolean", "default": False},
         "output_path": {"type": "string"}}}, "handler": tool_preview},
    {"name": "label_print", "description": "打印自定义标签（文本/条码/二维码，支持中文）。默认 40×30mm 纸、offset 10mm。",
     "inputSchema": {"type": "object", "properties": {
         "spec": {"type": "object"},
         "text_lines": {"type": "array"},
         "barcode": {"type": "object"},
         "qr": {"type": "object"},
         "width_mm": {"type": "number", "default": 40},
         "height_mm": {"type": "number", "default": 30},
         "offset_x_mm": {"type": "number", "default": 10},
         "offset_y_mm": {"type": "number", "default": 0, "description": "垂直微调 mm（负=上移）"},
         "border": {"type": "boolean", "default": False},
         "copies": {"type": "integer", "default": 1},
         "density": {"type": "integer", "default": 75, "description": "35/55/75/95"},
         "align": {"type": "string", "enum": ["left", "center", "right"], "default": "left"},
         "no_auto_learn": {"type": "boolean", "default": False},
         "form_feed": {"type": "boolean", "default": False},
         "model": {"type": "string"},
         "addr": {"type": "string"}}}, "handler": tool_print},
    {"name": "label_print_image", "description": "打印一张图片文件到标签（按目标 mm 尺寸缩放居中，含纸卷偏移）。",
     "inputSchema": {"type": "object", "properties": {
         "image_path": {"type": "string"},
         "width_mm": {"type": "number", "default": 40},
         "height_mm": {"type": "number", "default": 30},
         "offset_x_mm": {"type": "number", "default": 10},
         "copies": {"type": "integer", "default": 1},
         "density": {"type": "integer", "default": 75},
         "model": {"type": "string"},
         "addr": {"type": "string"}}, "required": ["image_path"]},
     "handler": tool_print_image},
]
TOOL_MAP = {t["name"]: t for t in TOOLS}


def handle(msg: dict):
    method = msg.get("method")
    mid = msg.get("id")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO}}
    if method in ("notifications/initialized", "initialized"):
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if method in ("tools/list", "listTools"):
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "tools": [{k: t[k] for k in ("name", "description", "inputSchema")} for t in TOOLS]}}
    if method in ("tools/call", "callTool"):
        params = msg.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        tool = TOOL_MAP.get(name)
        try:
            if not tool:
                raise KeyError(f"unknown tool {name!r}")
            result = (_proxy_http(name, args) if _http_up() else None) or tool["handler"](args)
            text = json.dumps(result, ensure_ascii=False, indent=2)
            is_error = result.get("ok") is False
        except Exception as e:  # noqa: BLE001
            text = f"{type(e).__name__}: {e}"
            is_error = True
            traceback.print_exc(file=sys.stderr)
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "content": [{"type": "text", "text": text}], "isError": is_error}}
    if mid is None:
        return None
    return {"jsonrpc": "2.0", "id": mid,
            "error": {"code": -32601, "message": f"method not found: {method}"}}


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        try:
            resp = handle(msg)
        except Exception as e:  # noqa: BLE001
            resp = {"jsonrpc": "2.0", "id": msg.get("id"),
                    "error": {"code": -32603, "message": str(e)}}
        if resp is not None:
            send(resp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
