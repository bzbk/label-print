#!/usr/bin/env python3
"""服务端自测 —— **绝不打印**，只做渲染/编码/HTTP 校验。

覆盖内容
--------
1. 依赖检查：Pillow / zint / liblzo2 / 中文字体；
2. spec 契约：合法解析、浓度吸附、非法输入报错；
3. 渲染：毫米 -> 像素尺寸（40×30 => 320×240）、边框、绝对定位、纵向堆叠；
4. 位图：1bpp / MSB 最左 / ``(r+g+b)//3 <= 128`` 判黑 的字节级校验；
5. LZO1X-1 压缩回环 + ``GS v00`` 帧结构解析（行宽/行数/块 ≤ 1024B）；
6. HTTP API：health/models/devices/preview(GET+POST)/status(离线)/错误处理/CORS。

用法::

    cd server
    python3 selftest.py                # 全部测试（不碰打印机）
    python3 selftest.py --with-status  # 额外对真实打印机做一次只读状态查询（不打印）

预览图输出到 ``/tmp/gongfeng_selftest/``。退出码 0 = 全部通过。
"""
from __future__ import annotations

import argparse
import io
import json
import struct
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from PIL import Image, ImageDraw  # noqa: E402

from gongfeng_print import APP_NAME, __version__  # noqa: E402
from gongfeng_print.printers import create, models_payload  # noqa: E402
from gongfeng_print.printers.hprt_m1 import (  # noqa: E402
    BLACK_THRESHOLD_SUM,
    CMD_START_PRINT,
    build_stream,
    image_to_raster,
    lzo1x_1,
    lzo1x_decompress,
    parse_status,
)
from gongfeng_print.render import (  # noqa: E402
    DPI,
    fonts_available,
    mm_to_px,
    render_label,
    render_png,
    zint_available,
)
from gongfeng_print.server import Api, create_server  # noqa: E402
from gongfeng_print.spec import SpecError, parse_spec  # noqa: E402

OUT_DIR = Path(tempfile.gettempdir()) / "gongfeng_selftest"
PASS = "PASS"
FAIL = "FAIL"


class Checker:
    def __init__(self) -> None:
        self.passed = 0
        self.failed = 0
        self.failures: list[str] = []

    def check(self, name: str, condition: bool, detail: str = "") -> bool:
        if condition:
            self.passed += 1
            print(f"  [{PASS}] {name}")
            return True
        self.failed += 1
        self.failures.append(f"{name}{f'：{detail}' if detail else ''}")
        print(f"  [{FAIL}] {name}{f'：{detail}' if detail else ''}")
        return False

    def eq(self, name: str, actual, expected) -> bool:
        return self.check(name, actual == expected, f"期望 {expected!r}，实际 {actual!r}")

    def section(self, title: str) -> None:
        print(f"\n== {title} ==")

    def summary(self) -> int:
        print(f"\n---- 结果：{self.passed} 通过 / {self.failed} 失败 ----")
        if self.failures:
            print("失败项：")
            for item in self.failures:
                print(f"  - {item}")
            return 1
        print(f"全部通过（{APP_NAME} v{__version__}，输出目录 {OUT_DIR}）")
        return 0


# ---------------------------------------------------------------- 1. 依赖
def test_deps(c: Checker) -> None:
    c.section("1. 依赖")
    fonts = fonts_available()
    c.check("中文字体可用", fonts.get("regular") is not None, str(fonts))
    c.check("zint 可执行", zint_available())
    from gongfeng_print.printers.hprt_m1 import _lzo_lib

    try:
        _lzo_lib()
        lzo_ok, lzo_err = True, ""
    except Exception as exc:  # noqa: BLE001
        lzo_ok, lzo_err = False, str(exc)
    c.check("liblzo2 可用", lzo_ok, lzo_err)


