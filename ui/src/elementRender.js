/**
 * 服务端实时渲染的条码 / 二维码图片缓存。
 *
 * 画布不再用示意 SVG：按元素参数请求 POST /api/element/render，
 * 拿到真实 zint PNG（203dpi，宽度 ≈ width_mm × 8 px），转 blob URL 缓存。
 * - 防抖 300ms：连续输入时只发最后一次请求；
 * - key 覆盖全部渲染参数（type/symbology/data/width/height/show_text/size）；
 * - 失败保留错误状态，调用方回退示意图，不白屏；
 * - 元素占位尺寸优先使用真实位图像素 / 8 换算 mm（画布与打印 1:1）。
 */

import { reactive } from 'vue'
import { round1 } from './units'
import { renderElementImage } from './api'

const MAX_CACHE = 120
const DEFAULT_DEBOUNCE = 300

export const renderCache = reactive(new Map())
const pending = new Map()
const timers = new Map()

function numberOr(value, fallback) {
  const parsed = Number(value)
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback
}

/** 条码渲染参数（也是接口 body） */
export function barcodeRenderEntry(item) {
  if (!item || item.type !== 'barcode') return null
  return {
    type: 'barcode',
    symbology: item.symbology || 'code128',
    data: String(item.data ?? ''),
    width_mm: round1(numberOr(item.width_mm, 20)),
    height_mm: round1(numberOr(item.height_mm, 12)),
    show_text: item.show_text !== false,
  }
}

/** 二维码渲染参数（也是接口 body） */
export function qrRenderEntry(item) {
  if (!item || item.type !== 'qr') return null
  return {
    type: 'qr',
    data: String(item.data ?? ''),
    size_mm: round1(numberOr(item.size_mm, 14)),
  }
}

export function elementRenderEntry(item) {
  return barcodeRenderEntry(item) || qrRenderEntry(item)
}

/** 缓存 key：包含全部渲染参数，参数任一变化都会重新请求 */
export function elementRenderKey(entry) {
  if (!entry) return ''
  if (entry.type === 'barcode') {
    return [
      'barcode',
      entry.symbology,
      entry.data,
      entry.width_mm,
      entry.height_mm,
      entry.show_text ? 1 : 0,
    ].join('|')
  }
  return ['qr', entry.data, entry.size_mm].join('|')
}

export function getEntryRender(entry) {
  if (!entry) return null
  return renderCache.get(elementRenderKey(entry)) || null
}

export function getItemRender(item) {
  return getEntryRender(elementRenderEntry(item))
}

/** 已渲染位图的真实尺寸（px → mm，203dpi 8 dot/mm） */
export function itemRenderedSizeMm(item) {
  const rendered = getItemRender(item)
  if (!rendered || rendered.status !== 'ready' || !rendered.widthPx || !rendered.heightPx) {
    return null
  }
  return {
    w_mm: rendered.widthPx / 8,
    h_mm: rendered.heightPx / 8,
    widthPx: rendered.widthPx,
    heightPx: rendered.heightPx,
  }
}

async function blobSize(blob) {
  if (typeof createImageBitmap === 'function') {
    const bitmap = await createImageBitmap(blob)
    const size = { width: bitmap.width, height: bitmap.height }
    bitmap.close?.()
    return size
  }
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(blob)
    const image = new Image()
    image.onload = () => {
      resolve({ width: image.naturalWidth, height: image.naturalHeight })
      URL.revokeObjectURL(url)
    }
    image.onerror = () => {
      URL.revokeObjectURL(url)
      reject(new Error('渲染位图无法解码'))
    }
    image.src = url
  })
}

function evictOldEntries(keepKey) {
  if (renderCache.size <= MAX_CACHE) return
  for (const [key, value] of renderCache) {
    if (renderCache.size <= MAX_CACHE) break
    if (key === keepKey) continue
    if (value?.url) URL.revokeObjectURL(value.url)
    renderCache.delete(key)
  }
}

async function fetchRender(entry, key) {
  if (pending.has(key)) return
  const task = renderElementImage(entry)
  pending.set(key, task)
  renderCache.set(key, {
    status: 'loading',
    url: '',
    widthPx: 0,
    heightPx: 0,
    error: '',
  })
  try {
    const blob = await task
    const size = await blobSize(blob)
    const url = URL.createObjectURL(blob)
    renderCache.set(key, {
      status: 'ready',
      url,
      widthPx: size.width,
      heightPx: size.height,
      error: '',
    })
    evictOldEntries(key)
  } catch (error) {
    renderCache.set(key, {
      status: 'error',
      url: '',
      widthPx: 0,
      heightPx: 0,
      error: error?.message || '服务端渲染失败',
    })
  } finally {
    pending.delete(key)
  }
}

/**
 * 请求渲染（自动防抖）。initial=true 时立即请求（首次挂载不闪占位）；
 * 参数变化使用 300ms 防抖。
 */
export function scheduleElementRender(entry, { delay = DEFAULT_DEBOUNCE, channel = null } = {}) {
  if (!entry) return
  const key = elementRenderKey(entry)
  if (!key) return
  const cached = renderCache.get(key)
  if (cached?.status === 'ready' || pending.has(key)) return

  // 同一元素（channel=item.id）的连续修改共用同一个防抖计时器：
  // 输入到最后一次参数后 300ms 才发一次请求，避免每个字符各请求一次。
  const timerKey = channel || key
  const existingTimer = timers.get(timerKey)
  if (existingTimer) clearTimeout(existingTimer)
  const run = () => {
    timers.delete(timerKey)
    const current = renderCache.get(key)
    if (current?.status === 'ready' || pending.has(key)) return
    fetchRender(entry, key)
  }
  if (delay <= 0) {
    run()
    return
  }
  timers.set(timerKey, setTimeout(run, delay))
}

/** 供组件销毁时清掉尚未触发的防抖计时器（缓存保留，供再次挂载复用） */
export function cancelScheduledRender(entry, channel = null) {
  const key = channel || elementRenderKey(entry)
  if (!key) return
  const timer = timers.get(key)
  if (timer) {
    clearTimeout(timer)
    timers.delete(key)
  }
}
