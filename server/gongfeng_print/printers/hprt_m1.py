"""汉印 HPRT M1 后端：ESC/POLI over 经典蓝牙 SPP。

协议要点（实测跑通，见 ``tool/m1_escpoli.py`` 与 ``re-apk/PROTOCOL.md``）
------------------------------------------------------------------------
* SPP RFCOMM channel 1；握手 ``1B 1C "set mm" n m`` -> ``OK`` -> 之后写入
  逐字节 XOR(n+m)&0xFF。
* 位图：1bpp，1=黑，MSB=最左，行字节 = ceil(w/8)，``avg(R,G,B) <= 128`` 判黑。
* 帧：``1D 76 30 30 | wBytes LE16 | rows LE16 | lzoLen LE32 | LZO1X-1(raw)``，
  按整行块切分，每块原始数据 ≤ 1024 字节，一块一帧。
* 命令：``1B 61 n`` 对齐；``1D "setc" d`` 浓度；``1D "setp" 2`` 标签纸；
  ``1D "setL"`` 测纸；``1B 1B 01 5A 00`` 开始打印；``1D 66 C0 03`` 走纸到撕纸位；
  ``1B 12 73`` 状态查询（回包前 2 字节 LE 按位，且带 ``pooli_sta=<byte>``）。
"""
from __future__ import annotations

import ctypes
import functools
import re
import time
from dataclasses import dataclass
from typing import Any

from PIL import Image

from ..transport.spp import SppTransport, TransportError
from .base import (
    STATUS_BIT_NAMES,
    BackendError,
    Capabilities,
    PrinterBackend,
    PrinterStatus,
)

# ---------------------------------------------------------------- 常量
DPI = 203
PACKAGE_SIZE = 1024
SPP_CHANNEL = 1
BLACK_THRESHOLD_SUM = 386  # (r+g+b)//3 <= 128  <=>  r+g+b <= 386

GS = 0x1D
ESC = 0x1B
HANDSHAKE_PREFIX = bytes([ESC, 0x1C]) + b"set mm"
CMD_STAT = bytes([ESC, 0x12, 0x73])                 # ESC DC2 's'
CMD_ALIGN = {
    "left": bytes([ESC, 0x61, 0x00]),
    "center": bytes([ESC, 0x61, 0x01]),
    "right": bytes([ESC, 0x61, 0x02]),
}
CMD_DENSITY = bytes([GS]) + b"setc"                  # + d
CMD_PAPER_TYPE = bytes([GS]) + b"setp"               # + t
CMD_LEARN = bytes([GS]) + b"setL"

# 官方参数命令: 1B 1C "& V1 setkey\r\n" 01 <key LE16> <encode> <value>
CMD_SETKEY = bytes([ESC, 0x1C]) + b"& V1 setkey\r\n" + b"\x01"


def setkey_cmd(key: int, value: int, encode: int) -> bytes:
    head = CMD_SETKEY + int(key).to_bytes(2, "little") + bytes([encode])
    if encode == 1:
        return head + bytes([value & 0xFF])
    if encode == 2:
        return head + (value & 0xFFFF).to_bytes(2, "little")
    return head + (value & 0xFFFFFFFF).to_bytes(4, "little")
CMD_START_PRINT = bytes([ESC, 0x1B, 0x01, 0x5A, 0x00])
CMD_FEED_960 = bytes([GS, 0x66, 0xC0, 0x03])
DENSITIES = (35, 55, 75, 95)
ALIGNS = ("left", "center", "right")


# ---------------------------------------------------------------- LZO
@functools.lru_cache(maxsize=1)
def _lzo_lib():
    try:
        lib = ctypes.CDLL("liblzo2.so.2")
    except OSError as exc:
        raise BackendError(
            "未找到 liblzo2（LZO1X 压缩库）：请安装 lzo（如 pacman -S lzo）") from exc
    lib.lzo1x_1_compress.argtypes = [
        ctypes.c_char_p, ctypes.c_ulong, ctypes.c_char_p,
        ctypes.POINTER(ctypes.c_ulong), ctypes.c_void_p,
    ]
    lib.lzo1x_1_compress.restype = ctypes.c_int
    lib.lzo1x_decompress_safe.argtypes = [
        ctypes.c_char_p, ctypes.c_ulong, ctypes.c_char_p,
        ctypes.POINTER(ctypes.c_ulong), ctypes.c_void_p,
    ]
    lib.lzo1x_decompress_safe.restype = ctypes.c_int
    return lib


LZO1X_1_MEM_COMPRESS = 16384 * 8


