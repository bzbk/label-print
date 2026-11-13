/**
 * 标签文档模型：元素增删、mm 尺寸、排版计算、spec 序列化。
 *
 * 前后端契约（严格一致）：
 * {
 *   width_mm, height_mm, border, density,
 *   items: [
 *     { type:'text',    text, size, bold, align, x_mm, y_mm },
 *     { type:'barcode', data, symbology, height_mm, x_mm, y_mm },
 *     { type:'qr',      data, size_mm, x_mm, y_mm }
 *   ]
 * }
 * 仅在后端支持的附加字段（show_text / invert）为可选扩展，默认不输出。
 */

import {
  DOT_PER_MM,
  clamp,
  measureTextWidthDots,
  round1,
  wrapTextLines,
} from './units'

let idSeq = 1

export function newId() {
  return `el${idSeq++}`
}

export const LABEL_PRESETS = [
  { id: '40x30', label: '40 × 30 mm', width_mm: 40, height_mm: 30 },
  { id: '40x60', label: '40 × 60 mm', width_mm: 40, height_mm: 60 },
  { id: '50x30', label: '50 × 30 mm', width_mm: 50, height_mm: 30 },
  { id: 'custom', label: '自定义尺寸…', width_mm: null, height_mm: null },
]

export const SYMBOLOGIES = [
  { value: 'code128', label: 'Code 128' },
  { value: 'ean13', label: 'EAN-13' },
  { value: 'code39', label: 'Code 39' },
]

export const DENSITIES = [35, 55, 75, 95]

export const TYPE_LABELS = { text: '文本', barcode: '条码', qr: '二维码' }

/**
 * 纸卷偏移：M1 纸筒挡板在左侧、纸仓 50mm，
 * 40mm 卷纸相对打印头原点整体右移 10mm；50mm 卷纸为 0。
 * 后端据此把内容绘制在打印头坐标 x=offset 之后，位图宽 = offset + width。
 */
export const OFFSET_X_MM = { default: 10, min: 0, max: 20, step: 0.5 }

export function snapOffsetX(value) {
  const n = Number(value)
  const source = Number.isFinite(n) ? n : OFFSET_X_MM.default
  const snapped = Math.round(source / OFFSET_X_MM.step) * OFFSET_X_MM.step
  return round1(clamp(snapped, OFFSET_X_MM.min, OFFSET_X_MM.max))
}

/**
 * 垂直微调：内容整体上下平移，用于补偿标签在打印头上的纵向位置偏差。
 * 范围 -15 ~ 15mm，步进 0.5；正 = 下移，负 = 上移；位图高度不变。
 */
export const OFFSET_Y_MM = { default: 0, min: -15, max: 15, step: 0.5 }

export function snapOffsetY(value) {
  const n = Number(value)
  const source = Number.isFinite(n) ? n : OFFSET_Y_MM.default
  const snapped = Math.round(source / OFFSET_Y_MM.step) * OFFSET_Y_MM.step
  return round1(clamp(snapped, OFFSET_Y_MM.min, OFFSET_Y_MM.max))
}

/**
 * 底部少走：固件不支持反向走纸，「打印后回退」用「少走」等效实现。
 * 打印时只下发 (标签高度 - trim) 行，预览仍是完整图像。
 * 范围 0 ~ 5mm，步进 0.5；40×30 标签实测 2mm 对齐撕纸梳。
 */
export const TRIM_BOTTOM_MM = { default: 0, min: 0, max: 5, step: 0.5 }

export function snapTrimBottom(value) {
  const n = Number(value)
  const source = Number.isFinite(n) ? n : TRIM_BOTTOM_MM.default
  const snapped = Math.round(source / TRIM_BOTTOM_MM.step) * TRIM_BOTTOM_MM.step
  return round1(clamp(snapped, TRIM_BOTTOM_MM.min, TRIM_BOTTOM_MM.max))
}

