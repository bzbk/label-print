"""标签渲染：Pillow 画布 + ``zint`` CLI 生成条码/二维码。

坐标与单位
----------
* 画布按毫米给尺寸，像素换算：``px = round(mm / 25.4 * 203)``（203 dpi）。
  1mm ≈ 8dot：40x30mm 标签内容 = 320x240px。
* ``offset_x_mm`` 纸卷偏移：M1 纸筒挡板在左侧，40mm 纸卷相对打印头原点右移
  10mm，因此输出位图宽度 = ``offset + width``（40mm 纸 + 10mm 偏移 = 400px），
  内容整体右移 offset（其余内容坐标仍以标签自身左上角为原点）。
* ``x_mm`` / ``y_mm`` 给出时按绝对定位放置；缺省按纵向堆叠布局。
* 文本 ``size`` 是像素字号（203dpi 下的真实像素）。
* 条码/二维码按整数模块像素渲染，保证 1bpp 打印时条空边界清晰：
  - ``height_mm`` 指条码元素（条 + 人眼可读文字）的总高度；
  - ``size_mm`` 指二维码目标边长，实际取整到整数模块像素后居中。
* 打印驱动的二值化规则是 ``avg(R,G,B) <= 128 -> 黑``，因此 A4 白底、黑字
  的 RGB 预览与最终打印一致；预览使用抗锯齿文字，打印边缘会被阈值化。

渲染层不依赖蓝牙，可独立用于 preview 与 selftest。
"""
from __future__ import annotations

import functools
import os
import shutil
import subprocess
import tempfile
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from .spec import SYMBOLOGIES, LabelSpec

DPI = 203
MM_PER_INCH = 25.4

FONT_CANDIDATES = [
    ("/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
     "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc"),
    ("/usr/share/fonts/adobe-source-han-sans/SourceHanSans.ttc",
     "/usr/share/fonts/adobe-source-han-sans/SourceHanSans.ttc"),
    ("/usr/share/fonts/TTF/DejaVuSans.ttf",
     "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf"),
]

PAD_MM = 1.5          # 默认内边距
ITEM_GAP_MM = 0.6     # 堆叠布局时元素间距
BORDER_MM = 0.25      # 边框线宽
MIN_BORDER_PX = 1
MAX_MODULE_PX = 8     # 条码/二维码单个模块最大像素（避免过大）


class RenderError(RuntimeError):
    """渲染失败（内容过长、zint 缺失等）。"""


# ---------------------------------------------------------------- 单位
def mm_to_px(mm: float, dpi: int = DPI) -> int:
    """毫米 -> 像素（至少 1px）。"""
    return max(1, int(round(float(mm) / MM_PER_INCH * dpi)))


def px_to_mm(px: float, dpi: int = DPI) -> float:
    return round(float(px) / dpi * MM_PER_INCH, 3)


# ---------------------------------------------------------------- 字体
@functools.lru_cache(maxsize=1)
def _font_paths() -> tuple[str | None, str | None]:
    for regular, bold in FONT_CANDIDATES:
        if os.path.exists(regular):
            return regular, (bold if os.path.exists(bold) else regular)
    return None, None


@functools.lru_cache(maxsize=64)
def _font(size_px: int, bold: bool = False) -> ImageFont.ImageFont:
    regular, bold_path = _font_paths()
    path = bold_path if bold else regular
    if path:
        try:
            return ImageFont.truetype(path, max(1, int(size_px)))
        except OSError:
            pass
    try:
        return ImageFont.load_default(size=max(1, int(size_px)))  # Pillow >= 10.1
    except TypeError:
        return ImageFont.load_default()


def fonts_available() -> dict[str, Any]:
    regular, bold = _font_paths()
    return {
        "regular": regular,
        "bold": bold,
        "cjk": bool(regular and "cjk" in regular.lower()),
    }


# ---------------------------------------------------------------- zint
@functools.lru_cache(maxsize=1)
def zint_available() -> bool:
    return shutil.which("zint") is not None


