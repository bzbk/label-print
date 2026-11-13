/**
 * 背端（打印机服务）API 封装。
 *
 * 默认后端：http://127.0.0.1:17600
 * 可通过环境变量 VITE_LABEL_API_BASE 覆盖（例如从别的机器访问）。
 *
 * 所有网络错误都会被包装成 ApiError，并带 network 标记，
 * 让 UI 能区分「后端没启动」和「后端返回业务错误」，绝不白屏。
 */

const DEFAULT_BASE = 'http://127.0.0.1:17600'

export const API_BASE = String(import.meta.env?.VITE_LABEL_API_BASE || DEFAULT_BASE).replace(/\/+$/, '')

const DEFAULT_TIMEOUT = 9000

export class ApiError extends Error {
  constructor(message, { status = 0, network = false, detail = '' } = {}) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.network = network
    this.detail = detail
  }
}

function timeoutSignal(ms) {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), ms)
  return {
    signal: controller.signal,
    cancel: () => clearTimeout(timer),
  }
}

async function readErrorDetail(response) {
  try {
    const contentType = response.headers.get('content-type') || ''
    if (contentType.includes('json')) {
      const data = await response.json()
      if (typeof data === 'string') return data
      const detail = data?.detail ?? data?.error ?? data?.message ?? data?.msg
      if (Array.isArray(detail)) {
        return detail
          .map((entry) => (typeof entry === 'string' ? entry : entry?.msg || JSON.stringify(entry)))
          .join('；')
      }
      if (detail) return String(detail)
      return JSON.stringify(data).slice(0, 300)
    }
    const text = await response.text()
    return text.slice(0, 300)
  } catch {
    return ''
  }
}

async function request(path, { method = 'GET', body, timeout = DEFAULT_TIMEOUT, responseType = 'json' } = {}) {
  const { signal, cancel } = timeoutSignal(timeout)
  try {
    const response = await fetch(API_BASE + path, {
      method,
      headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
    })

    if (!response.ok) {
      const detail = await readErrorDetail(response)
      throw new ApiError(`后端返回 ${response.status}${detail ? `：${detail}` : ''}`, {
        status: response.status,
        detail,
      })
    }

    if (responseType === 'blob') {
      return { response, blob: await response.blob() }
    }

    const contentType = response.headers.get('content-type') || ''
    if (contentType.includes('json')) return response.json()

    const text = await response.text()
    if (!text) return null
    try {
      return JSON.parse(text)
    } catch {
      return { raw: text }
    }
  } catch (error) {
    if (error instanceof ApiError) throw error
    if (error?.name === 'AbortError') {
      throw new ApiError(
        `后端响应超时（${Math.round(timeout / 1000)} 秒）。请确认标签打印服务正在运行：${API_BASE}`,
        { network: true },
      )
    }
    throw new ApiError(
      `无法连接后端服务（${API_BASE}）。请先启动打印机后端，或检查 17600 端口是否被占用。`,
      { network: true },
    )
  } finally {
    cancel()
  }
}

/** GET /api/status — 打印机状态；传入 addr 查询指定打印机 */
export function fetchStatus(addr) {
  const query = addr ? `?addr=${encodeURIComponent(addr)}` : ''
  return request(`/api/status${query}`, { timeout: 6000 })
}

/** GET /api/devices — 已知蓝牙设备（快，不主动扫描） */
export function fetchDevices() {
  return request('/api/devices', { timeout: 12000 })
}

/** GET /api/scan?seconds=8 — 主动扫描蓝牙设备（阻塞约 seconds 秒） */
export function scanDevices(seconds = 8) {
  const value = Math.max(3, Math.min(20, Math.round(Number(seconds) || 8)))
  return request(`/api/scan?seconds=${value}`, { timeout: 30000 })
}

/** POST /api/connect — 连接指定地址并握手；返回 {ok, addr, model, status, value, errors} */
export function connectDevice(addr) {
  return request('/api/connect', {
    method: 'POST',
    body: { addr: String(addr || '') },
    timeout: 30000,
  })
}

/**
 * POST /api/preview — 生成 PNG 预览。
 * 兼容两种后端实现：直接返回 image/png 二进制，或返回 JSON（image/base64/png/data 字段）。
 * 返回 { url }，url 可直接作为 <img src>。
 */
export async function fetchPreview(spec) {
  const { response, blob } = await request('/api/preview', {
    method: 'POST',
    body: spec,
    responseType: 'blob',
    timeout: 20000,
  })
  const contentType = (response.headers.get('content-type') || '').toLowerCase()

  if (contentType.startsWith('image/') || blob.type.startsWith('image/')) {
    const typed = blob.type ? blob : new Blob([blob], { type: 'image/png' })
    return { kind: 'binary', url: URL.createObjectURL(typed) }
  }

  // 部分实现会以 JSON 返回 base64
  const text = await blob.text()
  let data = null
  try {
    data = JSON.parse(text)
  } catch {
    data = null
  }
  const base64 = data && (data.image || data.png || data.base64 || data.data)
  if (typeof base64 === 'string' && base64.length > 0) {
    return {
      kind: 'base64',
      url: base64.startsWith('data:') ? base64 : `data:image/png;base64,${base64}`,
    }
  }
  throw new ApiError('预览接口返回了无法识别的数据（期望 PNG 二进制，或含 image/base64 字段的 JSON）')
}

/** POST /api/element/render — 服务端实时渲染单个条码/二维码 PNG（与打印同一渲染器） */
export async function renderElementImage(payload) {
  const { response, blob } = await request('/api/element/render', {
    method: 'POST',
    body: payload,
    responseType: 'blob',
    timeout: 9000,
  })
  const contentType = (response.headers.get('content-type') || '').toLowerCase()
  if (!contentType.startsWith('image/') && !blob.type.startsWith('image/')) {
    throw new ApiError('元素渲染接口未返回图片')
  }
  return blob
}

/** POST /api/print — 打印。body = spec + copies（copies 为前端扩展字段） */
export function printLabel(spec) {
  return request('/api/print', { method: 'POST', body: spec, timeout: 25000 })
}

/** POST /api/printer/params — 写入打印机参数（207 起始位置 / 208 定位后走纸） */
export function writePrinterParams(payload) {
  return request('/api/printer/params', {
    method: 'POST',
    body: payload,
    timeout: 30000,
  })
}

/** POST /api/learn — 测纸 / 定位；addr 指定目标打印机 */
export function learnPaper(addr) {
  return request('/api/learn', {
    method: 'POST',
    body: addr ? { addr } : {},
    timeout: 20000,
  })
}

export const api = {
  base: API_BASE,
  fetchStatus,
  fetchDevices,
  scanDevices,
  connectDevice,
  fetchPreview,
  renderElementImage,
  printLabel,
  learnPaper,
  writePrinterParams,
}
