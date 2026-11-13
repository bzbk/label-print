#!/usr/bin/env python3
"""Part-DB 库存 → 标签（库位置顶 / 无条码 / 图标 + 内容说明 / 二维码进 Part-DB）。

用法::

    python3 tools/partdb_labels.py --location 空白标准盒004 --preview /tmp/box004
    python3 tools/partdb_labels.py --location 空白标准盒004 --limit 5 --print
    python3 tools/partdb_labels.py --ids 1,2,3 --preview /tmp/labels

版式（40×30mm，可打印区约 4–28mm）：
    y=4.2   库位（15px 粗体）
    y=9.0   零件名称（12px 粗体）
    y=12.8  内容说明（10px，优先描述，其次分类）
    y=16.6  数量（10px）
    左下     分类图标（8mm，程序绘制）
    右下     二维码（11mm → part.gofor.ltd/part/<id>）
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import sys
import urllib.request

API = os.environ.get("PARTDB_API", "https://part.gofor.ltd/api")
TOKEN = os.environ.get(
    "PARTDB_TOKEN",
    "tcp_e2294b3ab67a45397dc100bf965620fb98fd1e17631c10832dd8c025040c5dd0",
)
LABEL_API = os.environ.get("GOFOR_LABEL_API", "http://127.0.0.1:17600")


# ---------------------------------------------------------------- Part-DB
def api_get(path: str) -> dict:
    req = urllib.request.Request(API + path, headers={
        "Authorization": f"Bearer {TOKEN}", "Accept": "application/ld+json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def _members(data: dict) -> list:
    return data.get("hydra:member") or data.get("member") or []


def find_location(name: str) -> dict:
    locs = _members(api_get("/storage_locations?itemsPerPage=500"))
    exact = [x for x in locs if (x.get("name") or "").strip() == name.strip()]
    if exact:
        return exact[0]
    part = [x for x in locs if name.strip() in (x.get("name") or "")]
    if not part:
        raise SystemExit(f"找不到库位：{name}")
    return part[0]


def parts_in_location(loc_id: str) -> list[dict]:
    """返回 [{part, amount}]，同一 part 的多个 lot 数量相加。"""
    lots = _members(api_get(
        f"/part_lots?storage_location=/api/storage_locations/{loc_id}&itemsPerPage=200"))
    merged: dict[str, dict] = {}
    for lot in lots:
        part = lot.get("part") or {}
        if isinstance(part, str):
            part = api_get(part.replace("/api", ""))
        pid = str(part.get("@id", "")).split("/")[-1]
        if not pid:
            continue
        # lot 里内嵌的 part 往往是精简对象，补齐 description/category/attachments
        if not part.get("description") and not part.get("category"):
            try:
                part = api_get(f"/parts/{pid}")
            except Exception:  # noqa: BLE001
                pass
        entry = merged.setdefault(pid, {"part": part, "amount": 0})
        try:
            entry["amount"] += float(lot.get("amount") or 0)
        except (TypeError, ValueError):
            pass
    return list(merged.values())


def part_by_ids(ids: str) -> list[dict]:
    out = []
    for pid in ids.split(","):
        pid = pid.strip().strip("/").split("/")[-1]
        if pid:
            out.append({"part": api_get(f"/parts/{pid}"), "amount": None})
    return out


def category_name(part: dict) -> str:
    cat = part.get("category")
    try:
        if isinstance(cat, str):
            cat = api_get(cat.replace("/api", ""))
        return (cat or {}).get("name") or ""
    except Exception:  # noqa: BLE001
        return ""


def short_value(part: dict) -> str:
    for key in ("description", "comment"):
        v = part.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip().split("\n")[0][:26]
    return ""


# ---------------------------------------------------------------- 图标
ICON_MAP = {
    "电阻": "resistor", "电容": "capacitor", "电感": "inductor",
    "二极管": "diode", "三极管": "transistor", "MOSFET": "transistor",
    "芯片": "ic", "线性稳压器": "ic", "接插件": "connector",
    "五金件": "screw", "开关": "switch", "电路板": "pcb",
    "包装材料": "bag", "开发板": "pcb",
}


def icon_data_url(kind: str, size: int = 64) -> str:
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (size, size), "white")
    d = ImageDraw.Draw(img)
    m = size // 8
    if kind == "resistor":
        d.line([(m, size // 2), (size - m, size // 2)], fill="black", width=3)
        d.rectangle([m + 8, size // 2 - 8, size - m - 8, size // 2 + 8],
                    outline="black", width=3)
    elif kind == "capacitor":
        d.line([(m, size // 2), (size // 2 - 6, size // 2)], fill="black", width=3)
        d.line([(size // 2 + 6, size // 2), (size - m, size // 2)], fill="black", width=3)
        d.line([(size // 2 - 6, m + 8), (size // 2 - 6, size - m - 8)], fill="black", width=4)
        d.line([(size // 2 + 6, m + 8), (size // 2 + 6, size - m - 8)], fill="black", width=4)
    elif kind == "inductor":
        for i in range(3):
            d.arc([m + i * 14, size // 2 - 12, m + 20 + i * 14, size // 2 + 8],
                  180, 360, fill="black", width=3)
    elif kind == "diode":
        d.polygon([(size // 2 - 12, m + 14), (size // 2 - 12, size - m - 14),
                   (size // 2 + 10, size // 2)], outline="black", width=3)
        d.line([(size // 2 + 10, m + 14), (size // 2 + 10, size - m - 14)] and
               [(size // 2 + 10, m + 14), (size // 2 + 10, size - m - 14)],
               fill="black", width=4)
        d.line([(m, size // 2), (size // 2 - 12, size // 2)] and
               [(m, size // 2), (size // 2 - 12, size // 2)], fill="black", width=3)
        d.line([(size // 2 + 10, size // 2), (size - m, size // 2)], fill="black", width=3)
    elif kind == "transistor":
        d.ellipse([m + 6, m + 6, size - m - 6, size - m - 6], outline="black", width=3)
        d.line([(size // 2, m + 12), (size // 2, size - m - 12)], fill="black", width=3)
        d.line([(size // 2, size // 2 - 6), (size - m - 10, m + 14)], fill="black", width=3)
        d.line([(size // 2, size // 2 + 6), (size - m - 10, size - m - 14)], fill="black", width=3)
    elif kind == "ic":
        d.rectangle([m + 4, m + 10, size - m - 4, size - m - 10], outline="black", width=3)
        for i in range(3):
            x = m + 12 + i * 12
            d.line([(x, m + 4), (x, m + 10)], fill="black", width=3)
            d.line([(x, size - m - 10), (x, size - m - 4)], fill="black", width=3)
    elif kind == "connector":
        d.rectangle([m, size // 2 - 10, size - m, size // 2 + 10], outline="black", width=3)
        for i in range(4):
            x = m + 10 + i * 12
            d.line([(x, size // 2 - 6), (x, size // 2 + 6)], fill="black", width=4)
    elif kind == "screw":
        d.polygon([(size // 2, m + 6), (size - m - 8, size // 2),
                   (size // 2, size - m - 6), (m + 8, size // 2)],
                  outline="black", width=3)
        d.ellipse([size // 2 - 8, size // 2 - 8, size // 2 + 8, size // 2 + 8],
                  outline="black", width=2)
    elif kind == "switch":
        d.rectangle([m + 6, size // 2 - 12, size - m - 6, size // 2 + 12],
                    outline="black", width=3)
        d.line([(size // 2, size // 2), (size // 2 + 12, size // 2 - 10)],
               fill="black", width=3)
    elif kind == "pcb":
        d.rectangle([m + 2, m + 8, size - m - 2, size - m - 8], outline="black", width=3)
        d.line([(m + 10, m + 16), (size - m - 10, m + 16)], fill="black", width=2)
        d.line([(m + 10, m + 28), (size - m - 16, m + 28)], fill="black", width=2)
    elif kind == "bag":
        d.rectangle([m + 8, m + 10, size - m - 8, size - m - 6], outline="black", width=3)
        d.line([(m + 8, m + 10), (m + 16, m + 4)], fill="black", width=2)
        d.line([(size - m - 8, m + 10), (size - m - 16, m + 4)], fill="black", width=2)
    else:
        d.ellipse([m + 10, m + 10, size - m - 10, size - m - 10], outline="black", width=3)
        d.line([(size // 2, m + 20), (size // 2, size - m - 20)], fill="black", width=3)
        d.line([(m + 20, size // 2), (size - m - 20, size // 2)], fill="black", width=3)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def icon_for(category: str) -> str:
    for key, kind in ICON_MAP.items():
        if key in category:
            return icon_data_url(kind)
    return icon_data_url("generic")


# ---------------------------------------------------------------- 标签
def build_spec(part: dict, location: str, amount: float | None = None) -> dict:
    pid = str(part.get("@id", "")).split("/")[-1]
    name = (part.get("name") or "?").strip()
    cat = category_name(part)
    value = short_value(part) or cat or "-"
    qty = f"数量 {int(amount)}" if amount is not None else ""
    items = [
        {"type": "text", "text": location, "size": 15, "bold": True,
         "x_mm": 1, "y_mm": 4.2},
        {"type": "text", "text": name, "size": 12, "bold": True,
         "x_mm": 1, "y_mm": 9.0},
        {"type": "text", "text": value, "size": 10,
         "x_mm": 1, "y_mm": 12.8},
        {"type": "text", "text": qty or "-", "size": 10,
         "x_mm": 1, "y_mm": 16.6},
        {"type": "image", "src": icon_for(cat), "width_mm": 8, "height_mm": 8,
         "x_mm": 1, "y_mm": 20.0},
        {"type": "qr", "data": f"https://part.gofor.ltd/part/{pid}",
         "size_mm": 11, "x_mm": 26, "y_mm": 16.8},
    ]
    return {"width_mm": 40, "height_mm": 30, "offset_x_mm": 10,
            "border": False, "density": 75, "copies": 1, "items": items}


def preview(spec: dict, out_png: str) -> int:
    req = urllib.request.Request(LABEL_API + "/api/preview",
                                 data=json.dumps(spec).encode(),
                                 headers={"Content-Type": "application/json"})
    png = urllib.request.urlopen(req, timeout=60).read()
    os.makedirs(os.path.dirname(out_png) or ".", exist_ok=True)
    with open(out_png, "wb") as f:
        f.write(png)
    return len(png)


def print_one(spec: dict, addr: str) -> dict:
    body = dict(spec)
    body["addr"] = addr
    req = urllib.request.Request(LABEL_API + "/api/print",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=180).read().decode())


def main() -> int:
    ap = argparse.ArgumentParser(description="Part-DB → 标签")
    ap.add_argument("--location", help="库位名称，如 空白标准盒004")
    ap.add_argument("--ids", help="part id 列表，如 1,2,3")
    ap.add_argument("--limit", type=int, default=0, help="最多处理多少个零件（0=全部）")
    ap.add_argument("--preview", help="预览输出目录（不打印）")
    ap.add_argument("--print", dest="do_print", action="store_true")
    ap.add_argument("--addr", default="06:41:F4:57:C7:53")
    args = ap.parse_args()

    if args.ids:
        entries = part_by_ids(args.ids)
        loc_name = "指定零件"
    else:
        loc = find_location(args.location or "空白标准盒004")
        loc_name = loc.get("name", "")
        entries = parts_in_location(str(loc.get("@id", "")).split("/")[-1])
    if args.limit:
        entries = entries[: args.limit]
    print(f"库位：{loc_name} | 零件 {len(entries)} 个")

    ok = 0
    for entry in entries:
        part = entry["part"]
        name = part.get("name", "?")
        spec = build_spec(part, loc_name, entry.get("amount"))
        if args.preview:
            safe = name.replace("/", "_")
            path = os.path.join(args.preview, f"{safe}.png")
            n = preview(spec, path)
            print(f"  ✓ {name}  {n}B → {path}")
        if args.do_print:
            res = print_one(spec, args.addr)
            good = bool(res.get("ok"))
            print(f"  {'✓' if good else '✗'} 打印 {name}: {res.get('status') or res.get('error')}")
            ok += 1 if good else 0
    if args.do_print:
        print(f"打印完成 {ok}/{len(entries)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
