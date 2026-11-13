#!/usr/bin/env python3
"""从 Part-DB 拉取库存零件，生成「共风标签打印」的四要素标签（预览/直接打印）。

用法::

    python3 tools/partdb_labels.py --search SS8050 --preview /tmp/part_labels
    python3 tools/partdb_labels.py --ids 1,2,3 --print --addr 06:41:F4:57:C7:53
    python3 tools/partdb_labels.py --location "5-4" --limit 10 --preview /tmp/part_labels

依赖：仅标准库 + Pillow（预览图）/ requests 不需要。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request

API = os.environ.get("PARTDB_API", "https://part.gofor.ltd/api")
TOKEN = os.environ.get(
    "PARTDB_TOKEN",
    "tcp_e2294b3ab67a45397dc100bf965620fb98fd1e17631c10832dd8c025040c5dd0",
)
LABEL_API = os.environ.get("GOFOR_LABEL_API", "http://127.0.0.1:17600")


def api_get(path: str) -> dict:
    req = urllib.request.Request(API + path, headers={
        "Authorization": f"Bearer {TOKEN}", "Accept": "application/ld+json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def list_parts(search: str = "", ids: str = "", limit: int = 20) -> list[dict]:
    if ids:
        out = []
        for pid in ids.split(","):
            pid = pid.strip().strip("/").split("/")[-1]
            if pid:
                out.append(api_get(f"/parts/{pid}"))
        return out
    params = {"itemsPerPage": str(limit)}
    if search:
        params["name"] = search
    q = urllib.parse.urlencode(params)
    data = api_get(f"/parts?{q}")
    return data.get("hydra:member") or data.get("member") or []


def location_of(part: dict) -> str:
    pid = part.get("@id", "").split("/")[-1]
    if not pid:
        return ""
    try:
        lots = api_get(f"/part_lots?part=/api/parts/{pid}")
        items = lots.get("hydra:member") or lots.get("member") or []
        names = []
        for lot in items:
            loc = lot.get("storage_location")
            if isinstance(loc, dict):
                loc = loc.get("name") or loc.get("@id")
            if loc and str(loc) not in names:
                names.append(str(loc))
        return " / ".join(names[:2])
    except Exception:  # noqa: BLE001
        return ""


def value_of(part: dict) -> str:
    for key in ("description", "comment"):
        v = part.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip().split("\n")[0][:28]
    return ""


def build_spec(part: dict, loc: str) -> dict:
    pid = part.get("@id", "").split("/")[-1]
    name = (part.get("name") or "?").strip()
    val = value_of(part)
    items = [
        {"type": "text", "text": name, "size": 16, "bold": True,
         "x_mm": 1, "y_mm": 4.5},
        {"type": "text", "text": val or "-", "size": 11,
         "x_mm": 1, "y_mm": 9.5},
        {"type": "text", "text": loc or "-", "size": 11,
         "x_mm": 1, "y_mm": 13.5},
        {"type": "qr", "data": f"https://part.gofor.ltd/part/{pid}", "size_mm": 9,
         "x_mm": 28, "y_mm": 13.5},
        {"type": "barcode", "data": name, "symbology": "code128",
         "height_mm": 4.5, "width_mm": 26, "x_mm": 1, "y_mm": 18.5,
         "show_text": False},
    ]
    return {"width_mm": 40, "height_mm": 30, "offset_x_mm": 10,
            "border": False, "density": 75, "copies": 1, "items": items}


def preview(spec: dict, out_png: str) -> int:
    req = urllib.request.Request(LABEL_API + "/api/preview",
                                 data=json.dumps(spec).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        png = r.read()
    os.makedirs(os.path.dirname(out_png) or ".", exist_ok=True)
    with open(out_png, "wb") as f:
        f.write(png)
    return len(png)


def print_spec(spec: dict, addr: str) -> dict:
    body = dict(spec)
    body["addr"] = addr
    req = urllib.request.Request(LABEL_API + "/api/print",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read().decode())


def main() -> int:
    ap = argparse.ArgumentParser(description="Part-DB → 标签打印")
    ap.add_argument("--search", default="", help="按名称搜索")
    ap.add_argument("--ids", default="", help="按 part id 列表，如 1,2,3")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--preview", help="预览输出目录（不打印）")
    ap.add_argument("--print", dest="do_print", action="store_true", help="真机打印")
    ap.add_argument("--addr", default="06:41:F4:57:C7:53")
    args = ap.parse_args()

    parts = list_parts(args.search, args.ids, args.limit)
    if not parts:
        print("没有匹配的零件")
        return 1
    print(f"匹配 {len(parts)} 个零件")
    ok = 0
    for part in parts:
        name = part.get("name", "?")
        loc = location_of(part)
        spec = build_spec(part, loc)
        if args.preview:
            path = os.path.join(args.preview, f"{name.replace('/', '_')}.png")
            n = preview(spec, path)
            print(f"  ✓ 预览 {name}  ({n}B) → {path}")
        if args.do_print:
            res = print_spec(spec, args.addr)
            status = "OK" if res.get("ok") else res.get("error", "失败")
            print(f"  {'✓' if res.get('ok') else '✗'} 打印 {name}: {status}")
            ok += 1 if res.get("ok") else 0
    if args.do_print:
        print(f"打印完成 {ok}/{len(parts)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