export const LIMITS = {
  width_mm: [10, 72],
  height_mm: [10, 300],
  offset_x_mm: [0, 20],
  offset_y_mm: [-15, 15],
  trim_bottom_mm: [0, 5],
  textSize: [6, 120],
  barcodeHeight: [4, 40],
  qrSize: [6, 60],
}

export function createDoc(patch = {}) {
  return {
    name: '未命名标签',
    width_mm: 40,
    height_mm: 30,
    border: false,
    offset_x_mm: OFFSET_X_MM.default,
    offset_y_mm: OFFSET_Y_MM.default,
    trim_bottom_mm: TRIM_BOTTOM_MM.default,
    density: 75,
    align: 'left',
    items: [],
    ...patch,
  }
}

export function createItem(type, doc = createDoc(), patch = {}) {
  const base = { id: newId(), type }
  if (type === 'text') {
    return {
      ...base,
      text: '物料 A',
      size: 24,
      bold: false,
      align: 'left',
      invert: false,
      x_mm: 2,
      y_mm: PRINT_ZONE_TOP_MM,
      ...patch,
    }
  }
  if (type === 'barcode') {
    return {
      ...base,
      data: 'ABC123',
      symbology: 'code128',
      height_mm: 12,
      show_text: true,
      x_mm: 2,
      y_mm: 12,
      ...patch,
    }
  }
  if (type === 'qr') {
    return {
      ...base,
      data: 'https://gofor.example/label',
      size_mm: 14,
      x_mm: 2,
      y_mm: PRINT_ZONE_TOP_MM,
      ...patch,
    }
  }
  throw new Error(`未知元素类型：${type}`)
}

/** 条码示意图宽度（mm）。真实条码由后端 zint 生成，前端只做近似占位。 */
export function barcodeWidthMm(item) {
  const length = [...String(item.data ?? '')].length
  let width
  if (item.symbology === 'ean13') width = 32
  else if (item.symbology === 'code39') width = Math.max(10, length * 2.2 + 5)
  else width = Math.max(10, length * 1.45 + 5)
  return round1(Math.min(width, 120))
}

/** 条码占位总高度：条码高度 + 人眼可读文字高度（若开启） */
export function barcodeBoxHeightMm(item) {
  const height = clamp(Number(item.height_mm) || 12, LIMITS.barcodeHeight[0], LIMITS.barcodeHeight[1])
  return round1(height + (item.show_text ? 3.4 : 0))
}

/** 文本排版：折行结果、占位宽高，全部以 dot 计算后再换算 mm */
export function textLayout(item, doc) {
  const size = clamp(Number(item.size) || 16, LIMITS.textSize[0], LIMITS.textSize[1])
  const x = Number(item.x_mm) || 0
  // 文本最多排到标签右边界，宽度严格按 mm 计算
  const availableDots = Math.max(DOT_PER_MM, (doc.width_mm - x) * DOT_PER_MM)
  const lines = wrapTextLines(String(item.text ?? ''), availableDots, size, !!item.bold)
  let widthDots = 0
  for (const line of lines) {
    widthDots = Math.max(widthDots, measureTextWidthDots(line, size, !!item.bold))
  }
  if (lines.length === 1 && widthDots > 0) {
    widthDots = Math.min(widthDots + size * 0.15, availableDots)
  } else {
    widthDots = availableDots
  }
  const lineHeightDots = size * 1.25
  const heightDots = Math.max(1, lines.length) * lineHeightDots
  return {
    lines,
    size,
    lineHeightDots,
    widthDots,
    heightDots,
    availableDots,
  }
}

