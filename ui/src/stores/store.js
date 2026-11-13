/**
 * 全局编辑器状态（不引入 pinia，纯 Vue reactive）。
 * 画布/面板/工具条共享同一份 doc，保存与打印都从 toSpec(doc) 序列化。
 */

import { computed, reactive } from 'vue'
import { api } from '../api'
import {
  DENSITIES,
  LIMITS,
  TYPE_LABELS,
  autoPlaceItem,
  clampItemFields,
  createDoc,
  createItem,
  fromSpec,
  PRINT_ZONE_TOP_MM,
  itemBoxMm,
  newId,
  normalizeItemPosition,
  snapOffsetX,
  snapOffsetY,
  snapTrimBottom,
  templateDoc,
  toSpec,
} from '../label'
import { clamp, round1 } from '../units'
import { prepareImageElementFile } from '../imageElement'

const MAX_TOASTS = 4
let toastSeq = 0

/* ------------------------------------------------------- 打印机记忆（localStorage） */

const PRINTER_STORAGE_KEY = 'gofor-label-printer-v1'

function readStoredPrinter() {
  try {
    if (typeof window === 'undefined' || !window.localStorage) return null
    const raw = window.localStorage.getItem(PRINTER_STORAGE_KEY)
    if (!raw) return null
    const data = JSON.parse(raw)
    if (!data || typeof data.addr !== 'string' || !data.addr.trim()) return null
    return {
      addr: data.addr.trim().toUpperCase(),
      name: typeof data.name === 'string' ? data.name : '',
      isM1: !!data.isM1,
      model: typeof data.model === 'string' ? data.model : '',
    }
  } catch {
    return null
  }
}

function persistPrinter(printer) {
  try {
    if (typeof window === 'undefined' || !window.localStorage) return
    if (!printer.addr) {
      window.localStorage.removeItem(PRINTER_STORAGE_KEY)
      return
    }
    window.localStorage.setItem(
      PRINTER_STORAGE_KEY,
      JSON.stringify({
        addr: printer.addr,
        name: printer.name || '',
        isM1: !!printer.isM1,
        model: printer.model || '',
      }),
    )
  } catch {
    /* localStorage 不可用（隐私模式）时忽略 */
  }
}

const storedPrinter = readStoredPrinter()

/* ------------------------------------------- 打印机参数（207/208）记忆 */

const PRINTER_SETTINGS_STORAGE_KEY = 'gofor-label-printer-settings-v1'
const PRINTER_PARAM_DEFAULTS = { start_pos_mm: 0, feed_after_pos_mm: -2 }
const PRINTER_PARAM_RANGE = [-5, 5]

function snapParamValue(value, fallback) {
  const n = Number(value)
  const source = Number.isFinite(n) ? n : fallback
  const snapped = Math.round(source / 0.5) * 0.5
  return round1(clamp(snapped, PRINTER_PARAM_RANGE[0], PRINTER_PARAM_RANGE[1]))
}

function readStoredPrinterSettings() {
  try {
    if (typeof window === 'undefined' || !window.localStorage) return { ...PRINTER_PARAM_DEFAULTS }
    const raw = window.localStorage.getItem(PRINTER_SETTINGS_STORAGE_KEY)
    if (!raw) return { ...PRINTER_PARAM_DEFAULTS }
    const data = JSON.parse(raw)
    if (!data || typeof data !== 'object') return { ...PRINTER_PARAM_DEFAULTS }
    return {
      start_pos_mm: snapParamValue(data.start_pos_mm, PRINTER_PARAM_DEFAULTS.start_pos_mm),
      feed_after_pos_mm: snapParamValue(data.feed_after_pos_mm, PRINTER_PARAM_DEFAULTS.feed_after_pos_mm),
    }
  } catch {
    return { ...PRINTER_PARAM_DEFAULTS }
  }
}

function persistPrinterSettings(params) {
  try {
    if (typeof window === 'undefined' || !window.localStorage) return
    window.localStorage.setItem(
      PRINTER_SETTINGS_STORAGE_KEY,
      JSON.stringify({
        start_pos_mm: snapParamValue(params.start_pos_mm, PRINTER_PARAM_DEFAULTS.start_pos_mm),
        feed_after_pos_mm: snapParamValue(
          params.feed_after_pos_mm,
          PRINTER_PARAM_DEFAULTS.feed_after_pos_mm,
        ),
      }),
    )
  } catch {
    /* localStorage 不可用时忽略 */
  }
}