def _zint_raw(sid: int, data: str, *, scale: float = 1.0,
              height: int | None = None, show_text: bool = True) -> Image.Image:
    """调用 zint 生成 PNG，返回 L 模式图像。"""
    exe = shutil.which("zint")
    if not exe:
        raise RenderError("未安装 zint 命令：请安装 zint（条码/二维码渲染依赖）")
    fd, path = tempfile.mkstemp(prefix="gongfeng-zint-", suffix=".png")
    os.close(fd)
    try:
        cmd = [exe, "-b", str(sid), "-d", data, "-o", path,
               "--scale", f"{scale:g}", "--border=0"]
        if height is not None:
            cmd += ["--height", str(int(height))]
        if not show_text:
            cmd.append("--notext")
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=30)
        except subprocess.TimeoutExpired:
            raise RenderError("zint 渲染超时") from None
        except OSError as exc:
            raise RenderError(f"zint 无法执行：{exc}") from None
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or b"").decode("utf-8", "replace").strip()
            raise RenderError(f"zint 渲染失败（{data!r}）：{detail or '未知错误'}")
        try:
            img = Image.open(path)
            img.load()
        except OSError as exc:
            raise RenderError(f"zint 输出无法读取：{exc}") from None
        return img.convert("L")
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _fit_nearest(img: Image.Image, box_w: int, box_h: int) -> Image.Image:
    """等比缩放到 box 内（NEAREST 保持条空边界），不放大超过原始像素。"""
    box_w, box_h = max(1, int(box_w)), max(1, int(box_h))
    if img.width <= box_w and img.height <= box_h:
        return img
    ratio = min(box_w / img.width, box_h / img.height)
    size = (max(1, int(img.width * ratio)), max(1, int(img.height * ratio)))
    return img.resize(size, Image.NEAREST)


