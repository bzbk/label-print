# 共风标签打印 · 打印服务端（server/）

纯 Python 标准库实现的本地 HTTP 打印服务，为「共风标签打印」桌面端提供
**标签渲染 / 预览 / 打印 / 设备管理** 能力。本期实现 **汉印 HPRT M1**
（ESC/POLI 指令集 over 经典蓝牙 SPP），架构上支持多机型扩展。

- 不引入 Flask / FastAPI / 任何 pip 依赖；HTTP 使用 `http.server.ThreadingHTTPServer`
- 渲染用 Pillow，条码/二维码用系统 `zint`，位图压缩用 `liblzo2`（ctypes）
- 纸卷偏移：M1 纸筒挡板在左侧，40mm 纸卷需整体右移 10mm（`offset_x_mm=10`，
  输出位图 400px 宽），详见下文「纸卷偏移」
- 前端（`ui/`，Vite 5173）通过 CORS 直接调用 `http://127.0.0.1:17600`

## 目录结构

```
server/
├── gongfeng_print/
│   ├── __init__.py           版本信息
│   ├── __main__.py           python3 -m gongfeng_print
│   ├── server.py             HTTP 服务 / 路由 / CORS / 并发锁
│   ├── spec.py               标签 spec 契约：解析/校验/规范化
│   ├── render.py             渲染（毫米画布 + Pillow + zint）
│   ├── transport/
│   │   └── spp.py            蓝牙经典 SPP：设备发现/握手/XOR 加密写入
│   └── printers/
│       ├── base.py           后端抽象：Capabilities / PrinterStatus / PrinterBackend
│       ├── registry.py       机型注册表 register(model_id, factory)
│       └── hprt_m1.py        汉印 M1：1bpp 位图 / LZO 帧 / ESC-POLI 命令
├── selftest.py               自测（只渲染 + 校验，不打印）
├── requirements.txt          系统依赖说明（无 pip 依赖）
└── README.md
```

## 环境依赖

| 依赖 | 用途 | Arch Linux 包 |
| --- | --- | --- |
| Python ≥ 3.10（已在 3.14 验证） | 运行 | `python` |
| Pillow | 画布 / 文字渲染 | `python-pillow` |
| zint | 条码 / 二维码 PNG | `zint` |
| liblzo2 | LZO1X-1 帧压缩（ctypes） | `lzo` |
| BlueZ (`bluetoothctl`) | 设备发现 + RFCOMM SPP | `bluez` / `bluez-utils` |
| Noto Sans CJK | 中文文字（缺失时回退其他字体） | `noto-fonts-cjk` |

> 本服务**不需要** `pip install` 任何包，`requirements.txt` 只记录系统依赖。

## 启动

```bash
cd server
python3 -m gongfeng_print.server --port 17600
# 输出：
# gongfeng-label-server v0.1.0 已启动：http://127.0.0.1:17600
```

可选参数：

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--host` | `127.0.0.1` | 监听地址（局域网访问用 `0.0.0.0`） |
| `--port` | `17600` | 监听端口 |
| `--model` | `hprt-m1` | 默认机型 |
| `--addr` | 自动发现 | M1 蓝牙地址，如 `06:41:F4:57:C7:53`；也可用环境变量 `M1_ADDR` |
| `--bt-timeout` | `20` | RFCOMM 连接超时秒数 |
| `--bt-retries` | `1` | 蓝牙连接重试次数 |
| `--cors` | 本地 5173/4173 | 追加允许的 CORS 来源，可重复 |
| `--verbose` | 关 | 打印访问日志 |
| `--version` | | 输出版本 |

环境变量：

- `M1_ADDR`：默认打印机地址（等价 `--addr`）
- `GONG_FENG_STATUS_TTL`：`/api/status` 结果缓存秒数（默认 `3`，前端状态栏轮询时避免反复占用蓝牙）

停止：`Ctrl+C`。

## 自测（不打印）

```bash
cd server
python3 selftest.py                # 135 项检查：依赖、spec、渲染、位图、LZO 帧、HTTP API
python3 selftest.py --with-status  # 额外对真实打印机做一次只读状态查询（不打印、不走纸）
```

预览图输出到 `/tmp/gongfeng_selftest/`；退出码 `0` 表示全部通过。
自测中的「离线状态」用无效蓝牙地址 `00:00:00:00:00:00` 触发，**不会**走纸打印。

## API

所有接口均可跨域访问（默认放行 `http://localhost:5173`、`http://127.0.0.1:5173`，
以及本地任意端口）。错误统一返回 JSON `{"ok": false, "error": "..."}`，
**打印机不可用时也是 HTTP 200 + `ok:false`，不会 500**。

### `GET /api/health`

服务与依赖健康状态。

```bash
curl -s http://127.0.0.1:17600/api/health
```