const storedPrinterSettings = readStoredPrinterSettings()

export const state = reactive({
  // 首次打开给一份可编辑的示例标签，便于直接看到 40×30 的真实排版效果；
  // 「新建」会得到空白标签。
  doc: templateDoc('material'),
  selectedId: null,
  /** 画布缩放：1 = 1 dot 渲染为 1 CSS px，默认略放大便于编辑 */
  zoom: 2.6,
  copies: 1,
  backend: {
    phase: 'idle', // idle | checking | online | offline
    info: null,
    error: '',
    lastCheck: 0,
    learning: false,
  },
  preview: {
    open: false,
    loading: false,
    url: '',
    error: '',
    spec: null,
  },
  /** 当前选择的打印机（地址记住在 localStorage，下次打开仍显示） */
  printer: {
    addr: storedPrinter?.addr || '',
    name: storedPrinter?.name || '',
    isM1: storedPrinter?.isM1 || false,
    model: storedPrinter?.model || '',
    // idle | connecting | connected | error
    phase: 'idle',
    statusLabel: '',
    error: '',
    lastConnectedAt: 0,
  },
  /** 打印机设置（写入设备）：207 起始打印位置 / 208 定位后走纸长度 */
  printerParams: {
    start_pos_mm: storedPrinterSettings.start_pos_mm,
    feed_after_pos_mm: storedPrinterSettings.feed_after_pos_mm,
    writing: false,
    written: [],
    error: '',
    lastWrittenAt: 0,
  },
  /** 图片元素处理中（文件读取/栅格化/压缩） */
  imageProcessing: false,
  /** 打印机扫描/选择弹窗 */
  printerDialog: {
    open: false,
    loading: false,
    scanning: false,
    devices: [],
    m1: null,
    hint: '',
    error: '',
    scanned: false,
    lastScan: 0,
    connectingAddr: '',
    connectError: null,
  },
  printing: false,
  toasts: [],
})

export const selectedItem = computed(
  () => state.doc.items.find((item) => item.id === state.selectedId) || null,
)

export const spec = computed(() => toSpec(state.doc))
export const specJson = computed(() => JSON.stringify(spec.value, null, 2))

/** 属性面板展示用：图片 src 只显示前 160 字符（复制/导出仍是完整 dataURL） */
export const specJsonShort = computed(() => {
  const copy = JSON.parse(JSON.stringify(spec.value))
  for (const item of copy.items || []) {
    if (item.type === 'image' && typeof item.src === 'string' && item.src.length > 160) {
      const omitted = item.src.length - 160
      item.src = `${item.src.slice(0, 160)}…（省略 ${omitted} 字符；复制/导出时包含完整 dataURL）`
    }
  }
  return JSON.stringify(copy, null, 2)
})

/** 当前打印机连接状态：未选 / 连接中 / 已连接 / 离线 */
export const printerConnection = computed(() => {
  if (!state.printer.addr) return { key: 'none', label: '未选' }
  if (state.printer.phase === 'connecting') return { key: 'connecting', label: '连接中' }
  if (state.printer.phase === 'connected' && state.backend.phase !== 'offline') {
    return { key: 'connected', label: '已连接' }
  }
  return { key: 'offline', label: '离线' }
})

export const currentPrinterLabel = computed(() => {
  if (!state.printer.addr) return ''
  const name = state.printer.name || state.printer.addr
  return name === state.printer.addr ? name : `${name} (${state.printer.addr})`
})

/** 打印机型号展示名（hprt-m1 → M1），不展示厂商名 */
export const printerModelLabel = computed(() => {
  const raw = String(state.printer.model || '').trim()
  if (!raw) return 'M1'
  return MODEL_NAMES[raw.toLowerCase()] || raw
})

/* ------------------------------------------------------------------ toast */

export function showToast(text, type = 'info', ttl = 3200) {
  const id = ++toastSeq
  state.toasts.push({ id, text: String(text), type })
  if (state.toasts.length > MAX_TOASTS) state.toasts.shift()
  window.setTimeout(() => {
    const index = state.toasts.findIndex((toast) => toast.id === id)
    if (index >= 0) state.toasts.splice(index, 1)
  }, ttl)
}

