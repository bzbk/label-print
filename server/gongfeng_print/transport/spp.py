"""蓝牙经典 SPP（RFCOMM）传输层：设备发现 + 握手 + XOR 加密写入。

协议事实（已实测跑通，来自 ``tool/m1_escpoli.py``）
--------------------------------------------------
* 经典蓝牙 SPP：RFCOMM UUID ``00001101-0000-1000-8000-00805F9B34FB``，channel 1。
* 连接后先发握手 ``1B 1C "set mm" <n> <m>``（n/m 各 1..100 随机），
  打印机回 ``OK``；此后**所有写入逐字节 XOR(n+m)&0xFF**，读回数据不解密。
* 打印机名形如 ``M1-xxxx``。未握手成功时抛 :class:`TransportError`。

本模块只依赖标准库，可独立用于 selftest（打印机关机时快速失败）。
"""
from __future__ import annotations

import os
import re
import socket
import subprocess
import time
from dataclasses import dataclass
from typing import Callable

SPP_UUID = "00001101-0000-1000-8000-00805F9B34FB"
SPP_CHANNEL = 1
RFCOMM_PROTO = getattr(socket, "BTPROTO_RFCOMM", 3)
AF_BLUETOOTH = getattr(socket, "AF_BLUETOOTH", 31)

M1_NAME_RE = re.compile(r"(?i)^(?:m1(?:[-_ ]?[0-9a-z]{3,})?|hprt[-_ ]?m1)")
ADDR_RE = re.compile(r"^[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}$")

LogFn = Callable[[str], None]


class TransportError(RuntimeError):
    """蓝牙连接/握手/读写失败。"""


@dataclass(frozen=True)
class BluetoothDevice:
    address: str
    name: str

    @property
    def is_m1(self) -> bool:
        return bool(M1_NAME_RE.match(self.name.strip()))

    def to_dict(self) -> dict:
        return {"address": self.address, "name": self.name, "is_m1": self.is_m1}


# ------------------------------------------------------------- 设备发现
def _bluetoothctl(args: list[str], timeout: float = 6.0) -> str:
    try:
        proc = subprocess.run(["bluetoothctl", *args], capture_output=True,
                              timeout=timeout, text=True)
        return proc.stdout or ""
    except FileNotFoundError:
        raise TransportError("未找到 bluetoothctl：请安装 BlueZ") from None
    except subprocess.TimeoutExpired:
        raise TransportError("bluetoothctl 超时：请确认蓝牙服务已启动") from None
    except OSError as exc:
        raise TransportError(f"无法执行 bluetoothctl：{exc}") from None


def list_devices() -> list[BluetoothDevice]:
    """解析 ``bluetoothctl devices``，返回已知设备（不主动扫描）。"""
    out = _bluetoothctl(["devices"])
    found: list[BluetoothDevice] = []
    for line in out.splitlines():
        m = re.match(r"Device\s+([0-9A-F:]{17})\s+(.*)", line.strip())
        if m:
            found.append(BluetoothDevice(m.group(1).upper(), m.group(2).strip()))
    return found


def controller_info() -> dict:
    """解析 ``bluetoothctl show``，返回控制器名称/地址/供电状态。"""
    try:
        out = _bluetoothctl(["show"], timeout=5.0)
    except TransportError as exc:
        return {"available": False, "error": str(exc)}
    info: dict = {"available": True, "powered": False, "name": None, "address": None}
    m = re.search(r"Controller\s+([0-9A-F:]{17})", out)
    if m:
        info["address"] = m.group(1).upper()
    m = re.search(r"^\s*Name:\s*(.+)$", out, re.MULTILINE)
    if m:
        info["name"] = m.group(1).strip()
    m = re.search(r"^\s*Powered:\s*(\w+)", out, re.MULTILINE | re.IGNORECASE)
    if m:
        info["powered"] = m.group(1).lower() == "yes"
    return info


def is_m1_name(name: str) -> bool:
    return bool(M1_NAME_RE.match((name or "").strip()))


def find_m1(devices: list[BluetoothDevice] | None = None) -> BluetoothDevice | None:
    """在已知设备里找 M1（优先 ``M1-xxxx``/``HPRT``，退而求其次名字含 m1）。"""
    devices = list_devices() if devices is None else devices
    for dev in devices:
        if dev.is_m1:
            return dev
    for dev in devices:
        if "m1" in dev.name.lower():
            return dev
    return None