```json
{"ok":true,"service":"gongfeng-label-server","version":"0.1.0",
 "default_model":"hprt-m1","models":["hprt-m1"],
 "deps":{"python":"3.14.9","pillow":"13.0.0","zint":true,"lzo":true,
         "fonts":{"regular":"/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc","cjk":true},
         "bluetooth_spp":true}}
```

### `GET /api/models`

支持的机型及能力（`dpi` / 打印头点数 / 标签尺寸 / 浓度档 / 能力开关）。

```bash
curl -s http://127.0.0.1:17600/api/models
```

```json
{"ok":true,"default_model":"hprt-m1","count":1,
 "models":[{"model_id":"hprt-m1","name":"汉印 HPRT M1","dpi":203,
   "head_dots":384,"max_width_mm":50,"printable_width_mm":48,
   "default_label":{"width_mm":40,"height_mm":30},
   "default_offset_x_mm":10,"max_offset_x_mm":20,
   "densities":[35,55,75,95],"aligns":["left","center","right"],
   "capabilities":{"learn":true,"status":true,"copies":true,"form_feed":true,"border":true},
   "connection":"bluetooth-spp"}]}
```

### `GET /api/devices`

解析 `bluetoothctl devices`，列出系统已知蓝牙设备并标记哪个是 M1。

```bash
curl -s http://127.0.0.1:17600/api/devices
```

```json
{"ok":true,"count":2,
 "devices":[{"address":"06:41:F4:57:C7:53","name":"M1-c753","is_m1":true},
            {"address":"F0:6E:0B:D0:9A:C3","name":"Surface Dial","is_m1":false}],
 "m1":{"address":"06:41:F4:57:C7:53","name":"M1-c753","is_m1":true},
 "controller":{"available":true,"powered":true,"name":"bzbk007-pc"},
 "configured_addr":null}
```

### `GET /api/status`

连接打印机并查询状态（只读，不走纸）。支持 `?model=`、`?addr=`。

```bash
curl -s http://127.0.0.1:17600/api/status
```

```json
{"model":"hprt-m1","addr":"06:41:F4:57:C7:53","ok":true,"status":"正常",
 "value":0,"value_hex":"0x0000","errors":[],"printing":false,
 "pooli_sta":null,"raw_hex":"00 00 01 41 0f 00 4b 02 ..."}
```

打印机离线 / 地址不可达：

```bash
curl -s 'http://127.0.0.1:17600/api/status?addr=00:00:00:00:00:00'
```

```json
{"ok":false,"model":"hprt-m1","addr":"00:00:00:00:00:00","status":"离线",
 "errors":["连接打印机失败：..."],"value":-1,"error":"连接打印机失败：..."}
```

状态位（回包前 2 字节 LE）：bit0 缺纸、bit1 过热、bit2 过冷、bit3 低电、
bit4 开盖、bit5 低压、bit6 定位失败；`raw_hex` 中还带 `pooli_sta=<byte>`。

### `POST /api/preview` → `image/png`

请求体为标签 spec，**直接返回 PNG 二进制**（不是 base64）。响应头带
`X-Label-Width-Px` / `X-Label-Height-Px` / `X-Label-Width-Mm` / `X-Label-Height-Mm` /
`X-Label-Offset-X-Mm` / `X-Label-Offset-X-Px` 便于前端布局。
PNG 宽度是**含纸卷偏移的画布宽**：默认 40mm 标签 → 400px（offset 0 时 → 320px）。

```bash
curl -s -X POST http://127.0.0.1:17600/api/preview \
  -H 'Content-Type: application/json' \
  -d '{"width_mm":40,"height_mm":30,"border":true,
       "items":[{"type":"text","text":"物料 A-102","size":28,"bold":true,"x_mm":2,"y_mm":2},
                {"type":"barcode","data":"ABC123","height_mm":10,"x_mm":2,"y_mm":14},
                {"type":"qr","data":"https://example.com","size_mm":12,"x_mm":25,"y_mm":15}]}' \
  -o /tmp/label.png
```

### `GET /api/preview?spec=<urlencoded json>`

便于 `<img src="http://127.0.0.1:17600/api/preview?spec=...">` 直接引用；
也支持便捷参数 `?text=HELLO&barcode=ABC123&qr=https://x&width_mm=40&height_mm=30`。

```bash
curl -s 'http://127.0.0.1:17600/api/preview?spec=%7B%22width_mm%22%3A40%2C%22height_mm%22%3A30%2C%22items%22%3A%5B%7B%22type%22%3A%22text%22%2C%22text%22%3A%22HELLO%22%7D%5D%7D' -o /tmp/label2.png
```

### `POST /api/print`

请求体 = 标签 spec + 打印选项（`copies`/`density`/`align`/`no_auto_learn`/`form_feed`/`addr`）。
打印前自动查状态；定位失败（bit6）时自动测纸（`no_auto_learn: true` 可关闭）。
同一时刻只允许一个打印/测纸/状态任务（并发请求返回 `{"ok":false,"busy":true}`）。