export function dismissToast(id) {
  const index = state.toasts.findIndex((toast) => toast.id === id)
  if (index >= 0) state.toasts.splice(index, 1)
}

/* --------------------------------------------------------------- selection */

export function selectItem(id) {
  state.selectedId = id || null
}

export function clearSelection() {
  state.selectedId = null
}

/* ---------------------------------------------------------------- document */

export function addItem(type) {
  if (!TYPE_LABELS[type]) return null
  const item = createItem(type, state.doc)
  autoPlaceItem(item, state.doc)
  state.doc.items.push(item)
  state.selectedId = item.id
  showToast(`已添加${TYPE_LABELS[type]}元素`, 'success', 1600)
  return item
}

export function removeItem(id) {
  const index = state.doc.items.findIndex((item) => item.id === id)
  if (index < 0) return
  state.doc.items.splice(index, 1)
  if (state.selectedId === id) state.selectedId = null
}

export function duplicateItem(id) {
  const index = state.doc.items.findIndex((item) => item.id === id)
  if (index < 0) return
  const copy = JSON.parse(JSON.stringify(state.doc.items[index]))
  copy.id = newId()
  copy.x_mm = round1((Number(copy.x_mm) || 0) + 1)
  copy.y_mm = round1((Number(copy.y_mm) || 0) + 1)
  normalizeItemPosition(copy, state.doc)
  state.doc.items.splice(index + 1, 0, copy)
  state.selectedId = copy.id
  showToast('已复制元素', 'success', 1600)
}

export function updateItem(id, patch) {
  const item = state.doc.items.find((entry) => entry.id === id)
  if (!item) return null
  const touchesPosition = 'x_mm' in patch || 'y_mm' in patch
  Object.assign(item, patch)
  clampItemFields(item)
  if (touchesPosition) normalizeItemPosition(item, state.doc)
  return item
}

/** 拖拽时直接写入 mm 坐标（已在画布侧做 1mm 吸附与边界约束） */
export function moveItem(id, x_mm, y_mm) {
  const item = state.doc.items.find((entry) => entry.id === id)
  if (!item) return
  item.x_mm = round1(x_mm)
  item.y_mm = round1(y_mm)
}

export function nudgeSelected(dxMm, dyMm) {
  const item = selectedItem.value
  if (!item) return
  updateItem(item.id, {
    x_mm: round1((Number(item.x_mm) || 0) + dxMm),
    y_mm: round1((Number(item.y_mm) || 0) + dyMm),
  })
}

const ALIGN_LABELS = {
  left: '左对齐',
  center: '水平居中',
  right: '右对齐',
  top: '上对齐',
  middle: '垂直居中',
  bottom: '下对齐',
}

export function alignSelected(mode) {
  const item = selectedItem.value
  if (!item || !ALIGN_LABELS[mode]) return
  const box = itemBoxMm(item, state.doc)
  const width = state.doc.width_mm
  const height = state.doc.height_mm
  const patch = {}
  if (mode === 'left') patch.x_mm = 0
  if (mode === 'center') patch.x_mm = round1(Math.max(0, (width - box.w_mm) / 2))
  if (mode === 'right') patch.x_mm = round1(Math.max(0, width - box.w_mm))
  if (mode === 'top') patch.y_mm = 0
  if (mode === 'middle') patch.y_mm = round1(Math.max(0, (height - box.h_mm) / 2))
  if (mode === 'bottom') patch.y_mm = round1(Math.max(0, height - box.h_mm))
  updateItem(item.id, patch)
  showToast(`已${ALIGN_LABELS[mode]}`, 'info', 1500)
}

export function updateDoc(patch) {
  const doc = state.doc
  if ('name' in patch) doc.name = String(patch.name ?? '')
  if ('width_mm' in patch) {
    doc.width_mm = clamp(round1(Number(patch.width_mm) || 40), LIMITS.width_mm[0], LIMITS.width_mm[1])
  }
  if ('height_mm' in patch) {
    doc.height_mm = clamp(round1(Number(patch.height_mm) || 30), LIMITS.height_mm[0], LIMITS.height_mm[1])
  }
  if ('border' in patch) doc.border = !!patch.border
  if ('offset_x_mm' in patch) doc.offset_x_mm = snapOffsetX(patch.offset_x_mm)
  if ('offset_y_mm' in patch) doc.offset_y_mm = snapOffsetY(patch.offset_y_mm)
  if ('trim_bottom_mm' in patch) doc.trim_bottom_mm = snapTrimBottom(patch.trim_bottom_mm)
  if ('align' in patch) {
    doc.align = ['left', 'center', 'right'].includes(patch.align) ? patch.align : 'left'
  }
  if ('density' in patch) {
    doc.density = DENSITIES.includes(Number(patch.density)) ? Number(patch.density) : 75
  }
  for (const item of doc.items) normalizeItemPosition(item, doc)
  if (state.selectedId && !doc.items.some((item) => item.id === state.selectedId)) {
    state.selectedId = null
  }
  return doc
}