# ---------------------------------------------------------------- 2. spec
def test_spec(c: Checker) -> None:
    c.section("2. spec 契约")
    spec = parse_spec({
        "width_mm": 40, "height_mm": 30, "border": True, "density": 80,
        "items": [
            {"type": "text", "text": "测试", "size": 24, "bold": True},
            {"type": "barcode", "data": "ABC123", "symbology": "code128"},
            {"type": "qr", "data": "https://example.com"},
            {"type": "gap", "mm": 1},
        ],
    })
    c.eq("默认值 width_mm", spec.width_mm, 40.0)
    c.eq("像素宽度 40mm", spec.width_px, 320)
    c.eq("像素高度 30mm", spec.height_px, 240)
    c.eq("默认纸卷偏移 offset_x_mm", spec.offset_x_mm, 10.0)
    c.eq("纸卷偏移像素 10mm", spec.offset_x_px, 80)
    c.eq("位图宽度 = 偏移 + 标签宽", spec.canvas_width_px, 400)
    offset_snap = parse_spec({"offset_x_mm": 0.7, "items": []})
    c.eq("offset_x_mm 吸附 0.5 步进", offset_snap.offset_x_mm, 0.5)
    try:
        parse_spec({"offset_x_mm": 25, "items": []})
        c.check("拒绝越界 offset_x_mm", False, "未抛 SpecError")
    except SpecError:
        c.check("拒绝越界 offset_x_mm", True)
    c.eq("浓度吸附 80 -> 75", spec.density, 75)
    c.eq("items 数量", len(spec.items), 4)
    c.check("x/y 缺省为 None",
            spec.items[0].get("x_mm") is None and spec.items[0].get("y_mm") is None)

    # 包装形式 {"spec": {...}}
    wrapped = parse_spec({"spec": {"width_mm": 50, "height_mm": 30, "items": []}})
    c.eq("支持 {\"spec\": ...} 包装", wrapped.width_px, mm_to_px(50))

    # no_auto_learn
    no_learn = parse_spec({"no_auto_learn": True, "items": []})
    c.check("no_auto_learn 生效", no_learn.auto_learn is False)

    for bad, label in (
        ({"width_mm": 1, "items": []}, "宽度过小"),
        ({"width_mm": 200, "items": []}, "宽度过大"),
        ({"items": [{"type": "unknown"}]}, "未知元素类型"),
        ({"items": [{"type": "barcode", "data": "X", "symbology": "nope"}]}, "未知符号体系"),
        ({"items": "not-a-list"}, "items 非数组"),
        ("{bad json", "非法 JSON"),
    ):
        try:
            parse_spec(bad)
            c.check(f"拒绝非法 spec（{label}）", False, "未抛 SpecError")
        except SpecError:
            c.check(f"拒绝非法 spec（{label}）", True)


