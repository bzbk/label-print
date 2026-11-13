/**
 * 单位与文字排版计算。
 *
 * 设计基准：M1 标签打印机 / 203dpi ≈ 8 dot/mm。
 * 因此画布内部统一用「dot」作为布局单位，1mm = 8dot；
 * 元素坐标、尺寸全部以 mm 存储与显示，渲染时乘以 DOT_PER_MM * zoom。
 */

export const DOT_PER_MM = 8
export const MM_PER_DOT = 1 / DOT_PER_MM

/** 画布与测量共用同一套字体，保证自动换行结果与浏览器渲染一致（CJK 优先）。 */
export const FONT_STACK =
  'system-ui, -apple-system, "Noto Sans CJK SC", "Source Han Sans SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif'

export function clamp(value, min, max) {
  const n = Number(value)
  if (!Number.isFinite(n)) return min
  return Math.min(Math.max(n, min), max)
}

export function round1(value) {
  const n = Number(value)
  if (!Number.isFinite(n)) return 0
  return Math.round(n * 10) / 10
}

export function mmToDot(mm) {
  return Number(mm) * DOT_PER_MM
}

export function dotToMm(dot) {
  return Number(dot) / DOT_PER_MM
}

let measureContext = null

function getMeasureContext() {
  if (measureContext || typeof document === 'undefined') return measureContext
  const canvas = document.createElement('canvas')
  measureContext = canvas.getContext('2d')
  return measureContext
}

function fontSpec(sizePx, bold) {
  return `${bold ? '700' : '400'} ${sizePx}px ${FONT_STACK}`
}

/** 测量单行文字宽度，单位：dot（字号按 dot 计） */
export function measureTextWidthDots(text, sizePx, bold = false) {
  const value = String(text ?? '')
  const ctx = getMeasureContext()
  if (!ctx) return [...value].length * sizePx * 0.58
  ctx.font = fontSpec(sizePx, bold)
  return ctx.measureText(value).width
}

// CJK 单字 / 连续非空白串 / 空白串
const TOKEN_RE =
  /[\u2e80-\u9fff\u3040-\u30ff\uac00-\ud7af\uf900-\ufaff\uff00-\uffef\u3000-\u303f]|[^\s\u2e80-\u9fff\u3040-\u30ff\uac00-\ud7af\uf900-\ufaff\uff00-\uffef\u3000-\u303f]+|\s+/g

/**
 * 按给定的最大宽度（dot）折行，结果与画布渲染逐行一致。
 * 中文按字断行，西文优先按词断行，超长单词按字符兜底。
 */
export function wrapTextLines(text, maxWidthDots, sizePx, bold = false) {
  const ctx = getMeasureContext()
  if (ctx) ctx.font = fontSpec(sizePx, bold)
  const measure = (value) => {
    if (!value) return 0
    if (!ctx) return [...value].length * sizePx * 0.58
    return ctx.measureText(value).width
  }
  const maxWidth = Math.max(1, maxWidthDots)
  const lines = []
  const paragraphs = String(text ?? '').split('\n')

  for (const paragraph of paragraphs) {
    if (paragraph === '') {
      lines.push('')
      continue
    }
    const tokens = paragraph.match(TOKEN_RE) || [paragraph]
    let current = ''
    for (const token of tokens) {
      if (current === '' && /^\s+$/.test(token)) continue
      if (current !== '' && measure(current + token) > maxWidth) {
        lines.push(current.replace(/\s+$/, ''))
        current = ''
        if (/^\s+$/.test(token)) continue
      }
      const tokenWidth = measure(token)
      if (tokenWidth <= maxWidth) {
        current += token
        continue
      }
      // 单个 token 超宽（长 URL / 无空格长串）：逐字符硬切
      for (const char of [...token]) {
        if (current !== '' && measure(current + char) > maxWidth) {
          lines.push(current)
          current = ''
        }
        current += char
      }
    }
    lines.push(current)
  }

  return lines.length > 0 ? lines : ['']
}