def lzo1x_1(data: bytes) -> bytes:
    """LZO1X-1 压缩（与 HPRT SDK 使用的 ``lzo1x_1_compress`` 一致）。"""
    lib = _lzo_lib()
    n = len(data)
    dst = ctypes.create_string_buffer(n + n // 16 + 64 + 3)
    out_len = ctypes.c_ulong(len(dst))
    work = ctypes.create_string_buffer(LZO1X_1_MEM_COMPRESS)
    rc = lib.lzo1x_1_compress(data, n, dst, ctypes.byref(out_len), work)
    if rc != 0:
        raise BackendError(f"LZO 压缩失败 rc={rc}")
    return dst.raw[: out_len.value]


def lzo1x_decompress(data: bytes, expected_len: int | None = None) -> bytes:
    """LZO1X 解压（selftest 做帧回读校验用）。"""
    lib = _lzo_lib()
    cap = int(expected_len) if expected_len else max(64, len(data) * 8)
    dst = ctypes.create_string_buffer(cap + 64)
    out_len = ctypes.c_ulong(len(dst))
    rc = lib.lzo1x_decompress_safe(data, len(data), dst, ctypes.byref(out_len), None)
    if rc != 0:
        raise BackendError(f"LZO 解压失败 rc={rc}")
    return dst.raw[: out_len.value]


# ---------------------------------------------------------------- 位图
def image_to_raster(image: Image.Image) -> tuple[bytes, int, int]:
    """RGB -> 1bpp 位图：``(r+g+b)//3 <= 128`` 判黑，MSB 最左，行字节 ceil(w/8)。"""
    img = image.convert("RGB")
    width, height = img.size
    wbytes = (width + 7) >> 3
    raw = img.tobytes()
    out = bytearray(wbytes * height)
    for y in range(height):
        row_base = y * width * 3
        out_base = y * wbytes
        for x in range(width):
            i = row_base + x * 3
            if raw[i] + raw[i + 1] + raw[i + 2] <= BLACK_THRESHOLD_SUM:
                out[out_base + (x >> 3)] |= 0x80 >> (x & 7)
    return bytes(out), wbytes, height


def frame(raw: bytes, wbytes: int, rows: int) -> bytes:
    """单帧：``1D 76 30 30 | wBytes LE16 | rows LE16 | lzoLen LE32 | LZO1X-1``。"""
    comp = lzo1x_1(raw)
    return (
        bytes([GS]) + b"v00"
        + int(wbytes).to_bytes(2, "little")
        + int(rows).to_bytes(2, "little")
        + len(comp).to_bytes(4, "little")
        + comp
    )


@dataclass
class StreamInfo:
    """一次打印的指令流以及统计信息（便于 API 返回与 selftest 校验）。"""

    stream: bytes
    row_bytes: int
    rows: int
    frames: int
    package_size: int = PACKAGE_SIZE

    @property
    def stream_bytes(self) -> int:
        return len(self.stream)


def _snap_density(density: Any) -> int:
    try:
        value = int(density)
    except (TypeError, ValueError):
        value = 75
    return min(DENSITIES, key=lambda d: (abs(d - value), d))


def _snap_align(align: Any) -> str:
    key = str(align or "left").strip().lower()
    return key if key in ALIGNS else "left"


def build_stream(image: Image.Image, *, package_size: int = PACKAGE_SIZE,
                 density: int = 75, align: str = "left",
                 paper_type: int | None = 3,
                 form_feed: bool = False,
                 flow: str = "vendor", trim_bottom_mm: float = 0.0,
                 post_feed_mm: float = 0.0) -> StreamInfo:
    """把位图编码成一整套 ESC/POLI 指令流（含全部 GS v00 帧）。"""
    raw, wbytes, rows = image_to_raster(image)
    if trim_bottom_mm > 0:
        keep = max(1, rows - int(round(float(trim_bottom_mm) * 8)))
        raw = raw[: keep * wbytes]
        rows = keep
    parts: list[bytes] = []
    if paper_type is not None:
        parts.append(CMD_PAPER_TYPE + bytes([int(paper_type) & 0xFF]))
    parts.append(CMD_DENSITY + bytes([_snap_density(density)]))
    parts.append(CMD_ALIGN[_snap_align(align)])

    block = max((int(package_size) // max(wbytes, 1)) * max(wbytes, 1), max(wbytes, 1))
    frames = 0
    for offset in range(0, len(raw), block):
        chunk = raw[offset:offset + block]
        chunk_rows = len(chunk) // max(wbytes, 1)
        if chunk_rows <= 0:
            continue
        parts.append(frame(chunk[: chunk_rows * wbytes], wbytes, chunk_rows))
        frames += 1
    # flow:
    #   single  = 先 setPollForm 走到缝隙对齐(保证从标签起点开始) → 位图 → 再走到下一张缝隙（只出一张）
    #   vendor  = 厂商原始顺序：位图 → setPollForm
    #   legacy  = 我们最早试出的 1B 1B 01 5A 00 触发
    if flow == "single":
        body = b"".join(parts[3:])
        return StreamInfo(stream=b"".join([*parts[:3], CMD_FEED_960, body, CMD_FEED_960]),
                          row_bytes=wbytes, rows=rows, frames=frames,
                          package_size=int(package_size))
    if flow == "vendor":
        parts.append(CMD_FEED_960)
    else:
        parts.append(CMD_START_PRINT)
        if form_feed:
            parts.append(CMD_FEED_960)
    # 注意：固件把 1D 66 参数当无符号数，负值会导致狂走纸，禁止使用负数回退。
    if post_feed_mm > 0:
        # 正向补走：标签30mm + 缝隙2mm = 32mm 节距，打印30mm后再走2mm，下一张正好对齐
        dots = int(round(float(post_feed_mm) * 8))
        parts.append(bytes([GS, 0x66]) + dots.to_bytes(2, "little"))
    return StreamInfo(stream=b"".join(parts), row_bytes=wbytes, rows=rows,
                      frames=frames, package_size=int(package_size))


# ---------------------------------------------------------------- 状态
_POOLI_RE = re.compile(rb"pooli_sta=([\x00-\xff])")


def parse_status(raw: bytes) -> PrinterStatus:
    """解析 ``1B 12 73`` 回包：前 2 字节 LE 按位 + ``pooli_sta=<byte>``。"""
    if not raw:
        return PrinterStatus(value=-1, errors=("无响应",), raw=b"")
    value = (raw[0] | (raw[1] << 8)) & 0xFFFF if len(raw) >= 2 else -1
    printing = bool(value >= 0 and (value & (1 << 8)))
    match = _POOLI_RE.search(raw)
    pooli_sta = match.group(1)[0] if match else None
    if value < 0:
        return PrinterStatus(value=-1, errors=("无响应",), raw=raw,
                             printing=False, pooli_sta=pooli_sta)
    errors = tuple(name for bit, name in STATUS_BIT_NAMES.items() if value & (1 << bit))
    return PrinterStatus(value=value, errors=errors, raw=raw,
                         printing=printing, pooli_sta=pooli_sta)


# ---------------------------------------------------------------- 后端
class HprtM1Backend(PrinterBackend):
    """汉印 M1（203dpi / 48mm 打印头 / 40×30mm 默认标签）。"""

    capabilities = Capabilities(
        model_id="hprt-m1",
        name="汉印 HPRT M1",
        vendor="HPRT 汉印",
        dpi=DPI,
        head_dots=384,
        max_width_mm=50.0,
        printable_width_mm=48.0,
        max_height_mm=300.0,
        default_offset_x_mm=10.0,
        max_offset_x_mm=20.0,
        default_label={"width_mm": 40.0, "height_mm": 30.0},
        densities=DENSITIES,
        aligns=ALIGNS,
        supports_learn=True,
        supports_status=True,
        supports_copies=True,
        supports_form_feed=True,
        supports_border=True,
        connection="bluetooth-spp",
        notes=(
            "经典蓝牙 SPP（RFCOMM channel 1），不是 BLE",
            "纸筒挡板在左侧：40mm 纸卷相对打印头原点向右偏移约 10mm"
            "（spec.offset_x_mm 默认 10），50mm 纸用 0",
            "打印头 384dot(48mm)；位图宽上限按 400px 容差（40mm 纸+10mm 偏移=400px）",
            "握手后写入数据按 XOR(n+m) 加密；读回数据不解密",
        ),
    )

    def __init__(self, addr: str | None = None, channel: int = SPP_CHANNEL,
                 timeout: float = 20.0, retries: int = 1,
                 log=None):
        self._addr = addr
        self._channel = channel
        self._timeout = timeout
        self._retries = retries
        self._log = log or (lambda _msg: None)
        self._transport: SppTransport | None = None

    # -- 连接 -------------------------------------------------------
    @property
    def addr(self) -> str | None:
        return self._transport.addr if self._transport else self._addr

    @addr.setter
    def addr(self, value: str | None) -> None:
        """允许外部在 connect() 前指定/改地址（MCP、脚本调用习惯）。"""
        if not self.connected:
            self._addr = (value or "").strip().upper() or None

    @property
    def connected(self) -> bool:
        return bool(self._transport and self._transport.connected)

    def connect(self, addr: str | None = None) -> "HprtM1Backend":
        if self.connected:
            return self
        target = (addr or self._addr or "").strip().upper() or None
        transport = SppTransport(addr=target, channel=self._channel,
                                 timeout=self._timeout, log=self._log)
        try:
            transport.connect(retries=self._retries)
        except TransportError as exc:
            transport.close()
            raise BackendError(str(exc)) from exc
        self._transport = transport
        if transport.addr:  # 自动发现的地址也记录下来（close 后仍可查询）
            self._addr = transport.addr
        return self

    def close(self) -> None:
        if self._transport is not None:
            try:
                self._transport.close()
            finally:
                self._transport = None

    def _require_transport(self) -> SppTransport:
        if self._transport is None or not self._transport.connected:
            raise BackendError("打印机未连接：请先 connect()")
        return self._transport

    def _write(self, data: bytes, gap: float = 0.02) -> None:
        try:
            self._require_transport().write(data, gap=gap)
        except TransportError as exc:
            raise BackendError(str(exc)) from exc

    def _read(self, timeout: float = 1.5) -> bytes:
        try:
            return self._require_transport().recv(timeout)
        except TransportError as exc:
            raise BackendError(str(exc)) from exc

    def _read_until(self, timeout: float = 1.5, min_len: int = 3,
                    quiet: float = 0.12) -> bytes:
        try:
            return self._require_transport()._recv_until(timeout, min_len=min_len,
                                                          quiet=quiet)
        except TransportError as exc:
            raise BackendError(str(exc)) from exc

    # -- 状态 -------------------------------------------------------
    def status(self, wait: float = 0.5) -> PrinterStatus:
        self._write(CMD_STAT, gap=0)
        # 打印机通常 100ms 内回包；最多等 1.5s（wait 参数保留兼容旧用法）
        raw = self._read_until(max(0.3, wait + 1.0), min_len=3, quiet=0.12)
        return parse_status(raw)

    def ensure_ready(self, auto_learn: bool = True) -> PrinterStatus:
        st = self.status()
        if st.locate_fail and auto_learn:
            self._log("检测到定位失败，自动测纸")
            st = self.learn()
        return st

    def learn(self, wait: float = 6.0) -> PrinterStatus:
        """测纸/定位（会走纸数张）。"""
        self._log("测纸中（会走纸）")
        self._write(CMD_PAPER_TYPE + bytes([2]))
        time.sleep(0.3)
        self._write(CMD_LEARN)
        time.sleep(max(0.5, wait))
        return self.status()

    def wait_print_done(self, timeout: float = 30.0) -> PrinterStatus:
        deadline = time.time() + timeout
        st = self.status()
        if st.printing:
            while time.time() < deadline:
                time.sleep(0.7)
                st = self.status()
                if not st.printing:
                    break
        return st

    # -- 打印 -------------------------------------------------------
    def print_image(self, image: Image.Image, *, copies: int = 1,
                    density: int = 75, align: str = "left",
                    auto_learn: bool = False, form_feed: bool = False,
                    flow: str = "legacy", trim_bottom_mm: float = 0.0,
                    post_feed_mm: float = 2.0, wait: float = 0.4) -> PrinterStatus:
        if image.width > self.capabilities.head_dots + 16:
            raise BackendError(
                f"位图宽度 {image.width}px 超过 M1 允许的 "
                f"{self.capabilities.head_dots + 16}px（标签宽度+纸卷偏移过大）")
        copies = max(1, min(int(copies or 1), 99))
        # 官方纸张参数：206=2 标签纸；208=定位后走纸长度(-24 = -2mm，固件侧回退/补偿)
        try:
            self._write(setkey_cmd(206, 2, 1), gap=0.05)
            self._write(setkey_cmd(208, -24, 2), gap=0.05)
        except Exception:
            pass
        started = time.time()
        pre = self.ensure_ready(auto_learn=auto_learn)
        # 握手成功但状态查询无回包（value<0）时不再阻断打印：
        # 部分固件在忙/刚上电时 status 查询会超时，但打印链路仍可用。
        if pre.value >= 0 and not pre.ok and not pre.locate_fail:
            raise BackendError(f"打印机状态异常：{pre.summary}")
        info: StreamInfo | None = None
        for _ in range(copies):
            info = build_stream(image, density=density, align=align,
                                flow=flow, trim_bottom_mm=trim_bottom_mm,
                                post_feed_mm=post_feed_mm, 
                                form_feed=form_feed)
            self._write(info.stream, gap=0)
            time.sleep(max(0.1, wait))
        st = self.wait_print_done()
        if info is not None:
            st.extra.update({
                "copies": copies,
                "frames": info.frames,
                "rows": info.rows,
                "row_bytes": info.row_bytes,
                "stream_bytes": info.stream_bytes,
                "elapsed_s": round(time.time() - started, 2),
            })
        return st