# ---------------------------------------------------------------- 3. 渲染
def test_render(c: Checker) -> None:
    c.section("3. 渲染")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    spec = parse_spec({
        "width_mm": 40, "height_mm": 30, "border": True, "density": 75,
        "items": [
            {"type": "text", "text": "物料 A-102", "size": 28, "bold": True,
             "x_mm": 2, "y_mm": 2},
            {"type": "barcode", "data": "ABC123", "symbology": "code128",
             "height_mm": 10, "x_mm": 2, "y_mm": 14},
            {"type": "qr", "data": "https://example.com", "size_mm": 12,
             "x_mm": 25, "y_mm": 15},
        ],
    })
    img = render_label(spec)
    path = OUT_DIR / "preview_40x30.png"
    img.save(path)
    c.eq("40×30mm + 10mm 偏移渲染尺寸", img.size, (400, 240))
    extrema = img.convert("L").getextrema()
    c.check("预览非空白", extrema[0] < 128, f"灰度范围 {extrema}")
    c.check("预览文件已写入", path.exists() and path.stat().st_size > 500,
            str(path))

    big = render_label(parse_spec({
        "width_mm": 40, "height_mm": 60, "border": False,
        "items": [{"type": "text", "text": "40x60mm", "size": 30}],
    }))
    big.save(OUT_DIR / "preview_40x60.png")
    c.eq("40×60mm + 10mm 偏移渲染尺寸", big.size, (400, 480))

    # 边框像素：左上角应为黑，内部（中心）应为白（无内容时）
    border_img = render_label(parse_spec({
        "width_mm": 40, "height_mm": 30, "offset_x_mm": 10,
        "border": True, "items": []}))
    border_img.save(OUT_DIR / "preview_border.png")
    px = border_img.convert("L").load()
    c.eq("边框包含偏移的总宽", border_img.size, (400, 240))
    c.check("偏移区左侧 80px 为白纸", px[0, 0] > 128 and px[79, 120] > 128,
            f"px[0,0]={px[0, 0]}, px[79,120]={px[79, 120]}")
    c.check("标签区左上角边框为黑", px[80, 0] < 128, f"{px[80, 0]}")
    c.check("标签区右下角边框为黑", px[399, 239] < 128, f"{px[399, 239]}")
    c.check("无内容标签内部为白", px[240, 120] > 128, f"{px[240, 120]}")

    # 纸卷偏移专项：offset=0 -> 320px；offset=10 -> 400px 且内容左边界在 80px
    no_offset = render_label(parse_spec({
        "width_mm": 40, "height_mm": 30, "offset_x_mm": 0, "border": True,
        "items": []}))
    no_offset.save(OUT_DIR / "preview_offset_0.png")
    c.eq("offset=0 时位图宽 320", no_offset.size, (320, 240))
    offset_img = render_label(parse_spec({
        "width_mm": 40, "height_mm": 30, "offset_x_mm": 10, "border": True,
        "items": [{"type": "text", "text": "OFFSET", "size": 30,
                   "x_mm": 2, "y_mm": 10}]}))
    offset_img.save(OUT_DIR / "preview_offset_10.png")
    gray = offset_img.convert("L")
    dark_cols = [x for x in range(gray.width)
                 if any(gray.getpixel((x, y)) < 128 for y in range(240))]
    c.eq("offset=10 时位图宽 400", offset_img.size, (400, 240))
    c.eq("offset=10 内容左边界在 80px", min(dark_cols), 80)
    c.check("offset=10 内容不越过右边界", max(dark_cols) <= 399,
            f"max={max(dark_cols)}")

    # 绝对定位：x_mm=10,y_mm=5 -> 偏移 80px + 80px = 160px；其左侧无内容
    pos_img = render_label(parse_spec({
        "width_mm": 40, "height_mm": 30, "offset_x_mm": 10, "border": False,
        "items": [{"type": "text", "text": "X", "size": 40,
                   "bold": True, "x_mm": 10, "y_mm": 5}]}))
    pos_img.save(OUT_DIR / "preview_absolute.png")
    left_px = mm_to_px(10) + mm_to_px(10)  # 纸卷偏移 + x_mm
    top_px = mm_to_px(5)
    pos_gray = pos_img.convert("L")
    left_dark = [x for x in range(left_px) for y in range(240)
                 if pos_gray.getpixel((x, y)) < 128]
    c.check("绝对定位 x_mm=10 左侧留白", not left_dark,
            f"最左黑像素 x={min(left_dark) if left_dark else None}")
    top_dark = [y for y in range(240) for x in range(pos_img.width)
                if pos_gray.getpixel((x, y)) < 128]
    c.check("绝对定位 y_mm=5 顶部以上无内容",
            top_dark and min(top_dark) >= top_px - 1,
            f"最上黑像素 y={min(top_dark) if top_dark else None}")

    # 纵向堆叠：两个无坐标文本，第二个应在第一个下方
    flow_img = render_label(parse_spec({
        "width_mm": 40, "height_mm": 30, "offset_x_mm": 0, "border": False,
        "items": [{"type": "text", "text": "AAAA", "size": 30},
                  {"type": "text", "text": "BBBB", "size": 30}]}))
    flow_img.save(OUT_DIR / "preview_flow.png")
    gray = flow_img.convert("L")
    row_has = [any(gray.getpixel((x, y)) < 128 for x in range(320)) for y in range(240)]
    runs, in_run = [], False
    for y, has in enumerate(row_has):
        if has and not in_run:
            runs.append(y)
            in_run = True
        elif not has:
            in_run = False
    c.check("纵向堆叠产生两段文本", len(runs) >= 2, f"文本起始行 {runs}")

    png = render_png(spec)
    c.check("PNG 魔数正确", png[:8] == b"\x89PNG\r\n\x1a\n", png[:8].hex())
    w, h = struct.unpack(">II", png[16:24])
    c.eq("PNG 内嵌像素尺寸（含偏移）", (w, h), (400, 240))