/** 元素占位框（mm），用于拖拽边界、对齐、状态栏展示 */
export function itemBoxMm(item, doc) {
  if (item.type === 'text') {
    const layout = textLayout(item, doc)
    return {
      w_mm: round1(layout.widthDots / DOT_PER_MM),
      h_mm: round1(layout.heightDots / DOT_PER_MM),
      layout,
    }
  }
  if (item.type === 'barcode') {
    return { w_mm: barcodeWidthMm(item), h_mm: barcodeBoxHeightMm(item), layout: null }
  }
  const size = clamp(Number(item.size_mm) || 14, LIMITS.qrSize[0], LIMITS.qrSize[1])
  return { w_mm: size, h_mm: size, layout: null }
}

/**
 * 硬件可打印区域（实测）：打印起点距标签顶部 4mm，底部 1mm 也不可打印。
 * 40×30 标签 → 可打印区 y = 4 ~ 29mm（25mm）。仅用于画布标注/软提示，不限制拖拽。
 */
export const PRINT_ZONE_TOP_MM = 4
export const PRINT_ZONE_BOTTOM_MM = 1

export function printableZoneMm(doc) {
  const height = Number(doc?.height_mm) || 0
  const topMm = PRINT_ZONE_TOP_MM
  const bottomMm = round1(Math.max(topMm, height - PRINT_ZONE_BOTTOM_MM))
  return {
    topMm,
    bottomMm,
    heightMm: round1(Math.max(0, bottomMm - topMm)),
  }
}

/** 元素是否超出可打印区域（y < top 或 y + 高度 > bottom）；软提示，不阻止保存/打印 */
export function itemOutsidePrintable(item, doc) {
  const box = itemBoxMm(item, doc)
  const zone = printableZoneMm(doc)
  const y = Number(item.y_mm) || 0
  return {
    zone,
    box,
    outside: y < zone.topMm - 0.05 || y + box.h_mm > zone.bottomMm + 0.05,
    above: y < zone.topMm - 0.05,
    below: y + box.h_mm > zone.bottomMm + 0.05,
  }
}

/** 是否超出标签范围（超出部分打印会被裁掉，画布上给出红色告警） */
export function itemOverflow(item, doc) {
  const box = itemBoxMm(item, doc)
  const x = Number(item.x_mm) || 0
  const y = Number(item.y_mm) || 0
  return {
    box,
    right: x + box.w_mm > doc.width_mm + 0.05,
    bottom: y + box.h_mm > doc.height_mm + 0.05,
  }
}

/** 把元素位置限制在标签内（完整可见，避免“文字显示不全”） */
export function normalizeItemPosition(item, doc) {
  const box = itemBoxMm(item, doc)
  const maxX = Math.max(0, round1(doc.width_mm - Math.min(box.w_mm, doc.width_mm)))
  const maxY = Math.max(0, round1(doc.height_mm - Math.min(box.h_mm, doc.height_mm)))
  item.x_mm = clamp(round1(Number(item.x_mm) || 0), 0, maxX)
  item.y_mm = clamp(round1(Number(item.y_mm) || 0), 0, maxY)
  return item
}

/** 新增元素时按纵向顺序摆放（避开已有元素） */
export function autoPlaceItem(item, doc) {
  const box = itemBoxMm(item, doc)
  let y = PRINT_ZONE_TOP_MM
  for (const other of doc.items) {
    const otherBox = itemBoxMm(other, doc)
    y = Math.max(y, (Number(other.y_mm) || 0) + otherBox.h_mm + 1.5)
  }
  const maxY = Math.max(0, round1(doc.height_mm - box.h_mm))
  item.x_mm = 2
  if (item.x_mm + box.w_mm > doc.width_mm) item.x_mm = 0
  item.y_mm = round1(clamp(y, 0, maxY))
  return item
}

