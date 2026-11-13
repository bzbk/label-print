# 共风标签打印 · GoFor Label Print

> 开源 PC 标签打印工具：Vue 3 标签设计器 + Python 打印服务 + MCP/skill（AI 可直接调用）。
> 首发支持 **汉印 HPRT M1** 便携标签打印机，架构上按机型抽象，欢迎扩展其它机型。

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

**仓库**：[GitHub · bzbk/label-print](https://github.com/bzbk/label-print) ｜ **镜像**：[gitea.gofor.ltd · bzbk007/label-print](https://gitea.gofor.ltd/bzbk007/label-print)

---

## 特性

- **所见即所得标签设计器**：Vue 3 + Vite，mm 画布、1mm 网格、拖拽/属性面板、文本/条码/二维码、边框。
- **真机直打**：Python 打印服务经经典蓝牙 **SPP** 直连打印机，自研 ESC/POLI 指令生成，**不依赖任何厂商客户端或授权**。
- **AI 可调用**：内置 **MCP server**（stdio JSON-RPC）与 **skill**，任何支持 MCP 的 Agent 都能查询状态、预览、打印。
- **多机型可扩展**：`PrinterBackend` 抽象 + 机型注册表；新增机型只需实现一个后端类并注册。
- **省纸模式**：先 `preview` 渲染 PNG 确认版式，再 `print` 出纸；打印前自动查状态，定位失败自动测纸。

## 支持机型

| 机型 | 连接 | 指令集 | 状态 | 说明 |
|---|---|---|---|---|
| 汉印 HPRT M1 | 经典蓝牙 SPP（RFCOMM 00001101） | HPRT ESC/POLI | ✅ 已实测 | 203dpi / 384dot 打印头 / 默认 40×30mm |

> 新增机型：见 [server/README.md §机型扩展](server/README.md)。协议细节见 [docs/PROTOCOL.md](docs/PROTOCOL.md)（M1 的字节级规格，含握手加密、光栅帧、状态位）。

## 架构

```
┌──────────────────────────┐        HTTP/JSON + PNG        ┌────────────────────────────┐
│ ui/   Vue 3 标签设计器     │  ───────────────────────────▶ │ server/  Python 打印服务     │
│ 画布 / 属性 / 预览 / 打印  │  ◀─────────────────────────── │ spec→渲染→机型后端→蓝牙 SPP │
└──────────────────────────┘        127.0.0.1:17600        └──────────────┬─────────────┘
                                                                          │ RFCOMM
┌──────────────────────────┐        MCP stdio                            ▼
│ mcp/  MCP server          │  ───────────────────────────▶       ┌──────────────┐
│ label_print / status / …  │                                     │ HPRT M1 打印机 │
└──────────────────────────┘                                     └──────────────┘
```

## 快速开始

### 依赖（Arch Linux）

```bash
sudo pacman -S --needed python-pillow zint lzo bluez-utils nodejs npm
```

其它发行版：需要 `Pillow`、`zint` 可执行文件、`liblzo2`、BlueZ。

### 1) 启动打印服务

```bash
cd server
python3 -m gongfeng_print.server --port 17600
# 自检：curl http://127.0.0.1:17600/api/health
```

### 2) 启动标签设计器

```bash
cd ui
npm install
npm run dev          # http://127.0.0.1:5173
# 或构建静态文件：npm run build  (产物在 ui/dist)
```

### 3) 打印机准备

1. 打印机开机，系统蓝牙里能看到 `M1-xxxx`（`bluetoothctl devices`）。
2. 首次连接或换纸后执行一次**测纸**（设计器底部「测纸」按钮，或 `POST /api/learn`）。
3. 装纸注意：**纸筒挡板在左侧，40mm 卷纸需向右偏移 10mm**（见下节）。

## 纸卷偏移（重要）

M1 的纸筒仓宽度 50mm，挡板在**左侧**；使用 40mm 卷纸时纸卷相对打印头原点整体右移 **10mm**。
因此位图必须带 10mm 左侧留白，否则内容左侧会被裁掉（表现为「文字显示不全」）。

- `offset_x_mm`：标签内容在打印头坐标中的左偏移，**40mm 纸=10，50mm 纸=0**，默认 10。
- 渲染宽度 = `offset_x_mm + width_mm`（40+10=50mm=400dot，M1 允许 384+16 容差）。
- 设计器左侧灰色斜纹区即该偏移留白，边框只画在标签区域内。

## HTTP API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 健康检查、依赖状态 |
| GET | `/api/models` | 支持机型、能力、`spec_defaults` |
| GET | `/api/devices` | 蓝牙设备列表，标出 M1 |
| GET | `/api/status` | 连接并查询打印机状态 |
| POST | `/api/preview` | 渲染标签，返回 `image/png` |
| POST | `/api/print` | 打印（spec + copies/density/align） |
| POST | `/api/learn` | 测纸/定位 |

## 标签 spec

```json
{
  "width_mm": 40, "height_mm": 30, "offset_x_mm": 10,
  "border": false, "density": 75,
  "items": [
    {"type": "text", "text": "物料 A-102", "size": 28, "bold": true, "x_mm": 2, "y_mm": 2},
    {"type": "barcode", "data": "ABC12345", "symbology": "code128", "height_mm": 12, "x_mm": 2, "y_mm": 14},
    {"type": "qr", "data": "https://example.com", "size_mm": 12, "x_mm": 26, "y_mm": 14}
  ]
}
```

`x_mm/y_mm` 可选，缺省按纵向堆叠；`barcode` 支持 `code128/code39/ean13/ean8/upca/datamatrix/qr…`（由 zint 生成）。

## MCP 与 skill

- MCP server：`mcp/gofor_label_mcp.py`（stdio），工具：`label_models` / `label_devices` / `label_status` / `label_learn` / `label_preview` / `label_print` / `label_print_image`。
- 接入示例（DSH `cordis.patch.yml` / Claude Desktop 等 MCP 客户端通用）：

```yaml
- insert:
    - id: mcp-gofor-label
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        transport: stdio
        serverName: gofor-label
        command: /path/to/label-print/mcp/gofor_label_mcp.py
        args: []
        toolCallTimeoutMs: 180000
```

- skill：`skill/SKILL.md`（复制到 `~/.dsh/skills/gofor-label-print/` 即生效），含硬件事实、排障、省纸工作流。

## 目录结构

```
label-print/
├── ui/            Vue 3 标签设计器（设计器/预览/打印）
├── server/        Python 打印服务 + 机型后端（hprt_m1）
├── mcp/           MCP stdio server
├── skill/         Agent skill 说明
├── docs/          PROTOCOL.md（字节级协议）、截图
└── LICENSE
```

## 合规声明

- 本项目为**独立实现**：不含任何厂商源码、资源或授权文件；协议知识来自对公开客户端与本设备的互操作性分析，用于让自己的设备可被自己控制。
- 不提供、不参与任何授权/激活绕过。使用厂商软件请遵守其许可。
- 商标「汉印/HPRT」「共风/GoFor」归各自所有者；本项目与打印机厂商无隶属关系。

## License

[MIT](LICENSE)