# ---------------------------------------------------------------- 4. 位图
def test_raster(c: Checker) -> None:
    c.section("4. 1bpp 位图")
    c.eq("黑色阈值常量", BLACK_THRESHOLD_SUM, 386)

    img = Image.new("RGB", (16, 4), "white")
    draw = ImageDraw.Draw(img)
    draw.point([(0, 0), (8, 0)], fill=(0, 0, 0))          # 第 0 行第 0/8 像素黑
    draw.point([(1, 2)], fill=(128, 128, 128))            # 恰好黑
    draw.point([(2, 2)], fill=(129, 129, 129))            # 恰好白
    draw.point([(9, 2)], fill=(0, 0, 0))
    draw.point([(15, 3)], fill=(0, 0, 0))

    raster, wbytes, rows = image_to_raster(img)
    c.eq("行字节数 ceil(16/8)", (wbytes, rows), (2, 4))
    expected = bytes([0x80, 0x80,  0x00, 0x00,  0x40, 0x40,  0x00, 0x01])
    c.eq("位图字节（MSB 最左 / 1=黑）", raster.hex(), expected.hex())
    c.check("(128,128,128) 判黑", raster[4] & 0x40, f"byte={raster[4]:#04x}")
    c.check("(129,129,129) 判白", not (raster[4] & 0x20), f"byte={raster[4]:#04x}")

    img.save(OUT_DIR / "raster_input.png")


# ---------------------------------------------------------------- 5. 帧
def test_frames(c: Checker) -> None:
    c.section("5. LZO 帧")
    raw = bytes(range(256)) * 8
    comp = lzo1x_1(raw)
    back = lzo1x_decompress(comp, len(raw))
    c.eq("LZO1X-1 压缩回环", back, raw)
    c.check("LZO 压缩确实缩小数据", len(comp) < len(raw), f"{len(raw)} -> {len(comp)}")

    spec = parse_spec({
        "width_mm": 40, "height_mm": 30, "border": True,
        "items": [{"type": "text", "text": "FRAME TEST 帧", "size": 28,
                   "bold": True, "x_mm": 2, "y_mm": 2},
                  {"type": "barcode", "data": "ABC123", "height_mm": 10,
                   "x_mm": 2, "y_mm": 14},
                  {"type": "qr", "data": "https://example.com", "size_mm": 12,
                   "x_mm": 25, "y_mm": 15}]})
    image = render_label(spec)
    info = build_stream(image, density=75, align="left", form_feed=False)
    raster, wbytes, rows = image_to_raster(image)
    c.eq("流统计行宽", info.row_bytes, wbytes)
    c.eq("流统计行数", info.rows, rows)
    c.eq("总字节数 == rows*wbytes", len(raster), wbytes * rows)

    data = info.stream
    c.check("以 setp 纸张类型开头", data[:5] == b"\x1dsetp", data[:8].hex())
    c.check("包含 align 命令 1B 61", b"\x1b\x61" in data)
    c.check("包含开始打印 1B 1B 01 5A 00", CMD_START_PRINT in data)
    c.check("未要求时不走纸", b"\x1d\x66\xc0\x03" not in data)

    # 逐帧解析
    frames_written = 0
    offset = data.find(b"\x1dv00")
    chunks: list[bytes] = []
    c.check("找到 GS v00 帧", offset >= 0, f"offset={offset}")
    while offset >= 0 and offset < len(data):
        wbytes_f = int.from_bytes(data[offset + 4:offset + 6], "little")
        rows_f = int.from_bytes(data[offset + 6:offset + 8], "little")
        lzo_len = int.from_bytes(data[offset + 8:offset + 12], "little")
        payload = data[offset + 12:offset + 12 + lzo_len]
        chunk = lzo1x_decompress(payload, wbytes_f * rows_f)
        c.check(f"帧 {frames_written}: 行宽 {wbytes_f}", wbytes_f == wbytes)
        c.check(f"帧 {frames_written}: 解压长度", len(chunk) == wbytes_f * rows_f,
                f"{len(chunk)} != {wbytes_f * rows_f}")
        chunks.append(chunk)
        frames_written += 1
        offset = data.find(b"\x1dv00", offset + 12 + lzo_len)

    merged = b"".join(chunks)
    c.eq("所有帧拼回原始位图", merged, raster)
    c.eq("帧数 == ceil(块数)", frames_written, info.frames)
    c.check("每帧为整行且 ≤1024B",
            all(len(ch) % wbytes == 0 and len(ch) <= 1024 for ch in chunks),
            f"块大小 {[len(ch) for ch in chunks]}")

    # 走纸开关
    feed_info = build_stream(image, form_feed=True)
    c.check("form_feed=True 时附加走纸命令",
            feed_info.stream.endswith(b"\x1d\x66\xc0\x03"))

    # 状态解析（模拟回包）
    st_ok = parse_status(b"\x00\x00pooli_sta=\x00")
    c.check("状态解析：正常", st_ok.ok and st_ok.value == 0, st_ok.summary)
    st_bad = parse_status(b"\x11\x00pooli_sta=\x11")  # bit0 缺纸 + bit4 开盖
    c.check("状态解析：缺纸+开盖",
            set(st_bad.errors) == {"缺纸", "开盖"}, str(st_bad.errors))
    st_locate = parse_status(b"\x40\x00pooli_sta=\x40")
    c.check("状态解析：定位失败", st_locate.locate_fail, st_locate.summary)
    st_none = parse_status(b"")
    c.check("状态解析：无响应", st_none.value == -1 and st_none.errors == ("无响应",),
            str(st_none.to_dict()))