def zint_png(symbology: str, data: str, *, target_px: int,
             height_mm: float = 12.0, show_text: bool = True) -> Image.Image:
    """渲染条码/二维码，返回尺寸尽量贴近目标（整数模块像素）的图像。"""
    info = SYMBOLOGIES.get(str(symbology).lower())
    if info is None:
        raise RenderError(f"不支持的符号体系 {symbology!r}")
    sid = int(info["zint"])

    if info["linear"]:
        # 1) 探测模块数：scale=1 时每模块 2px。
        probe = _zint_raw(sid, data, scale=1, height=1, show_text=False)
        modules = max(1, probe.width // 2)
        target_total = mm_to_px(height_mm, DPI)
        # 2) 整数模块宽度：默认 2px（203dpi 下约 0.25mm X-dim），
        #    可用宽度放不下时退到 1px，仍放不下则由上层等比缩小。
        room = int(target_px) // modules if modules else 0
        module_px = 2 if room >= 2 else 1
        # 条码总高 = module_px * (height_units + 8*show_text)
        h_units = max(1, target_total // module_px - (8 if show_text else 0))
        img = _zint_raw(sid, data, scale=module_px / 2, height=h_units,
                        show_text=show_text)
        return img

    # 矩阵码（QR/DataMatrix/Aztec...）：目标为边长。
    probe = _zint_raw(sid, data, scale=1, show_text=False)
    modules = max(1, probe.width // 2)
    target = max(8, int(target_px))
    module_px = max(1, min(MAX_MODULE_PX, int(round(target / modules))))
    img = _zint_raw(sid, data, scale=module_px / 2, show_text=False)
    return _fit_nearest(img, target, target)


# ---------------------------------------------------------------- 绘制
def render_label(spec: LabelSpec, dpi: int = DPI) -> Image.Image:
    """按 spec 渲染标签，返回 RGB 图像（白底黑内容）。

    返回图像宽度 = ``offset_x_mm + width_mm`` 对应像素：标签内容绘制在
    ``offset_x_mm`` 之后，模拟纸卷相对打印头原点的物理偏移。
    """
    content = _render_content(spec, dpi)
    offset_px = mm_to_px(spec.offset_x_mm, dpi) if spec.offset_x_mm > 0 else 0
    if offset_px <= 0:
        return content
    canvas = Image.new("RGB", (offset_px + content.width, content.height), "white")
    canvas.paste(content, (offset_px, 0))
    return canvas


def _render_content(spec: LabelSpec, dpi: int = DPI) -> Image.Image:
    """绘制标签内容本身（不含纸卷偏移），宽 = width_mm。"""
    W = mm_to_px(spec.width_mm, dpi)
    H = mm_to_px(spec.height_mm, dpi)
    img = Image.new("RGB", (W, H), "white")
    draw = ImageDraw.Draw(img)
    pad = mm_to_px(PAD_MM, dpi)
    gap_px = mm_to_px(ITEM_GAP_MM, dpi)
    flow_y = float(pad)
    right_pad = pad

    for idx, item in enumerate(spec.items):
        kind = item["type"]

        if kind == "gap":
            flow_y += mm_to_px(item.get("mm", 2.0), dpi)
            continue

        x_abs = item.get("x_mm")
        y_abs = item.get("y_mm")
        x0 = mm_to_px(x_abs, dpi) if x_abs is not None else pad
        y0 = float(mm_to_px(y_abs, dpi)) if y_abs is not None else flow_y
        if y0 >= H:
            continue
        align = item.get("align", "left")
        box_right = W - right_pad

        if kind == "text":
            line_h, drawn_h, adv = 0, 0, 0
            text = str(item.get("text", ""))
            lines = text.split("\n") if text else [""]
            # 计算整块宽度以便 align 对齐
            font = _font(int(item.get("size", 24)), bool(item.get("bold", False)))
            widths: list[int] = []
            heights: list[int] = []
            for line in lines:
                bbox = draw.textbbox((0, 0), line or " ", font=font)
                widths.append(bbox[2] - bbox[0])
                heights.append(bbox[3] - bbox[1])
            block_h = sum(heights) + max(0, len(lines) - 1) * gap_px
            spacing = mm_to_px(item.get("spacing_mm", ITEM_GAP_MM), dpi)

            cursor_y = y0
            for line, tw, lh in zip(lines, widths, heights):
                if align == "right":
                    tx = box_right - tw
                elif align == "center":
                    tx = x0 + max(0, (box_right - x0 - tw) // 2)
                else:  # left
                    tx = x0
                local = draw.textbbox((0, 0), line or " ", font=font)
                # 用 (0,0) 处的 bearing 修正，让字形视觉左上角贴住 (tx, cursor_y)
                draw.text((tx - local[0], cursor_y - local[1]), line or " ",
                          font=font, fill="black")
                cursor_y += lh + gap_px
            drawn_h = max(1, int(cursor_y - y0))
            flow_y = y0 + float(drawn_h) + spacing
            continue

        if kind == "barcode":
            height_mm = float(item.get("height_mm", 12.0))
            target_total = mm_to_px(height_mm, dpi)
            avail_w = max(8, int((mm_to_px(item["width_mm"], dpi)
                                  if item.get("width_mm") else box_right - x0)))
            pic = zint_png(item["symbology"], str(item["data"]),
                           target_px=avail_w, height_mm=height_mm,
                           show_text=bool(item.get("show_text", True)))
            pic = _fit_nearest(pic, avail_w, max(1, H - int(y0)))
            if x_abs is not None:
                px = x0
            elif align == "right":
                px = box_right - pic.width
            elif align == "center":
                px = x0 + max(0, (box_right - x0 - pic.width) // 2)
            else:
                px = x0
            img.paste(pic.convert("RGB"), (int(px), int(round(y0))))
            flow_y = y0 + pic.height + gap_px
            continue

        if kind == "qr":
            side = mm_to_px(item.get("size_mm", 14.0), dpi)
            pic = zint_png("qr", str(item["data"]), target_px=side)
            pic = _fit_nearest(pic, side, max(1, H - int(y0)))
            if x_abs is not None:
                px = x0
            elif align == "right":
                px = box_right - pic.width
            elif align == "center":
                px = x0 + max(0, (box_right - x0 - pic.width) // 2)
            else:
                px = x0
            img.paste(pic.convert("RGB"), (int(px), int(round(y0))))
            flow_y = y0 + pic.height + gap_px
            continue

        raise RenderError(f"未知元素类型 {kind!r}（items[{idx}]）")

    if spec.border:
        width = max(MIN_BORDER_PX, mm_to_px(BORDER_MM, dpi))
        draw.rectangle([0, 0, W - 1, H - 1], outline="black", width=width)
    return img


def render_png(spec: LabelSpec) -> bytes:
    """渲染并编码为 PNG 字节（preview 接口直接返回）。"""
    import io

    img = render_label(spec)
    buf = io.BytesIO()
    img.save(buf, format="PNG", dpi=(DPI, DPI))
    return buf.getvalue()