# ------------------------------------------------------------- 传输
class SppTransport:
    """一条 SPP 连接 + 握手密钥；线程内使用，不保证多线程安全。"""

    def __init__(self, addr: str | None = None, channel: int = SPP_CHANNEL,
                 timeout: float = 20.0, log: LogFn | None = None):
        self.addr = (addr or os.environ.get("M1_ADDR") or "").strip().upper() or None
        self.channel = int(channel)
        self.timeout = float(timeout)
        self.log = log or (lambda _msg: None)
        self.sock: socket.socket | None = None
        self.key: int | None = None
        self._pending = bytearray()

    # -- 连接/握手 -------------------------------------------------
    @property
    def connected(self) -> bool:
        return self.sock is not None

    def connect(self, retries: int = 1, retry_delay: float = 1.2) -> "SppTransport":
        last: Exception | None = None
        for attempt in range(max(1, retries + 1)):
            try:
                if not self.addr:
                    dev = find_m1()
                    if dev is None:
                        raise TransportError(
                            "找不到 M1 打印机：请确认打印机已开机并已配对，"
                            "`bluetoothctl devices` 里应能看到 M1-xxxx")
                    self.addr = dev.address
                    self.log(f"自动发现打印机 {dev.name} ({dev.address})")
                else:
                    if not ADDR_RE.match(self.addr):
                        raise TransportError(f"蓝牙地址格式不正确：{self.addr!r}")
                self._open_socket()
                if self.handshake():
                    return self
                raise TransportError("握手失败（未收到 OK）：打印机可能未就绪")
            except TransportError as exc:
                last = exc
                self._close_sock()
                if attempt >= retries:
                    break
                self.log(f"连接失败（第 {attempt + 1} 次）：{exc}，重试中…")
                time.sleep(retry_delay)
        raise TransportError(f"连接打印机失败：{last}")

    def _open_socket(self) -> None:
        try:
            sock = socket.socket(AF_BLUETOOTH, socket.SOCK_STREAM, RFCOMM_PROTO)
        except (AttributeError, OSError) as exc:
            raise TransportError(
                f"当前 Python 不支持蓝牙 RFCOMM（{exc}）："
                "请使用带蓝牙支持的 Linux Python") from None
        sock.settimeout(self.timeout)
        try:
            sock.connect((self.addr, self.channel))
        except OSError as exc:
            sock.close()
            raise TransportError(f"无法连接 {self.addr} channel {self.channel}：{exc}") from None
        self.sock = sock
        self.log(f"已连接 {self.addr} (channel {self.channel})")

    def handshake(self, tries: int = 3) -> bool:
        """发 ``1B 1C "set mm" n m``，收到 ``OK`` 后设置 XOR 密钥。"""
        assert self.sock is not None
        for _ in range(tries):
            n, m = _rand_pair()
            payload = b"\x1b\x1cset mm" + bytes([n, m])
            try:
                self.sock.sendall(payload)
            except OSError as exc:
                raise TransportError(f"握手写入失败：{exc}") from None
            data = self._recv_until(2.0, min_len=2)
            if data[:2] == b"OK":
                self.key = (n + m) & 0xFF
                if len(data) > 2:  # 握手回包后可能紧跟别的数据
                    self._pending.extend(data[2:])
                self.log(f"握手成功 key=0x{self.key:02x}")
                return True
        return False

    # -- 读写 ------------------------------------------------------
    def _recv(self, timeout: float) -> bytes:
        if self._pending:
            data = bytes(self._pending)
            self._pending.clear()
            return data
        if self.sock is None:
            return b""
        self.sock.settimeout(timeout)
        try:
            data = self.sock.recv(512)
        except socket.timeout:
            return b""
        except OSError:
            return b""
        return data or b""

    def recv(self, timeout: float = 1.5) -> bytes:
        return self._recv(timeout)

    def _recv_until(self, timeout: float = 2.0, min_len: int = 2,
                    quiet: float = 0.0) -> bytes:
        """读取到至少 min_len 字节（再静默 quiet 秒收尾），超时返回已收到的数据。"""
        deadline = time.time() + max(0.05, timeout)
        data = bytearray()
        while time.time() < deadline:
            chunk = self._recv(min(0.3, max(0.02, deadline - time.time())))
            if chunk:
                data.extend(chunk)
                continue
            if len(data) >= min_len:
                if quiet <= 0:
                    break
                # 已满足长度：再等一小段收尾
                tail = self._recv(quiet)
                if tail:
                    data.extend(tail)
                break
            time.sleep(0.01)
        return bytes(data)

    def write(self, data: bytes, gap: float = 0.02) -> None:
        """按握手密钥 XOR 后写入；未握手则拒绝（避免打印乱码）。"""
        if self.sock is None:
            raise TransportError("未连接打印机")
        if self.key is None:
            raise TransportError("未完成握手，拒绝写入")
        payload = bytes(b ^ self.key for b in data)
        try:
            self.sock.sendall(payload)
        except OSError as exc:
            raise TransportError(f"写入失败：{exc}") from None
        if gap > 0:
            time.sleep(gap)

    # -- 关闭 ------------------------------------------------------
    def _close_sock(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None
        self.key = None
        self._pending.clear()

    def close(self) -> None:
        self._close_sock()

    def __enter__(self) -> "SppTransport":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()


def _rand_pair() -> tuple[int, int]:
    import random

    return random.randint(1, 100), random.randint(1, 100)
