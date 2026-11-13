/**
 * 把用户选择的 PNG / JPG / SVG 处理成「图片元素」可用的 data URL。
 *
 * - SVG 在浏览器端栅格化（canvas，白底）；
 * - 统一转 PNG（必要时降级 JPEG），保证 spec 不会过大；
 * - 若源 data URL 超过 2MB，或像素过大，则缩到最长边 800px（大图体积控制）；
 * - 返回最终 data URL 与像素尺寸，供画布按原图比例布局。
 */

export const MAX_DATA_URL_BYTES = 2 * 1024 * 1024
export const IMAGE_MAX_SIDE = 800
const RASTER_MAX_SIDE = 1600

const IMAGE_EXT_RE = /\.(png|jpe?g|svg)$/i

export function imageFileKind(file) {
  const name = String(file?.name || '')
  const type = String(file?.type || '').toLowerCase()
  if (type.includes('svg') || /\.svg$/i.test(name)) return 'svg'
  if (type === 'image/png' || type === 'image/jpeg' || /\.(png|jpe?g)$/i.test(name)) return 'raster'
  return null
}

function readAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result || ''))
    reader.onerror = () => reject(new Error('读取图片文件失败'))
    reader.readAsDataURL(file)
  })
}

function loadImage(url) {
  return new Promise((resolve, reject) => {
    const image = new Image()
    image.onload = () => resolve(image)
    image.onerror = () => reject(new Error('图片加载失败（文件损坏或格式不受支持）'))
    image.src = url
  })
}

/** 白底绘制并等比缩放到目标最长边，返回 canvas */
function renderCanvas(image, naturalWidth, naturalHeight, targetLongest) {
  const scale = Math.min(1, targetLongest / Math.max(naturalWidth, naturalHeight))
  const width = Math.max(1, Math.round(naturalWidth * scale))
  const height = Math.max(1, Math.round(naturalHeight * scale))
  const canvas = document.createElement('canvas')
  canvas.width = width
  canvas.height = height
  const ctx = canvas.getContext('2d')
  ctx.fillStyle = '#ffffff'
  ctx.fillRect(0, 0, width, height)
  ctx.imageSmoothingEnabled = true
  ctx.imageSmoothingQuality = 'high'
  ctx.drawImage(image, 0, 0, width, height)
  return canvas
}

function safeEncode(canvas, mime, quality) {
  try {
    return canvas.toDataURL(mime, quality)
  } catch {
    throw new Error('图片包含跨域资源，无法转换为可打印图片（请换成本地图片或内嵌 SVG）')
  }
}

/**
 * @param {File} file
 * @returns {Promise<{dataUrl:string, width:number, height:number, kind:'svg'|'raster', bytes:number}>}
 */
export async function prepareImageElementFile(file) {
  if (!(file instanceof Blob)) throw new Error('没有可用的图片文件')
  const name = String(file.name || '')
  if (name && !IMAGE_EXT_RE.test(name) && !String(file.type || '').startsWith('image/')) {
    throw new Error('仅支持 PNG / JPG / JPEG / SVG 图片')
  }
  const kind = imageFileKind(file)
  if (!kind) throw new Error('仅支持 PNG / JPG / JPEG / SVG 图片')

  const sourceUrl = await readAsDataUrl(file)
  const image = await loadImage(sourceUrl)
  const naturalWidth = image.naturalWidth || image.width || 0
  const naturalHeight = image.naturalHeight || image.height || 0
  if (!naturalWidth || !naturalHeight) throw new Error('无法读取图片尺寸')

  // 体积控制：源 dataURL > 2MB 时直接按最长边 800px 处理；普通图片也限制在 1600px 内
  let targetLongest =
    kind === 'svg'
      ? IMAGE_MAX_SIDE
      : sourceUrl.length > MAX_DATA_URL_BYTES
        ? IMAGE_MAX_SIDE
        : Math.min(Math.max(naturalWidth, naturalHeight), RASTER_MAX_SIDE)

  let dataUrl = ''
  let width = 0
  let height = 0

  for (let attempt = 0; attempt < 6; attempt += 1) {
    const canvas = renderCanvas(image, naturalWidth, naturalHeight, targetLongest)
    width = canvas.width
    height = canvas.height
    dataUrl = safeEncode(canvas, 'image/png')
    if (dataUrl.length <= MAX_DATA_URL_BYTES) break

    // PNG 过大 → 先用 JPEG 压缩；仍过大则继续缩小
    dataUrl = safeEncode(canvas, 'image/jpeg', 0.86)
    if (dataUrl.length <= MAX_DATA_URL_BYTES) break

    if (targetLongest <= 400) break
    targetLongest = Math.max(400, Math.round(targetLongest * 0.75))
  }

  return {
    dataUrl,
    width,
    height,
    kind,
    bytes: dataUrl.length,
  }
}
