<script setup>
import { computed } from 'vue'
import { DOT_PER_MM, FONT_STACK } from '../units'
import {
  SYMBOLOGIES,
  itemBoxMm,
  itemOverflow,
  textLayout,
} from '../label'
import AppIcon from './AppIcon.vue'

const props = defineProps({
  item: { type: Object, required: true },
  doc: { type: Object, required: true },
  zoom: { type: Number, required: true },
  selected: { type: Boolean, default: false },
  index: { type: Number, default: 0 },
})

defineEmits(['pointerdown'])

function mmPx(mm) {
  return Number(mm) * DOT_PER_MM * props.zoom
}

const box = computed(() => itemBoxMm(props.item, props.doc))
const overflow = computed(() => itemOverflow(props.item, props.doc))

const rootStyle = computed(() => ({
  left: `${mmPx(props.item.x_mm)}px`,
  top: `${mmPx(props.item.y_mm)}px`,
  width: `${mmPx(box.value.w_mm)}px`,
  height: `${mmPx(box.value.h_mm)}px`,
  zIndex: props.selected ? 60 : props.index + 1,
}))

const overflowWarning = computed(() => {
  const parts = []
  if (overflow.value.right) parts.push('右侧')
  if (overflow.value.bottom) parts.push('底部')
  return parts.length > 0 ? `元素超出标签${parts.join('、')}边界，打印时会被裁切` : ''
})

/* ---------------------------------------------------------------- 文本 */

const textLayoutResult = computed(() => {
  if (props.item.type !== 'text') return null
  return box.value.layout || textLayout(props.item, props.doc)
})

const textLines = computed(() => textLayoutResult.value?.lines || [''])

const textStyle = computed(() => {
  const layout = textLayoutResult.value
  if (!layout) return null
  return {
    fontFamily: FONT_STACK,
    fontSize: `${layout.size * props.zoom}px`,
    lineHeight: `${layout.lineHeightDots * props.zoom}px`,
    textAlign: props.item.align,
    fontWeight: props.item.bold ? '700' : '400',
    color: props.item.invert ? '#ffffff' : '#111111',
    background: props.item.invert ? '#111111' : 'transparent',
  }
})

/* --------------------------------------------------------------- 条码 */

function hashString(text) {
  let hash = 2166136261
  for (const char of String(text ?? '')) {
    hash ^= char.codePointAt(0)
    hash = Math.imul(hash, 16777619) >>> 0
  }
  return hash >>> 0
}

/**
 * 装饰性条码示意：按数据做确定性伪随机条纹。
 * 明确不是可扫描的合法编码——真实条码由后端 zint 生成。
 */
const barcodeBars = computed(() => {
  if (props.item.type !== 'barcode') return []
  let seed = hashString(`${props.item.data}|${props.item.symbology}`) || 1
  const random = () => {
    seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0
    return seed / 4294967296
  }
  const bars = []
  const total = 1000
  const module = total / 96
  let x = module * 2
  while (x < total - module * 2 && bars.length < 160) {
    const width = module * (1 + Math.floor(random() * 3))
    if (x + width > total - module) break
    bars.push({ x: Number(x.toFixed(2)), width: Number(width.toFixed(2)) })
    x += width + module * (1 + Math.floor(random() * 2))
  }
  return bars
})

const symbologyLabel = computed(() => {
  const entry = SYMBOLOGIES.find((candidate) => candidate.value === props.item.symbology)
  return entry ? entry.label : String(props.item.symbology || '')
})

const barsStyle = computed(() => ({ height: `${mmPx(props.item.height_mm || 12)}px` }))

const captionStyle = computed(() => ({
  fontSize: `${mmPx(2.1)}px`,
  lineHeight: `${mmPx(3.4)}px`,
  height: `${mmPx(3.4)}px`,
}))

const metaStyle = computed(() => ({
  fontSize: `${Math.max(7, mmPx(1.75))}px`,
  lineHeight: `${Math.max(9, mmPx(2.4))}px`,
  height: `${Math.max(9, mmPx(2.4))}px`,
}))

const tagStyle = computed(() => ({
  fontSize: `${Math.max(7, mmPx(1.5))}px`,
  lineHeight: `${Math.max(9, mmPx(2))}px`,
}))

