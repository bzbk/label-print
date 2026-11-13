---
name: gofor-label-print
description: Use when printing labels on the GoFor/共风 label-print setup with a HPRT (汉印) M1 portable label printer on this Linux host — whether via the Vue label designer UI, the Python print service REST API, the `hub`/`gofor-label` MCP tools (label_preview/label_print/label_status/label_learn), or the CLI. Covers the Bluetooth SPP link, the ESC/POLI raster frames, the mandatory handshake+XOR, the paper-roll offset (40 mm roll = +10 mm), label-gap learning, and the paper-wasting pitfalls to avoid.
---

# 共风标签打印 · GoFor Label Print（汉印 HPRT M1）

## 0. 适用场景

- 要打印自定义标签（文本/条码/二维码）到汉印 M1。
- 想通过 AI/Agent 直接打印（MCP 工具）。
- 打印不出、内容被裁、文字显示不全、定位失败、卡纸等排障。
- 需要扩展其它打印机机型（见 server/README.md 机型扩展）。

## 1. 本机事实（先读，别重复探查）

| 项 | 值 |
|---|---|
| 打印机 | 汉印 HPRT M1（BLE 名称 `M1-c753`，蓝牙地址 `06:41:F4:57:C7:53`） |
| 连接方式 | **经典蓝牙 SPP**（RFCOMM UUID `00001101`，channel 1），不是 BLE |
| 指令集 | **HPRT ESC/POLI 私有二进制**（不是 TSPL/CPCL/ESC-POS） |
| 分辨率 | 203 dpi（8 dot/mm），打印头 384 dot（约 48mm） |
| 标签纸 | **40mm 宽 × 30mm 高**（默认），纸筒仓 50mm |
| 纸卷偏移 | **挡板在左侧，40mm 纸需右移 10mm**（`offset_x_mm=10`）→ 渲染 400px 宽 |
| 仓库 | `~/Desktop/label print/label-print`（GitHub: `bzbk/label-print`） |

## 2. 三层用法（任选其一）

### A. MCP（推荐给 AI）

工具（serverName `gofor-label`）：

| 工具 | 用途 |
|---|---|
| `label_models` | 机型/能力/默认参数 |
| `label_devices` | 找打印机地址 |
| `label_status` | 缺纸/开盖/低电/定位失败 |
| `label_learn` | 测纸（**会走纸数张，别乱调**） |
| `label_preview` | 只渲染 PNG，省纸验证版式 |
| `label_print` | 打印（文本/条码/二维码） |
| `label_print_image` | 打印图片文件 |

标准工作流：`label_status` → 若定位失败则 `label_learn` → `label_preview` 确认 → `label_print`。

### B. HTTP 服务

```bash
cd "~/Desktop/label print/label-print/server"
python3 -m gongfeng_print.server --port 17600 &
curl -s http://127.0.0.1:17600/api/status
curl -s http://127.0.0.1:17600/api/models
curl -s -X POST http://127.0.0.1:17600/api/preview -H 'Content-Type: application/json' \
  -d '{"width_mm":40,"height_mm":30,"offset_x_mm":10,"items":[{"type":"text","text":"测试","size":28,"bold":true}]}' -o /tmp/p.png
```

### C. CLI

```bash
cd "~/Desktop/label print/label-print/server"
python3 -m gongfeng_print.cli --status              # 若提供
```

## 3. 协议要点（实现/排障必知）

1. **握手加密**：连接后发 `1B 1C 73 65 74 20 6D 6D n m`，回 `4F 4B`("OK")，之后**所有字节 XOR (n+m)**；否则打印机静默丢弃（表现为"完全没反应"）。
2. **位图帧**：`1D 76 30 30 | wBytes LE16 | rows LE16 | lzoLen LE32 | LZO1X-1(raw)`；整行块 ≤1024 字节，每块一帧；1bpp、1=黑、MSB 最左、`avg(RGB)<=128` 判黑。
3. **状态**：`1B 12 73`，回包前 2 字节 LE 按位：bit0 缺纸、1 过热、2 过冷、3 低电、4 开盖、5 低压、**6 定位失败**；另有 `pooli_sta=<byte>`。
4. **出纸**：帧全部发完后 `1B 1B 01 5A 00` 触发打印；`1D 66 C0 03` 走纸到撕纸位（会多走约 120mm，默认关闭）。
5. **测纸**：`1D 73 65 74 70 02` 设标签纸 + `1D 73 65 74 4C` 测间隙；测纸会连续走纸直到找到缝隙。

## 4. 排障

| 现象 | 原因 | 处理 |
|---|---|---|
| 完全没反应 | 未握手/未 XOR；或打印机被别的设备占用 | 先 `label_status`，握手 OK 才有后续 |
| 文字/内容左侧被裁 | 纸卷偏移未加 | `offset_x_mm=10`（40mm 纸）|
| 只有一部分内容/越界 | 位图宽超过 400px | `offset+width ≤ 50mm`；减宽或减 offset |
| 状态 = 定位失败(0x40) | 换纸/首次使用未测纸 | `label_learn`（走纸数张，属正常） |
| 打印空白 | 帧顺序错（浓度/对齐在帧后）或 LZO 不兼容 | 命令顺序：setp → setc → 对齐 → 帧 → 打印 |
| 连接失败 | 未配对/蓝牙关闭 | `bluetoothctl devices` 能看到 M1-xxxx；必要时重新配对 |
| 卡纸 | 纸卷装反/偏移 | 挡板在左，纸卷靠左装入后整体右移 10mm |

**省纸原则**：先 `label_preview` 看 PNG；`label_learn` 只在状态报定位失败时调用；不要用 `form_feed` 打小标签。

## 5. 扩展新机型

1. `server/gongfeng_print/printers/<model>.py` 实现 `PrinterBackend`（connect/status/learn/print_image/close + Capabilities）。
2. `printers/__init__.py` 里 `register(Caps.model_id, Backend)`。
3. `GET /api/models` 自动出现；MCP `label_models` 同步可见。