```bash
curl -s -X POST http://127.0.0.1:17600/api/print \
  -H 'Content-Type: application/json' \
  -d '{"width_mm":40,"height_mm":30,"copies":1,"density":75,"align":"left",
       "items":[{"type":"text","text":"物料 A-102","size":28,"bold":true}]}'
```

```json
{"ok":true,"model":"hprt-m1","addr":"06:41:F4:57:C7:53",
 "status":"正常","value":0,"errors":[],"printing":false,
 "width_px":400,"height_px":240,"offset_x_mm":10,"copies":1,"density":75,
 "frames":10,"rows":240,"row_bytes":40,"stream_bytes":1581,
 "elapsed_s":2.1,"warnings":[]}
```

### `POST /api/learn`

测纸/定位（会让打印机走纸数张，仅在定位失败或换纸型后使用）。

```bash
curl -s -X POST http://127.0.0.1:17600/api/learn -H 'Content-Type: application/json' -d '{}'
```

### 标签 spec 契约（与前端 `ui/` 严格一致）

```json
{
  "width_mm": 40,
  "height_mm": 30,
  "offset_x_mm": 10,
  "border": false,
  "density": 75,
  "align": "left",
  "copies": 1,
  "model": "hprt-m1",
  "no_auto_learn": false,
  "form_feed": false,
  "items": [
    {"type": "text", "text": "物料 A", "size": 28, "bold": true,
     "align": "left", "x_mm": 2, "y_mm": 2},
    {"type": "barcode", "data": "ABC123", "symbology": "code128",
     "height_mm": 12, "show_text": true, "x_mm": 2, "y_mm": 15},
    {"type": "qr", "data": "https://x", "size_mm": 14, "x_mm": 18, "y_mm": 14},
    {"type": "gap", "mm": 2}
  ]
}
```

- **尺寸单位**：全部为毫米；画布像素 = `round(mm / 25.4 * 203)`，1mm ≈ 8dot。
- **纸卷偏移** `offset_x_mm`：标签内容在打印头坐标里的左偏移，默认 **10.0**
  （M1 纸筒挡板在左侧，40mm 卷筒整体右移 10mm），范围 0–20，步进 0.5。
  **输出位图宽度 = `offset_x_mm + width_mm`**：40mm 标签 + 10mm 偏移 → 400×240px；
  50mm 纸请设为 0（→ 400px）。偏移区为空白（不打印）。
  文本 `size` 例外，是 203dpi 下的像素字号。
- **定位**：`x_mm`/`y_mm` 可选，给出时按绝对坐标放置（元素左/上边界）；
  缺省按纵向堆叠。`gap` 只在堆叠布局中增加垂直间距。
- **文本**：支持中文与多行（`\n`）；`align` 为 `left|center|right`，
  在 `x_mm` 到右内边距的区域内对齐；`size` 范围 4–400px。
- **条码**：`symbology` 支持 `code128/code39/code93/ean13/ean8/upca/upce/itf14/
  pdf417/datamatrix/qr/aztec/hanxin` 等；`height_mm` 为条码元素总高（含下方数字）。
- **二维码**：`size_mm` 为目标边长，按整数模块像素渲染后居中（实际尺寸可能略微取整）。
- **约束**：M1 标签宽 ≤ 50mm，且 `offset_x_mm + width_mm` 对应像素 ≤ 400px
  （超出明确报错）；400px 超过打印头 384dot 时返回 `warnings` 提示。
- 浓度会吸附到 M1 四档 `35/55/75/95`；`copies` 1–99。

## 纸卷偏移（重要）

M1 的**纸筒挡板在左侧**，纸筒仓最大 50mm；我们常用 40mm 卷筒，装入后纸卷
相对打印头原点整体**向右偏移 10mm**。若位图不从 10mm 处开始画内容，打印时
**左侧会被裁掉、文字显示不全**。

因此 spec 增加 `offset_x_mm`（默认 `10.0`）：

- 渲染画布宽 = `offset_x_mm + width_mm`（像素），内容整体右移 `offset_x_mm`；
  40mm 纸 + 10mm = **400px**，正好是 M1 的位图宽度上限（`head_dots 384 + 16` 容差）。
- 标签内容坐标（`x_mm`）仍以**标签自身左上角**为原点，无需手动加偏移。
- 40mm 纸用 `10`；50mm 纸用 `0`。若 `offset + width` 超过 400px，接口会明确报错
  （提示调整 `offset_x_mm` 或减宽）。
- 前端/API 调用不传该字段时按默认 10 处理；`GET /api/models` 的
  `spec_defaults.offset_x_mm` 与机型 `default_offset_x_mm` 也给出该默认值。