# ---------------------------------------------------------------- 6. HTTP
def _http(method: str, url: str, body: bytes | None = None,
          headers: dict[str, str] | None = None):
    req = urllib.request.Request(url, data=body, method=method,
                                 headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def test_http(c: Checker, with_status: bool = False) -> None:
    c.section("6. HTTP API（服务进程内临时端口，不打印）")
    api = Api(bt_timeout=3.0, bt_retries=0)
    server = create_server("127.0.0.1", 0, api=api, verbose=False)
    port = server.server_address[1]
    base = f"http://127.0.0.1:{port}"
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1},
                              daemon=True)
    thread.start()
    try:
        # health
        status, headers, body = _http("GET", base + "/api/health")
        health = json.loads(body)
        c.eq("GET /api/health 状态码", status, 200)
        c.check("health ok", health.get("ok") is True, str(health)[:200])
        c.check("health 报告依赖", "deps" in health, str(health.get("deps"))[:200])

        # models
        status, headers, body = _http("GET", base + "/api/models")
        models = json.loads(body)
        c.eq("GET /api/models 状态码", status, 200)
        c.check("models ok", models.get("ok") is True)
        ids = [m["model_id"] for m in models.get("models", [])]
        c.check("包含 hprt-m1", "hprt-m1" in ids, str(ids))
        m1 = next((m for m in models["models"] if m["model_id"] == "hprt-m1"), {})
        c.eq("M1 dpi", m1.get("dpi"), 203)
        c.eq("M1 默认标签", m1.get("default_label"),
             {"width_mm": 40.0, "height_mm": 30.0})
        c.eq("M1 浓度档", m1.get("densities"), [35, 55, 75, 95])

        # devices（蓝牙不可用时也必须是 JSON，而不是异常）
        status, headers, body = _http("GET", base + "/api/devices")
        devices = json.loads(body)
        c.eq("GET /api/devices 状态码", status, 200)
        c.check("devices 返回 JSON dict", isinstance(devices, dict), str(devices)[:120])
        c.check("devices 含 devices 列表", isinstance(devices.get("devices"), list))

        # preview POST
        spec = {"width_mm": 40, "height_mm": 30, "border": True, "density": 75,
                "items": [
                    {"type": "text", "text": "物料 A-102", "size": 28,
                     "bold": True, "x_mm": 2, "y_mm": 2},
                    {"type": "barcode", "data": "ABC123", "symbology": "code128",
                     "height_mm": 10, "x_mm": 2, "y_mm": 14},
                    {"type": "qr", "data": "https://example.com", "size_mm": 12,
                     "x_mm": 25, "y_mm": 15}]}
        status, headers, body = _http(
            "POST", base + "/api/preview",
            body=json.dumps(spec).encode(), headers={"Content-Type": "application/json"})
        c.eq("POST /api/preview 状态码", status, 200)
        c.check("preview 返回 image/png",
                headers.get("Content-Type", "").startswith("image/png"),
                headers.get("Content-Type", ""))
        c.check("preview PNG 魔数", body[:8] == b"\x89PNG\r\n\x1a\n", body[:8].hex())
        w, h = struct.unpack(">II", body[16:24])
        c.eq("preview PNG 尺寸 400×240（40mm + 10mm 偏移）", (w, h), (400, 240))
        c.eq("preview 尺寸响应头（含偏移）", headers.get("X-Label-Width-Px"), "400")
        c.eq("preview 高度响应头", headers.get("X-Label-Height-Px"), "240")
        c.eq("preview 偏移响应头", headers.get("X-Label-Offset-X-Mm"), "10.0")
        (OUT_DIR / "api_preview_40x30.png").write_bytes(body)

        # 显式 offset_x_mm=0 -> 320×240
        spec_no_offset = dict(spec, offset_x_mm=0)
        status, headers, body = _http(
            "POST", base + "/api/preview",
            body=json.dumps(spec_no_offset).encode(),
            headers={"Content-Type": "application/json"})
        w, h = struct.unpack(">II", body[16:24])
        c.eq("offset=0 时 preview PNG 为 320×240", (w, h), (320, 240))
        (OUT_DIR / "api_preview_offset_0.png").write_bytes(body)

        # 宽度+偏移超过 400px 上限 -> 400 明确报错
        status, headers, body = _http(
            "POST", base + "/api/preview",
            body=json.dumps({"width_mm": 50, "offset_x_mm": 20,
                             "items": []}).encode(),
            headers={"Content-Type": "application/json"})
        payload = json.loads(body)
        c.eq("宽度+偏移超限状态码", status, 400)
        c.check("宽度+偏移超限错误明确",
                "纸卷偏移" in payload.get("error", "")
                and "400" in payload.get("error", ""), str(payload)[:220])

        # preview GET ?spec=
        quoted = urllib.parse.quote(json.dumps(spec))
        status, headers, body = _http("GET", base + "/api/preview?spec=" + quoted)
        c.eq("GET /api/preview?spec= 状态码", status, 200)
        c.check("GET preview 为 PNG",
                headers.get("Content-Type", "").startswith("image/png"))
        w, h = struct.unpack(">II", body[16:24])
        c.eq("GET preview 尺寸（含偏移）", (w, h), (400, 240))

        # preview GET 便捷参数
        status, headers, body = _http(
            "GET", base + "/api/preview?text=HELLO&width_mm=40&height_mm=30")
        c.eq("GET /api/preview?text= 状态码", status, 200)
        c.check("GET preview?text 为 PNG",
                headers.get("Content-Type", "").startswith("image/png"))

        # 打印机关机/不可达时：/api/status 必须 ok:false 而不是 500
        status, headers, body = _http(
            "GET", base + "/api/status?addr=00:00:00:00:00:00")
        payload = json.loads(body)
        c.eq("离线 /api/status 状态码（不得 500）", status, 200)
        c.check("离线 /api/status ok=false", payload.get("ok") is False,
                str(payload)[:200])
        c.check("离线 /api/status 带 error", bool(payload.get("error")),
                str(payload)[:200])
        c.check("离线 /api/status 带 value=-1", payload.get("value") == -1,
                str(payload.get("value")))

        # 未知机型 -> 明确错误
        status, headers, body = _http(
            "POST", base + "/api/preview",
            body=json.dumps({"model": "no-such-printer", "items": []}).encode(),
            headers={"Content-Type": "application/json"})
        payload = json.loads(body)
        c.eq("未知机型状态码", status, 400)
        c.check("未知机型错误明确",
                payload.get("ok") is False and "不支持" in payload.get("error", ""),
                str(payload)[:200])
        c.check("未知机型列出已注册",
                "hprt-m1" in payload.get("supported_models", []),
                str(payload.get("supported_models")))

        # 非法请求体
        status, headers, body = _http(
            "POST", base + "/api/preview", body=b"{bad json",
            headers={"Content-Type": "application/json"})
        payload = json.loads(body)
        c.eq("非法 JSON 状态码", status, 400)
        c.check("非法 JSON ok=false", payload.get("ok") is False)

        # 超宽标签（超过 M1 纸宽 50mm）-> 400 明确错误
        status, headers, body = _http(
            "POST", base + "/api/preview",
            body=json.dumps({"width_mm": 60, "items": []}).encode(),
            headers={"Content-Type": "application/json"})
        payload = json.loads(body)
        c.eq("超宽标签状态码", status, 400)
        c.check("超宽标签错误明确", "宽度" in payload.get("error", ""),
                str(payload)[:160])

        # POST /api/print 对未知机型：绝不触碰打印机
        status, headers, body = _http(
            "POST", base + "/api/print",
            body=json.dumps({"model": "no-such-printer", "items": []}).encode(),
            headers={"Content-Type": "application/json"})
        payload = json.loads(body)
        c.eq("未知机型打印状态码", status, 400)
        c.check("未知机型打印 ok=false", payload.get("ok") is False)

        # 未知路由
        status, headers, body = _http("GET", base + "/api/nope")
        c.eq("未知路由状态码", status, 404)
        c.check("未知路由返回 JSON", json.loads(body).get("ok") is False)

        # CORS 预检
        status, headers, body = _http(
            "OPTIONS", base + "/api/preview",
            headers={"Origin": "http://localhost:5173",
                     "Access-Control-Request-Method": "POST"})
        c.eq("OPTIONS 预检状态码", status, 204)
        c.eq("CORS 放行 localhost:5173",
             headers.get("Access-Control-Allow-Origin"), "http://localhost:5173")
        c.check("CORS 允许 POST",
                "POST" in headers.get("Access-Control-Allow-Methods", ""))
        status, headers, body = _http(
            "GET", base + "/api/health",
            headers={"Origin": "http://127.0.0.1:5173"})
        c.eq("CORS 放行 127.0.0.1:5173",
             headers.get("Access-Control-Allow-Origin"), "http://127.0.0.1:5173")

        # 首页
        status, headers, body = _http("GET", base + "/")
        c.eq("GET / 状态码", status, 200)
        c.check("GET / 是 HTML", b"gongfeng" in body.lower()
                or "打印".encode() in body)

        # 可选：真实打印机只读状态查询（不打印、不走纸）
        if with_status:
            status, headers, body = _http("GET", base + "/api/status", None)
            payload = json.loads(body)
            c.eq("真实 /api/status 状态码（不打印）", status, 200)
            c.check("真实 /api/status 返回 ok 字段", "ok" in payload,
                    str(payload)[:200])
            print(f"     真实状态：{json.dumps(payload, ensure_ascii=False)[:200]}")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# ---------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="共风标签打印服务端自测（不打印）")
    parser.add_argument("--with-status", action="store_true",
                        help="额外对真实打印机做一次只读状态查询（不打印、不走纸）")
    args = parser.parse_args(argv)

    print(f"共风标签打印 · 服务端自测 {APP_NAME} v{__version__}")
    print(f"Python {sys.version.split()[0]}  输出目录 {OUT_DIR}")
    t0 = time.time()
    c = Checker()
    test_deps(c)
    test_spec(c)
    test_render(c)
    test_raster(c)
    test_frames(c)
    test_http(c, with_status=args.with_status)
    print(f"\n耗时 {time.time() - t0:.2f}s")
    return c.summary()


if __name__ == "__main__":
    raise SystemExit(main())