export function setPreset(preset) {
  if (!preset || preset.width_mm == null || preset.height_mm == null) return
  // 纸仓固定 50mm：偏移 = 50 - 纸宽（40mm 纸 → 10mm，50mm 纸 → 0），可再手动微调
  const autoOffset = snapOffsetX(50 - preset.width_mm)
  updateDoc({ width_mm: preset.width_mm, height_mm: preset.height_mm, offset_x_mm: autoOffset })
  showToast(
    `已切换 ${preset.width_mm} × ${preset.height_mm} mm，纸卷偏移 ${autoOffset} mm`,
    'info',
    2400,
  )
}

export function newDoc() {
  state.doc = createDoc()
  state.selectedId = null
  showToast('已新建空白标签（40 × 30 mm）')
}

export function applyTemplate(name) {
  state.doc = templateDoc(name)
  state.selectedId = null
  showToast(`已载入模板：${state.doc.name}`, 'success', 2400)
}

export function loadSpec(raw, options = {}) {
  const doc = fromSpec(raw)
  if (options.name) doc.name = String(options.name)
  state.doc = doc
  state.selectedId = null
  hydrateImageItems(doc)
  showToast(`已打开标签「${doc.name}」（${doc.width_mm} × ${doc.height_mm} mm）`, 'success', 2600)
  return doc
}

/* -------------------------------------------------------------- JSON files */

