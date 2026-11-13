#!/usr/bin/env python3
"""用 HTML 模板排版 → 无头 Chromium 渲染 → 黑白位图 → 直接打印。

示例::

    python3 tools/html_label.py --template templates/box_label.html \
        --data '{"NAME":"空白标准盒004","SHORT":"端子线","DESC":"MX1.25 / SH1.0 等","TYPE":"端子线","ID":"130"}' \
        --qr "https://part.gofor.ltd/storage_location/130" --preview /tmp/box.png --print
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import tempfile
import urllib.request

LABEL_API = os.environ.get("GOFOR_LABEL_API", "http://127.0.0.1:17600")
CHROMIUM = os.environ.get("CHROMIUM_BIN", "/usr/bin/chromium")
W, H = 320, 240


def zint_png_b64(payload: dict) -> str:
    req = urllib.request.Request(LABEL_API + "/api/element/render",
                                 data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    png = urllib.request.urlopen(req, timeout=30).read()
    return "data:image/png;base64," + base64.b64encode(png).decode()


def render_html(html: str, out_png: str) -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as f:
        f.write(html)
        path = f.name
    try:
        subprocess.run([
            CHROMIUM, "--headless=new", "--no-sandbox", "--disable-gpu",
            "--hide-scrollbars", "--force-device-scale-factor=2",
            f"--window-size={W},{H}", f"--screenshot={out_png}", f"file://{path}",
        ], check=True, capture_output=True, timeout=60)
    finally:
        os.unlink(path)


# 可打印区（像素，203dpi）：x 0..304 (38mm)、y 32..216 (4..27mm)
SAFE = (0, 32, 304, 216)


def to_bw(src: str, dst: str, check_safe: bool = True) -> None:
    from PIL import Image
    img = Image.open(src).convert("L").resize((W, H), Image.LANCZOS)
    img = img.point(lambda v: 0 if v < 140 else 255).convert("1")
    img.save(dst)
    if check_safe:
        px = img.convert("L").load()
        xs, ys = [], []
        for y in range(H):
            for x in range(W):
                if px[x, y] < 128:
                    xs.append(x); ys.append(y)
        if xs:
            box = (min(xs), min(ys), max(xs), max(ys))
            left, top, right, bottom = SAFE
            over = box[0] < left or box[1] < top or box[2] > right or box[3] > bottom
            print(f"[layout] content bbox={box} safe={SAFE} {'!! 越界' if over else 'OK'}")


def print_image(png_path: str, addr: str, width_mm=40, height_mm=30, offset_x_mm=10) -> dict:
    data = open(png_path, "rb").read()
    q = (f"?width_mm={width_mm}&height_mm={height_mm}&offset_x_mm={offset_x_mm}"
         f"&copies=1&density=75&addr={addr}")
    import time
    last: dict = {}
    for attempt in range(3):
        try:
            req = urllib.request.Request(LABEL_API + "/api/print/image" + q, data=data,
                                         headers={"Content-Type": "image/png"})
            last = json.loads(urllib.request.urlopen(req, timeout=120).read().decode())
            if last.get("ok"):
                return last
        except Exception as exc:  # noqa: BLE001
            last = {"ok": False, "error": str(exc)}
        time.sleep(2 + attempt * 2)   # 蓝牙链路偶尔断开，等待重连
    return last


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--template", required=True)
    ap.add_argument("--data", default="{}", help="JSON 变量表")
    ap.add_argument("--qr", help="二维码内容（生成 QR 图片插入 {{QR}}）")
    ap.add_argument("--icon-kind", default="", help="图标类型（预留）")
    ap.add_argument("--preview", help="输出 PNG")
    ap.add_argument("--print", dest="do_print", action="store_true")
    ap.add_argument("--addr", default="06:41:F4:57:C7:53")
    args = ap.parse_args()

    vars_ = json.loads(args.data)
    html = open(args.template, encoding="utf-8").read()
    if args.qr:
        b64 = zint_png_b64({"type": "qr", "data": args.qr, "size_mm": 13})
        vars_["QR"] = f'<img class="qr" src="{b64}">'
    else:
        vars_["QR"] = ""
    vars_.setdefault("ICON", "")
    if args.icon_kind:
        b64 = zint_png_b64({"type": "barcode", "data": args.icon_kind, "width_mm": 10, "height_mm": 10})
        vars_["ICON"] = f'<img class="icon" src="{b64}">'
    for k, v in vars_.items():
        html = html.replace("{{%s}}" % k, str(v))
    html = html.replace("{{", "").replace("}}", "")

    raw = (args.preview or "/tmp").rsplit(".", 1)[0] + "_raw.png"
    render_html(html, raw)
    out = args.preview or "/tmp/label.png"
    to_bw(raw, out)
    print(f"rendered {out}")
    if args.do_print:
        res = print_image(out, args.addr)
        print("print:", res.get("ok"), res.get("status") or res.get("error"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