export function clampItemFields(item) {
  if (item.type === 'text') {
    item.size = clamp(Number(item.size) || 24, LIMITS.textSize[0], LIMITS.textSize[1])
    item.align = ['left', 'center', 'right'].includes(item.align) ? item.align : 'left'
    item.text = String(item.text ?? '')
    item.bold = !!item.bold
    item.invert = !!item.invert
  } else if (item.type === 'barcode') {
    item.height_mm = clamp(Number(item.height_mm) || 12, LIMITS.barcodeHeight[0], LIMITS.barcodeHeight[1])
    if (!SYMBOLOGIES.some((s) => s.value === item.symbology)) item.symbology = 'code128'
    item.data = String(item.data ?? '')
    item.show_text = !!item.show_text
  } else if (item.type === 'qr') {
    item.size_mm = clamp(Number(item.size_mm) || 14, LIMITS.qrSize[0], LIMITS.qrSize[1])
    item.data = String(item.data ?? '')
  }
  return item
}

/** 前端内部元素 -> 后端 spec 元素（严格字段） */
export function itemToSpec(item) {
  const base = {
    type: item.type,
    x_mm: round1(Number(item.x_mm) || 0),
    y_mm: round1(Number(item.y_mm) || 0),
  }
  if (item.type === 'text') {
    const spec = {
      ...base,
      text: String(item.text ?? ''),
      size: Math.round(Number(item.size) || 24),
      bold: !!item.bold,
      align: ['left', 'center', 'right'].includes(item.align) ? item.align : 'left',
    }
    if (item.invert) spec.invert = true // 可选扩展：反白
    return spec
  }
  if (item.type === 'barcode') {
    const spec = {
      ...base,
      data: String(item.data ?? ''),
      symbology: item.symbology || 'code128',
      height_mm: round1(Number(item.height_mm) || 12),
    }
    if (item.show_text) spec.show_text = true // 可选扩展：人眼可读文字
    return spec
  }
  if (item.type === 'qr') {
    return {
      ...base,
      data: String(item.data ?? ''),
      size_mm: round1(Number(item.size_mm) || 14),
    }
  }
  return base
}

/** 编辑器文档 -> 后端 / 文件 spec */
export function toSpec(doc) {
  const spec = {
    width_mm: round1(doc.width_mm),
    height_mm: round1(doc.height_mm),
    border: !!doc.border,
    offset_x_mm: snapOffsetX(doc.offset_x_mm ?? OFFSET_X_MM.default),
    offset_y_mm: snapOffsetY(doc.offset_y_mm ?? OFFSET_Y_MM.default),
    trim_bottom_mm: snapTrimBottom(doc.trim_bottom_mm ?? TRIM_BOTTOM_MM.default),
    density: DENSITIES.includes(Number(doc.density)) ? Number(doc.density) : 75,
    items: doc.items.map(itemToSpec),
  }
  // 顶层 align 是「打印对齐」（后端支持 left/center/right），默认 left 不输出，
  // 保证默认导出的 JSON 与契约示例完全一致。
  if (doc.align && doc.align !== 'left') spec.align = doc.align
  return spec
}

function toNumber(value, fallback) {
  const n = Number(value)
  return Number.isFinite(n) ? n : fallback
}

