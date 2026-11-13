/**
 * 导入图片 → 打印位图准备。
 *
 * 目标：把用户选择的 PNG / JPG / SVG 转成「标签区域」的 PNG（宽 = 标签宽 mm × 8，
 * 高 = 标签高 mm × 8；白底、等比缩放居中），供 POST /api/print/image 上传。
 * 服务端会再次按 width_mm/height_mm 等比缩放并应用纸卷偏移 offset_x_mm。
 *
 * 预览 canvas 则按「最终位图尺寸」绘制（宽度 = 偏移 + 标签宽），让用户看到包含
 * 偏移留白的最终效果。SVG 在浏览器端栅格化（服务端不解析 SVG）。
 */

export const IMAGE_DOT_PER_MM = 8

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

function canvasToBlob(canvas) {
  return new Promise((resolve, reject) => {
    canvas.toBlob(
      (blob) => (blob ? resolve(blob) : reject(new Error('图片栅格化失败'))),
      'image/png',
    )
  })
}

/**
 * @param {File} file
 * @param {{ widthMm: number, heightMm: number, offsetMm: number }} options 当前标签参数
 * @returns {Promise<{kind:string, previewDataUrl:string, uploadBlob:Blob, uploadName:string,
 *   labelW:number, labelH:number, offsetPx:number, fullW:number, naturalW:number, naturalH:number}>}
 */
export async function prepareImageForPrint(file, options = {}) {
  if (!(file instanceof Blob)) throw new Error('没有可用的图片文件')
  const name = String(file.name || '')
  if (name && !IMAGE_EXT_RE.test(name) && !String(file.type || '').startsWith('image/')) {
    throw new Error('仅支持 PNG / JPG / JPEG / SVG 图片')
  }
  const kind = imageFileKind(file)
  if (!kind) throw new Error('仅支持 PNG / JPG / JPEG / SVG 图片')

  const labelW = Math.max(1, Math.round(Number(options.widthMm) * IMAGE_DOT_PER_MM))
  const labelH = Math.max(1, Math.round(Number(options.heightMm) * IMAGE_DOT_PER_MM))
  const offsetPx = Math.max(0, Math.round(Number(options.offsetMm) * IMAGE_DOT_PER_MM))
  const fullW = offsetPx + labelW

  const dataUrl = await readAsDataUrl(file)
  const source = await loadImage(dataUrl)
  let naturalW = source.naturalWidth || source.width || 0
  let naturalH = source.naturalHeight || source.height || 0
  if (!naturalW || !naturalH) {
    naturalW = labelW
    naturalH = labelH
  }

  // 等比缩放居中（与后端 /api/print/image 的 fit 逻辑一致）
  const ratio = Math.min(labelW / naturalW, labelH / naturalH)
  const drawW = Math.max(1, Math.round(naturalW * ratio))
  const drawH = Math.max(1, Math.round(naturalH * ratio))
  const drawX = offsetPx + Math.floor((labelW - drawW) / 2)
  const drawY = Math.floor((labelH - drawH) / 2)

  const fullCanvas = document.createElement('canvas')
  fullCanvas.width = fullW
  fullCanvas.height = labelH
  const fullCtx = fullCanvas.getContext('2d')
  fullCtx.fillStyle = '#ffffff'
  fullCtx.fillRect(0, 0, fullW, labelH)
  fullCtx.imageSmoothingEnabled = true
  fullCtx.imageSmoothingQuality = 'high'
  fullCtx.drawImage(source, drawX, drawY, drawW, drawH)

  let previewDataUrl
  try {
    previewDataUrl = fullCanvas.toDataURL('image/png')
  } catch {
    throw new Error('图片包含跨域资源，无法生成预览（请换成本地图片或内嵌 SVG）')
  }

  // 上传体：仅标签区域（服务端会再应用 offset_x_mm）
  const labelCanvas = document.createElement('canvas')
  labelCanvas.width = labelW
  labelCanvas.height = labelH
  const labelCtx = labelCanvas.getContext('2d')
  labelCtx.fillStyle = '#ffffff'
  labelCtx.fillRect(0, 0, labelW, labelH)
  labelCtx.drawImage(fullCanvas, offsetPx, 0, labelW, labelH, 0, 0, labelW, labelH)

  const uploadBlob = await canvasToBlob(labelCanvas)
  const baseName = name ? name.replace(/\.[^.]+$/, '') : 'image'

  return {
    kind,
    previewDataUrl,
    uploadBlob,
    uploadName: `${baseName}-${labelW}x${labelH}.png`,
    labelW,
    labelH,
    offsetPx,
    fullW,
    naturalW,
    naturalH,
  }
}