示例：40×30mm 纸 + 10mm 偏移的实际打印位图为 400×240px，最左侧 80px 为空白
（物理上纸卷在该区域没有纸/不打印）。

## 渲染与打印行为

- 渲染输出 RGB 白底黑内容；打印时驱动按 `(R+G+B)/3 <= 128` 二值化为 1bpp
  （1=黑、MSB=最左、行字节 `ceil(w/8)`），送打印机前按 1024 字节整行分块，
  每块做 LZO1X-1 压缩并封装为 `1D 76 30 30` 帧。
- 打印流程：连接（自动发现 `M1-xxxx`）→ 握手（`1B 1C "set mm" n m` 收到 `OK`
  后所有写入 XOR(n+m)）→ 查状态（定位失败则自动测纸）→ setp/setc/align → 位图帧
  → `1B 1B 01 5A 00` 开始打印 → 可选 `1D 66 C0 03` 走纸到撕纸位 → 等待打印完成。
- 每次 API 请求独立连接/断开，避免长期占用蓝牙设备；状态查询有 3s 缓存（可配）。
- 并发：进程内 `threading.Lock` 串行化所有设备操作；打印/测纸遇到占用立即返回
  `{"ok":false,"busy":true}`，状态查询会排队等待（最多 15s）。

## 新增机型（多机型扩展）

1. 在 `printers/` 新建模块，实现 `PrinterBackend`：

```python
from .base import Capabilities, PrinterBackend, PrinterStatus

class MyPrinterBackend(PrinterBackend):
    capabilities = Capabilities(
        model_id="my-printer", name="某品牌 X1", dpi=300,
        head_dots=640, max_width_mm=54.0,
        default_label={"width_mm": 50.0, "height_mm": 30.0},
        densities=(50, 75, 100), connection="usb",
    )

    def __init__(self, addr: str | None = None):
        self._addr = addr

    def connect(self, addr=None): ...
    def status(self, **kw) -> PrinterStatus: ...
    def learn(self, **kw) -> PrinterStatus: ...
    def print_image(self, image, *, copies=1, density=75, align="left",
                    auto_learn=True, form_feed=False) -> PrinterStatus: ...
    def close(self): ...
```

2. 在 `printers/__init__.py` 中注册：

```python
from .my_printer import MyPrinterBackend
register(MyPrinterBackend.capabilities.model_id, MyPrinterBackend)
```

3. 前端在 spec 里传 `"model": "my-printer"` 即可；`GET /api/models` 自动列出。
   未知机型返回 `{"ok":false,"error":"不支持的机型 ...","supported_models":[...]}`。

上层（HTTP/渲染/锁）不感知传输与指令集差异；`PrinterBackend` 只需实现
`connect/status/learn/print_image/close` 与 `capabilities`。

## 协议参考资料

- 驱动与协议实测代码：`../tool/m1_escpoli.py`（SPP 握手/XOR、GS v00 帧、状态解析）
- 渲染参考：`../tool/label_render.py`
- APK 逆向字节级规格：`../re-apk/PROTOCOL.md`（ESC/POLI、位图、LZO 帧、状态位）

## 故障排查

| 现象 | 处理 |
| --- | --- |
| `找不到 M1 打印机` | 打印机开机；`bluetoothctl devices` 能看到 `M1-xxxx`；必要时系统蓝牙里重新配对 |
| `[Errno 16] Device or resource busy` | 该 RFCOMM 地址/通道被其他程序占用；关闭其他客户端或换 `--addr` |
| 握手失败（未收到 OK） | 打印机忙/未就绪；稍后重试；确认是经典蓝牙 SPP 而非 BLE |
| `/api/status` 很久才返回 | 首次连接需 3~5s；之后有 `GONG_FENG_STATUS_TTL` 缓存（默认 3s） |
| `未安装 zint 命令` | `sudo pacman -S zint` |
| `未找到 liblzo2` | `sudo pacman -S lzo` |
| 端口被占用 | `--port` 换端口，并同步前端 `VITE_API_BASE`/`api.js` 中的地址 |
| 二维码扫不出来 | 增大 `size_mm`（模块像素更大）；避免贴在强反光面 |
| 内容左侧被裁/文字不全 | 纸卷偏移没加：40mm 纸设 `offset_x_mm=10`，50mm 纸设 0 |
| 位图越界/右侧缺字 | `offset_x_mm + width_mm` 超过 400px，减小其一 |
| 50mm 宽标签右侧缺失 | M1 打印头 384dot≈48mm，右侧约 2mm 不可打印 |

## 安全说明

服务默认只监听 `127.0.0.1`，不对外网开放，也不做认证——请勿直接
`--host 0.0.0.0` 暴露到不可信网络。`/api/print` 会真实打印耗材，前端联调时
请先用 `/api/preview` 确认版式。