/** 文件 / 后端 spec -> 编辑器文档（宽松容错，缺失字段用默认值） */
export function fromSpec(raw) {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    throw new Error('JSON 根节点必须是对象')
  }
  const width = Number(raw.width_mm)
  const height = Number(raw.height_mm)
  if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) {
    throw new Error('缺少合法的 width_mm / height_mm（必须为正数）')
  }
  const doc = createDoc({
    width_mm: clamp(round1(width), LIMITS.width_mm[0], LIMITS.width_mm[1]),
    height_mm: clamp(round1(height), LIMITS.height_mm[0], LIMITS.height_mm[1]),
    border: !!raw.border,
    offset_x_mm: snapOffsetX(raw.offset_x_mm ?? OFFSET_X_MM.default),
    offset_y_mm: snapOffsetY(raw.offset_y_mm ?? OFFSET_Y_MM.default),
    trim_bottom_mm: snapTrimBottom(raw.trim_bottom_mm ?? TRIM_BOTTOM_MM.default),
    density: DENSITIES.includes(Number(raw.density)) ? Number(raw.density) : 75,
    align: ['left', 'center', 'right'].includes(raw.align) ? raw.align : 'left',
    items: [],
  })
  const items = Array.isArray(raw.items) ? raw.items : []
  for (const rawItem of items) {
    if (!rawItem || typeof rawItem !== 'object') continue
    const type = rawItem.type
    if (!['text', 'barcode', 'qr'].includes(type)) continue
    const item = createItem(type, doc)
    item.x_mm = round1(toNumber(rawItem.x_mm, item.x_mm))
    item.y_mm = round1(toNumber(rawItem.y_mm, item.y_mm))
    if (type === 'text') {
      item.text = String(rawItem.text ?? '')
      item.size = clamp(toNumber(rawItem.size, 24), LIMITS.textSize[0], LIMITS.textSize[1])
      item.bold = !!rawItem.bold
      item.align = ['left', 'center', 'right'].includes(rawItem.align) ? rawItem.align : 'left'
      item.invert = !!rawItem.invert
    } else if (type === 'barcode') {
      item.data = String(rawItem.data ?? '')
      item.symbology = SYMBOLOGIES.some((s) => s.value === rawItem.symbology) ? rawItem.symbology : 'code128'
      item.height_mm = clamp(toNumber(rawItem.height_mm, 12), LIMITS.barcodeHeight[0], LIMITS.barcodeHeight[1])
      item.show_text = rawItem.show_text !== false
    } else {
      item.data = String(rawItem.data ?? '')
      item.size_mm = clamp(toNumber(rawItem.size_mm, 14), LIMITS.qrSize[0], LIMITS.qrSize[1])
    }
    clampItemFields(item)
    normalizeItemPosition(item, doc)
    doc.items.push(item)
  }
  return doc
}

/** 内置模板（纯前端示例数据，便于一上手就是可用的物料/物流标签） */
export function templateDoc(name = 'material') {
  if (name === 'logistics') {
    return createDoc({
      name: '物流标签 40×60',
      width_mm: 40,
      height_mm: 60,
      border: true,
      density: 75,
      items: [
        createItem('text', null, { text: '收件城市：上海', size: 20, bold: true, x_mm: 2, y_mm: PRINT_ZONE_TOP_MM }),
        createItem('text', null, { text: '运单号 SF1234567890', size: 14, x_mm: 2, y_mm: 7.5 }),
        createItem('barcode', null, {
          data: 'SF1234567890',
          symbology: 'code128',
          height_mm: 14,
          show_text: true,
          x_mm: 2,
          y_mm: 13,
        }),
        createItem('text', null, { text: '重量：2.5 kg · 易碎品', size: 14, x_mm: 2, y_mm: 32 }),
        createItem('qr', null, { data: 'https://gofor.example/track/SF1234567890', size_mm: 14, x_mm: 2, y_mm: 38 }),
        createItem('text', null, { text: '共风物流', size: 13, bold: true, align: 'right', x_mm: 20, y_mm: 38 }),
        createItem('text', null, { text: '请轻放 · 防潮', size: 12, align: 'right', x_mm: 20, y_mm: 43 }),
      ],
    })
  }
  return createDoc({
    name: '物料标签 40×30',
    width_mm: 40,
    height_mm: 30,
    border: false,
    density: 75,
    items: [
      createItem('text', null, { text: '物料 A', size: 22, bold: true, x_mm: 2, y_mm: PRINT_ZONE_TOP_MM }),
      createItem('text', null, { text: '规格 24×18 mm', size: 15, x_mm: 2, y_mm: 7.5 }),
      createItem('barcode', null, {
        data: 'ABC123456',
        symbology: 'code128',
        height_mm: 9,
        show_text: true,
        x_mm: 2,
        y_mm: 14,
      }),
      createItem('qr', null, { data: 'https://gofor.example/m/ABC123456', size_mm: 12, x_mm: 26, y_mm: 14 }),
    ],
  })
}