const elementTitle = computed(() => {
  if (props.item.type === 'barcode') return `${symbologyLabel.value} 示意 · ${props.item.data}`
  if (props.item.type === 'qr') return `二维码 示意 · ${props.item.data}`
  if (props.item.type === 'text') return props.item.text
  return ''
})

/* ----------------------------------------------------------- 二维码 */

const qrModules = computed(() => {
  if (props.item.type !== 'qr') return []
  const size = 21
  const modules = []
  const add = (x, y) => modules.push({ x, y })

  const finder = (originX, originY) => {
    for (let y = 0; y < 7; y += 1) {
      for (let x = 0; x < 7; x += 1) {
        const onBorder = x === 0 || x === 6 || y === 0 || y === 6
        const inCore = x >= 2 && x <= 4 && y >= 2 && y <= 4
        if (onBorder || inCore) add(originX + x, originY + y)
      }
    }
  }
  finder(0, 0)
  finder(14, 0)
  finder(0, 14)

  for (let i = 8; i <= 12; i += 1) {
    if (i % 2 === 0) {
      add(i, 6)
      add(6, i)
    }
  }

  let seed = hashString(props.item.data) || 7
  const random = () => {
    seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0
    return seed / 4294967296
  }
  const inFinder = (x, y) => (x < 8 && y < 8) || (x > 12 && y < 8) || (x < 8 && y > 12)
  for (let y = 0; y < size; y += 1) {
    for (let x = 0; x < size; x += 1) {
      if (inFinder(x, y)) continue
      if (random() > 0.52) add(x, y)
    }
  }
  return modules.map((module, index) => ({ ...module, key: index }))
})
</script>

<template>
  <div
    class="label-element"
    :class="[`label-element--${item.type}`, { 'is-selected': selected, 'is-inverted': item.invert }]"
    :style="rootStyle"
    :title="overflowWarning || elementTitle"
    @pointerdown="$emit('pointerdown', $event)"
  >
    <!-- 文本：逐行渲染，行宽/折行严格按 mm 计算，超出标签的部分可见地被裁切 -->
    <div v-if="item.type === 'text'" class="label-text" :style="textStyle">
      <div v-for="(line, lineIndex) in textLines" :key="lineIndex" class="label-text-line">
        {{ line || '\u00a0' }}
      </div>
    </div>

    <!-- 条码：SVG 示意占位（非可扫描编码，真实条码由后端 zint 生成） -->
    <div v-else-if="item.type === 'barcode'" class="barcode-box">
      <span class="element-tag" :style="tagStyle">{{ symbologyLabel }}</span>
      <svg
        class="barcode-bars"
        :style="barsStyle"
        viewBox="0 0 1000 100"
        preserveAspectRatio="none"
        aria-hidden="true"
      >
        <rect
          v-for="(bar, barIndex) in barcodeBars"
          :key="barIndex"
          :x="bar.x"
          y="0"
          :width="bar.width"
          height="100"
          fill="#111111"
        />
      </svg>
      <div v-if="item.show_text" class="barcode-caption" :style="captionStyle">
        {{ item.data }}
      </div>
      <div v-else class="element-meta" :style="metaStyle">
        示意 · {{ symbologyLabel }} · {{ item.data }}
      </div>
    </div>

    <!-- 二维码：SVG 示意占位（非可扫描编码，真实二维码由后端 zint 生成） -->
    <div v-else-if="item.type === 'qr'" class="qr-box">
      <span class="element-tag" :style="tagStyle">QR</span>
      <svg class="qr-svg" viewBox="-2 -2 25 25" preserveAspectRatio="xMidYMid meet" aria-hidden="true">
        <rect x="-2" y="-2" width="25" height="25" fill="#ffffff" />
        <rect
          v-for="module in qrModules"
          :key="module.key"
          :x="module.x"
          :y="module.y"
          width="1"
          height="1"
          fill="#111111"
        />
      </svg>
      <div class="element-meta" :style="metaStyle">示意 · {{ item.data }}</div>
    </div>

    <span v-if="overflowWarning" class="element-warn" aria-hidden="true">
      <AppIcon name="warning" :size="12" />
    </span>
  </div>
</template>
