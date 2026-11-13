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

const MAX_TOASTS = 4
let toastSeq = 0

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
  printing: false,
  toasts: [],
})

export const selectedItem = computed(
  () => state.doc.items.find((item) => item.id === state.selectedId) || null,
)

export const spec = computed(() => toSpec(state.doc))
export const specJson = computed(() => JSON.stringify(spec.value, null, 2))

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

export async function refreshStatus() {
  if (state.backend.phase === 'checking') return
  state.backend.phase = 'checking'
  try {
    const raw = await api.fetchStatus()
    state.backend.info = normalizeStatus(raw)
    state.backend.error = ''
    state.backend.phase = 'online'
  } catch (error) {
    state.backend.info = null
    state.backend.phase = 'offline'
    state.backend.error = error.message || '无法连接后端'
  } finally {
    state.backend.lastCheck = Date.now()
  }
}

export async function learnPaper() {
  if (state.backend.learning) return
  state.backend.learning = true
  try {
    await api.learnPaper()
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
  state.preview.open = true
  await refreshPreview()
}

export async function refreshPreview() {
  const preview = state.preview
  preview.loading = true
  preview.error = ''
  releasePreviewUrl()
  preview.url = ''
  preview.spec = toSpec(state.doc)
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

/* ------------------------------------------------------------------- print */

export async function printLabel() {
  if (state.printing) return
  state.printing = true
  try {
    const payload = { ...toSpec(state.doc), copies: Math.max(1, Math.min(99, Number(state.copies) || 1)) }
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