function safeFileName(name) {
  return String(name || 'label')
    .replace(/[\\/:*?"<>|\s]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 60) || 'label'
}

export function exportSpecJson() {
  return JSON.stringify(spec.value, null, 2)
}

export function saveJson() {
  const blob = new Blob([exportSpecJson()], { type: 'application/json;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${safeFileName(state.doc.name)}.json`
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 3000)
  showToast('已导出 spec JSON（浏览器下载）', 'success')
}

export async function openJsonFile(file) {
  if (!file) return
  try {
    const text = await file.text()
    const parsed = JSON.parse(text)
    const name = String(file.name || '').replace(/\.json$/i, '')
    loadSpec(parsed, { name })
  } catch (error) {
    showToast(`导入失败：${error.message || error}`, 'error', 6000)
  }
}

export async function copySpecJson() {
  const text = exportSpecJson()
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
    } else {
      const textarea = document.createElement('textarea')
      textarea.value = text
      textarea.style.position = 'fixed'
      textarea.style.opacity = '0'
      document.body.appendChild(textarea)
      textarea.select()
      document.execCommand('copy')
      textarea.remove()
    }
    showToast('spec JSON 已复制到剪贴板', 'success', 2200)
  } catch (error) {
    showToast(`复制失败：${error.message || error}`, 'error')
  }
}

/* ----------------------------------------------------------------- backend */

const HARD_STATE_KEYS = new Set(['error', 'failed', 'fault', 'offline', 'disconnected', 'power_off'])
const SOFT_STATE_KEYS = new Set([
  'no_paper',
  'paper_out',
  'paper_missing',
  'cover_open',
  'lid_open',
  'learn_failed',
  'position_failed',
  'calibrate_failed',
  'low_battery',
  'battery_low',
  'overheat',
])

// 后端 /api/status 的 status 是中文摘要（正常 / 缺纸,开盖 / 离线 …），做一次语义映射
const STATE_ALIASES = {
  正常: 'ready',
  就绪: 'ready',
  在线: 'ready',
  待机: 'ready',
  打印中: 'printing',
  忙碌: 'busy',
  离线: 'offline',
  无响应: 'offline',
  未连接: 'offline',
  断开: 'offline',
  缺纸: 'no_paper',
  开盖: 'cover_open',
  上盖未关: 'cover_open',
  过热: 'overheat',
  过冷: 'error',
  低电: 'low_battery',
  低压: 'error',
  定位失败: 'learn_failed',
  错误: 'error',
  异常: 'error',
  失败: 'failed',
}

const MODEL_NAMES = {
  'hprt-m1': 'M1',
}

function firstString(...values) {
  for (const value of values) {
    if (typeof value === 'string' && value.trim()) return value.trim()
  }
  return ''
}

/**
 * 兼容多种后端返回结构：
 *   { ok, model, addr, status: '正常'|'缺纸,开盖'|'离线', errors, error }   ← 本项目的 Python 后端
 *   { data: { printer: { model, state }, errors: [...] } }
 * 未知结构也不会崩，原始 JSON 保存在 raw 里供 tooltip 展示。
 */
export function normalizeStatus(raw) {
  const root = raw && typeof raw === 'object' ? raw : {}
  const info =
    root.data && typeof root.data === 'object' && !Array.isArray(root.data) ? root.data : root
  const printer = info.printer && typeof info.printer === 'object' ? info.printer : {}

  const modelId =
    firstString(info.model, info.printer_model, info.device, printer.model, printer.name) || 'hprt-m1'
  const model = MODEL_NAMES[modelId.toLowerCase()] || modelId

  const rawState = firstString(info.status, info.state, info.printer_state, printer.state, printer.status)
  const stateKey = STATE_ALIASES[rawState] || rawState.toLowerCase().replace(/\s+/g, '_')

  const errors = []
  const pushError = (value) => {
    if (!value) return
    if (Array.isArray(value)) {
      value.forEach(pushError)
      return
    }
    if (typeof value === 'object') {
      const message = value.msg || value.message || value.detail
      if (message) pushError(message)
      return
    }
    const text = String(value).trim()
    if (text) errors.push(text)
  }
  pushError(info.errors)
  pushError(info.error)
  pushError(info.detail)
  pushError(printer.error)
  if (info.paper === false) errors.push('缺纸')
  if (info.cover === false || info.cover_open === true) errors.push('上盖未关')

  const offline =
    info.ok === false ||
    stateKey === 'offline' ||
    /离线|无响应|未连接|断开/.test(rawState)
  const online = !offline && info.online !== false && info.connected !== false
  const uniqueErrors = [...new Set(errors)]

  let tone = 'ok'
  if (!online) tone = 'err'
  else if (HARD_STATE_KEYS.has(stateKey) || /失败|异常|错误/.test(rawState)) tone = 'err'
  else if (SOFT_STATE_KEYS.has(stateKey) || /缺纸|开盖|低电|过热|低压/.test(rawState) || uniqueErrors.length > 0) {
    tone = 'warn'
  }

  const stateLabel = rawState ? rawState.replace(/,/g, '、') : online ? '正常' : '离线'

  return {
    model,
    modelId,
    stateKey,
    stateLabel,
    online,
    errors: uniqueErrors,
    tone,
    raw: root,
  }
}

function markBackendOffline(error) {
  if (error && error.network) {
    state.backend.phase = 'offline'
    state.backend.info = null
    state.backend.error = error.message || '无法连接后端'
    state.backend.lastCheck = Date.now()
  }
}

/* -------------------------------------------------------- 打印机扫描 / 连接 */

function normalizeDevice(raw, m1Address) {
  if (!raw || typeof raw !== 'object') return null
  const address = String(raw.address || raw.addr || '').trim().toUpperCase()
  if (!address) return null
  const name = String(raw.name || raw.address || address).trim() || address
  const isM1 = !!(
    raw.is_m1 ||
    raw.isM1 ||
    (m1Address && String(m1Address).toUpperCase() === address) ||
    /\bm1\b|hprt/i.test(name)
  )
  return { address, name, isM1 }
}

function applyDevicePayload(payload, scanned) {
  const dialog = state.printerDialog
  if (!payload || typeof payload !== 'object') {
    dialog.error = '设备接口返回了无法识别的数据'
    return
  }
  if (payload.ok === false) {
    dialog.error = payload.error || '读取蓝牙设备失败'
    if (scanned) dialog.devices = []
    return
  }
  const m1 = payload.m1 && typeof payload.m1 === 'object' ? payload.m1 : null
  const m1Address = m1 ? m1.address : ''
  dialog.m1 = m1
  const list = []
  const seen = new Set()
  const add = (raw) => {
    const device = normalizeDevice(raw, m1Address)
    if (!device || seen.has(device.address)) return
    seen.add(device.address)
    list.push(device)
  }
  if (m1) add(m1)
  if (Array.isArray(payload.devices)) payload.devices.forEach(add)
  if (Array.isArray(payload.known)) payload.known.forEach(add)
  list.sort((a, b) => Number(b.isM1) - Number(a.isM1) || a.name.localeCompare(b.name))
  dialog.devices = list
  dialog.hint = typeof payload.hint === 'string' ? payload.hint : ''
  dialog.scanned = scanned || dialog.scanned
  dialog.lastScan = scanned ? Date.now() : dialog.lastScan
}

export function openPrinterDialog() {
  state.printerDialog.open = true
  if (state.printerDialog.devices.length === 0) {
    loadKnownDevices()
  }
}

export function closePrinterDialog() {
  state.printerDialog.open = false
  state.printerDialog.connectError = null
}

/** GET /api/devices — 已知设备（快，不主动扫描） */
export async function loadKnownDevices() {
  const dialog = state.printerDialog
  dialog.loading = true
  dialog.error = ''
  try {
    const payload = await api.fetchDevices()
    applyDevicePayload(payload, false)
  } catch (error) {
    dialog.error = error.message || String(error)
    markBackendOffline(error)
  } finally {
    dialog.loading = false
  }
}

/** GET /api/scan?seconds=8 — 主动扫描蓝牙设备 */
export async function scanPrinters(seconds = 8) {
  const dialog = state.printerDialog
  if (dialog.scanning) return
  dialog.scanning = true
  dialog.error = ''
  try {
    const payload = await api.scanDevices(seconds)
    applyDevicePayload(payload, true)
    const m1 = dialog.m1
    showToast(
      m1 ? `已找到打印机：${m1.name || m1.address}` : '扫描完成，未发现打印机',
      m1 ? 'success' : 'warn',
      3500,
    )
  } catch (error) {
    dialog.error = error.message || String(error)
    markBackendOffline(error)
  } finally {
    dialog.scanning = false
  }
}

/** POST /api/connect — 连接并握手；失败时把后端 error 文案展示出来 */
export async function connectPrinter(target) {
  const dialog = state.printerDialog
  const rawAddress =
    typeof target === 'string' ? target : target?.address || target?.addr || ''
  const addr = String(rawAddress).trim().toUpperCase()
  if (!addr || dialog.connectingAddr) return null

  const device = typeof target === 'object' && target ? target : null
  const isCurrentPrinter =
    !!state.printer.addr && state.printer.addr.toUpperCase() === addr
  const previousPhase = state.printer.phase
  dialog.connectingAddr = addr
  dialog.connectError = null
  // 只有首次连接 / 重连当前打印机时才把全局状态切到「连接中」，
  // 尝试连接另一台设备失败时不会影响当前已连接打印机。
  if (!state.printer.addr || isCurrentPrinter) {
    state.printer.phase = 'connecting'
    state.printer.error = ''
  }

  try {
    const result = await api.connectDevice(addr)
    if (!result || result.ok === false) {
      throw new Error(
        firstString(
          result?.error,
          Array.isArray(result?.errors) ? result.errors.join('、') : '',
          result?.message,
        ) || '连接失败（打印机可能被占用）',
      )
    }
    state.printer.addr = addr
    state.printer.name = firstString(result.name, device?.name, state.printer.name) || addr
    state.printer.isM1 = !!device?.isM1 || /\bm1\b|hprt/i.test(state.printer.name)
    state.printer.model = firstString(result.model, state.printer.model) || 'M1'
    state.printer.statusLabel = firstString(result.status, result.message) || '已连接'
    state.printer.error = firstString(
      Array.isArray(result.errors) ? result.errors.join('、') : result.error,
    )
    state.printer.phase = 'connected'
    state.printer.lastConnectedAt = Date.now()
    state.backend.phase = 'online'
    state.backend.info = normalizeStatus({
      ...result,
      model: result.model || 'hprt-m1',
    })
    state.backend.error = ''
    persistPrinter(state.printer)
    showToast(
      `已连接打印机 ${state.printer.name}（${state.printer.statusLabel}）`,
      'success',
      3500,
    )
    window.setTimeout(() => {
      if (state.printer.addr === addr) refreshStatus()
    }, 1200)
    return state.printer
  } catch (error) {
    const message = error.message || String(error)
    const stillCurrentPrinter =
      !!state.printer.addr && state.printer.addr.toUpperCase() === addr
    dialog.connectError = { addr, message }
    if (!state.printer.addr || stillCurrentPrinter) {
      state.printer.phase = 'error'
      state.printer.error = message
    } else {
      state.printer.phase = previousPhase
    }
    showToast(`连接失败：${message}`, 'error', 7000)
    markBackendOffline(error)
    return null
  } finally {
    dialog.connectingAddr = ''
  }
}

export function setPrinterParam(key, value) {
  if (!(key in PRINTER_PARAM_DEFAULTS)) return
  state.printerParams[key] = snapParamValue(value, PRINTER_PARAM_DEFAULTS[key])
}

/**
 * POST /api/printer/params — 写入设备参数（207 起始位置 / 208 定位后走纸）。
 * 只写入选中的打印机；成功后持久化到 localStorage（不参与打印请求）。
 */
export async function writePrinterParams() {
  const params = state.printerParams
  if (params.writing) return false
  if (!state.printer.addr) {
    showToast('请先选择打印机', 'warn', 4500)
    openPrinterDialog()
    return false
  }
  params.writing = true
  params.error = ''
  params.written = []
  try {
    const result = await api.writePrinterParams({
      addr: state.printer.addr,
      start_pos_mm: params.start_pos_mm,
      feed_after_pos_mm: params.feed_after_pos_mm,
    })
    if (!result || result.ok === false) {
      throw new Error(
        firstString(result?.error, result?.message) || '写入打印机参数失败',
      )
    }
    params.written = Array.isArray(result.written) ? result.written.map(String) : []
    params.lastWrittenAt = Date.now()
    persistPrinterSettings(params)
    showToast('已写入打印机', 'success', 3500)
    window.setTimeout(() => refreshStatus(), 1800)
    return true
  } catch (error) {
    params.error = error.message || String(error)
    showToast(`写入失败：${params.error}`, 'error', 7000)
    markBackendOffline(error)
    return false
  } finally {
    params.writing = false
  }
}

export function clearPrinterSelection() {
  state.printer.addr = ''
  state.printer.name = ''
  state.printer.isM1 = false
  state.printer.model = ''
  state.printer.phase = 'idle'
  state.printer.statusLabel = ''
  state.printer.error = ''
  state.printerDialog.connectError = null
  persistPrinter(state.printer)
  showToast('已清除打印机选择', 'info', 2200)
}

/** 打印/预览前必须已选择打印机 */
function ensurePrinterSelected() {
  if (state.printer.addr) return true
  showToast('请先选择打印机', 'warn', 4500)
  openPrinterDialog()
  return false
}

export async function refreshStatus() {
  if (state.backend.phase === 'checking') return
  if (state.printer.phase === 'connecting') return
  state.backend.phase = 'checking'
  try {
    const raw = await api.fetchStatus(state.printer.addr || undefined)
    state.backend.info = normalizeStatus(raw)
    state.backend.error = ''
    state.backend.phase = 'online'
    if (state.printer.addr && state.backend.info) {
      const info = state.backend.info
      state.printer.phase = info.online ? 'connected' : 'error'
      if (info.stateLabel) state.printer.statusLabel = info.stateLabel
      state.printer.error = info.errors?.length ? info.errors.join('、') : ''
    }
  } catch (error) {
    state.backend.info = null
    state.backend.phase = 'offline'
    state.backend.error = error.message || '无法连接后端'
    if (state.printer.addr) {
      state.printer.phase = 'error'
      state.printer.error = state.backend.error
    }
  } finally {
    state.backend.lastCheck = Date.now()
  }
}

export async function learnPaper() {
  if (state.backend.learning) return
  state.backend.learning = true
  try {
    await api.learnPaper(state.printer.addr || undefined)
    showToast('已发送测纸指令，请观察打印机是否走纸并重新定位', 'success', 5000)
    window.setTimeout(() => refreshStatus(), 2500)
  } catch (error) {
    showToast(`测纸失败：${error.message || error}`, 'error', 6000)
    markBackendOffline(error)
  } finally {
    state.backend.learning = false
  }
}

/** 轮询打印机状态；返回停止函数。页面隐藏时跳过请求。 */
export function startStatusPolling(interval = 15000) {
  let timer = null
  let stopped = false
  const tick = async () => {
    if (stopped) return
    if (document.visibilityState !== 'hidden') {
      try {
        await refreshStatus()
      } catch {
        /* refreshStatus 内部已兜底 */
      }
    }
    if (!stopped) timer = window.setTimeout(tick, interval)
  }
  timer = window.setTimeout(tick, interval)
  return () => {
    stopped = true
    if (timer) window.clearTimeout(timer)
  }
}

/* ----------------------------------------------------------------- preview */

function releasePreviewUrl() {
  const url = state.preview.url
  if (url && url.startsWith('blob:')) URL.revokeObjectURL(url)
}

export async function openPreview() {
  if (!ensurePrinterSelected()) return
  state.preview.open = true
  await refreshPreview()
}

export async function refreshPreview() {
  const preview = state.preview
  preview.loading = true
  preview.error = ''
  releasePreviewUrl()
  preview.url = ''
  preview.spec = { addr: state.printer.addr, ...toSpec(state.doc) }
  try {
    const result = await api.fetchPreview(preview.spec)
    preview.url = result.url
  } catch (error) {
    preview.error = error.message || String(error)
    markBackendOffline(error)
  } finally {
    preview.loading = false
  }
}

export function closePreview() {
  releasePreviewUrl()
  state.preview.open = false
  state.preview.loading = false
  state.preview.url = ''
  state.preview.error = ''
}

/* --------------------------------------------------- 图片元素（参与排版） */

/** 从 spec/JSON 载入的图片元素：异步探测原图尺寸，用于「高度留空=等比」 */
function hydrateImageItem(item) {
  if (!item || item.type !== 'image' || !item.src) return
  if (Number(item.naturalWidth) > 0 && Number(item.naturalHeight) > 0) return
  const image = new Image()
  image.onload = () => {
    item.naturalWidth = image.naturalWidth || image.width || 0
    item.naturalHeight = image.naturalHeight || image.height || 0
    normalizeItemPosition(item, state.doc)
  }
  image.src = item.src
}

function hydrateImageItems(doc) {
  for (const item of doc.items || []) {
    if (item.type === 'image') hydrateImageItem(item)
  }
}

/**
 * 添加「图片」元素：PNG/JPG/SVG → dataURL（SVG 栅格化、大图压缩）→ 画布元素。
 * 默认放在可打印区内（y=4），宽 20mm，高度留空（按原图比例）。
 */
export async function addImageElementFromFile(file) {
  if (!file || state.imageProcessing) return null
  state.imageProcessing = true
  try {
    const prepared = await prepareImageElementFile(file)
    const item = createItem('image', state.doc, {
      src: prepared.dataUrl,
      width_mm: 20,
      height_mm: null,
      align: 'left',
      naturalWidth: prepared.width,
      naturalHeight: prepared.height,
      x_mm: 2,
      y_mm: PRINT_ZONE_TOP_MM,
    })
    normalizeItemPosition(item, state.doc)
    state.doc.items.push(item)
    state.selectedId = item.id
    showToast(
      `已添加图片元素（${prepared.width}×${prepared.height}px）`,
      'success',
      3000,
    )
    return item
  } catch (error) {
    showToast(`图片处理失败：${error.message || error}`, 'error', 6500)
    return null
  } finally {
    state.imageProcessing = false
  }
}

/* ------------------------------------------------------------------- print */

export async function printLabel() {
  if (state.printing) return
  if (!ensurePrinterSelected()) return
  state.printing = true
  try {
    const payload = {
      addr: state.printer.addr,
      ...toSpec(state.doc),
      copies: Math.max(1, Math.min(99, Number(state.copies) || 1)),
    }
    const result = await api.printLabel(payload)
    const message =
      result && typeof result === 'object'
        ? firstString(result.message, result.msg, result.detail, result.raw)
        : typeof result === 'string'
          ? result
          : ''
    const copies = Math.max(1, Math.min(99, Number(state.copies) || 1))
    showToast(
      `打印任务已发送${copies > 1 ? `（${copies} 份）` : ''}${message ? `：${message}` : ''}`,
      'success',
      4000,
    )
    if (state.backend.phase !== 'online') refreshStatus()
  } catch (error) {
    showToast(`打印失败：${error.message || error}`, 'error', 7000)
    markBackendOffline(error)
  } finally {
    state.printing = false
  }
}
