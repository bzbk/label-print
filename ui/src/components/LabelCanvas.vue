<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import AppIcon from './AppIcon.vue'
import LabelElement from './LabelElement.vue'
import { itemBoxMm } from '../label'
import { DOT_PER_MM, clamp, round1 } from '../units'
import { clearSelection, selectItem, state } from '../stores/store'

const doc = computed(() => state.doc)
const zoom = computed(() => state.zoom)

const scrollEl = ref(null)
const dragState = ref(null)
let resizeTimer = null

/** 纸卷偏移：40mm 卷纸在 50mm 纸仓中相对打印头原点右移 10mm；内容从偏移线之后开始 */
const offsetMm = computed(() => Number(doc.value.offset_x_mm) || 0)
const totalMm = computed(() => round1(doc.value.width_mm + offsetMm.value))
const labelDotW = computed(() => doc.value.width_mm * DOT_PER_MM)
const totalDotW = computed(() => (doc.value.width_mm + offsetMm.value) * DOT_PER_MM)
const dotH = computed(() => doc.value.height_mm * DOT_PER_MM)

const offsetPx = computed(() => offsetMm.value * DOT_PER_MM * zoom.value)
const labelPx = computed(() => labelDotW.value * zoom.value)
const sheetW = computed(() => totalDotW.value * zoom.value)
const sheetH = computed(() => dotH.value * zoom.value)

function buildTicks(lengthMm, stepPx) {
  const ticks = []
  const whole = Math.floor(lengthMm)
  for (let mm = 0; mm <= whole; mm += 1) {
    ticks.push({
      key: `t${mm}`,
      pos: mm * stepPx,
      major: mm % 5 === 0,
      label: mm > 0 && mm % 5 === 0 ? String(mm) : '',
    })
  }
  if (Math.abs(lengthMm - whole) > 0.001) {
    ticks.push({
      key: 't-end',
      pos: lengthMm * stepPx,
      major: true,
      label: String(round1(lengthMm)),
    })
  }
  return ticks
}

// 标尺按「打印头坐标」标注：0 = 打印头原点，纸卷从 offset 处开始
const hTicks = computed(() => buildTicks(totalMm.value, DOT_PER_MM * zoom.value))
const vTicks = computed(() => buildTicks(doc.value.height_mm, DOT_PER_MM * zoom.value))

const gridStyle = computed(() => {
  const minor = DOT_PER_MM * zoom.value
  if (minor < 3) return { backgroundImage: 'none' }
  const major = minor * 5
  const fine = 'rgba(30, 55, 100, 0.10)'
  const strong = 'rgba(30, 55, 100, 0.20)'
  return {
    backgroundImage: [
      `linear-gradient(to right, ${strong} 1px, transparent 1px)`,
      `linear-gradient(to bottom, ${strong} 1px, transparent 1px)`,
      `linear-gradient(to right, ${fine} 1px, transparent 1px)`,
      `linear-gradient(to bottom, ${fine} 1px, transparent 1px)`,
    ].join(', '),
    backgroundSize: [
      `${major}px ${major}px`,
      `${major}px ${major}px`,
      `${minor}px ${minor}px`,
      `${minor}px ${minor}px`,
    ].join(', '),
  }
})

const isEmpty = computed(() => doc.value.items.length === 0)
const showOriginTag = computed(() => offsetPx.value > 78)
const showGutterNote = computed(() => offsetPx.value > 62)

/* ----------------------------------------------------------- 缩放 / 适应 */

function fitZoom() {
  const element = scrollEl.value
  if (!element) return
  const availableWidth = Math.max(160, element.clientWidth - 132)
  const availableHeight = Math.max(160, element.clientHeight - 132)
  const next = Math.min(availableWidth / totalDotW.value, availableHeight / dotH.value)
  state.zoom = clamp(Math.round(next * 20) / 20, 0.5, 6)
}

function zoomBy(factor) {
  state.zoom = clamp(Math.round(state.zoom * factor * 100) / 100, 0.5, 6)
}

function onResize() {
  window.clearTimeout(resizeTimer)
  resizeTimer = window.setTimeout(fitZoom, 160)
}

/* --------------------------------------------------------------- 拖拽 */

function onItemPointerDown(event, item) {
  if (event.button !== 0) return
  event.preventDefault()
  event.stopPropagation()
  selectItem(item.id)
  const box = itemBoxMm(item, state.doc)
  dragState.value = {
    id: item.id,
    startClientX: event.clientX,
    startClientY: event.clientY,
    startX: Number(item.x_mm) || 0,
    startY: Number(item.y_mm) || 0,
    maxX: Math.max(0, round1(state.doc.width_mm - Math.min(box.w_mm, state.doc.width_mm))),
    maxY: Math.max(0, round1(state.doc.height_mm - Math.min(box.h_mm, state.doc.height_mm))),
  }
  window.addEventListener('pointermove', onPointerMove)
  window.addEventListener('pointerup', stopDrag, { once: true })
  window.addEventListener('pointercancel', stopDrag, { once: true })
}

function onPointerMove(event) {
  const drag = dragState.value
  if (!drag) return
  const dxMm = (event.clientX - drag.startClientX) / (DOT_PER_MM * state.zoom)
  const dyMm = (event.clientY - drag.startClientY) / (DOT_PER_MM * state.zoom)
  const item = state.doc.items.find((entry) => entry.id === drag.id)
  if (!item) return
  // 默认吸附 1mm 网格；按住 Shift 时精细到 0.1mm
  const step = event.shiftKey ? 0.1 : 1
  const nextX = Math.round((drag.startX + dxMm) / step) * step
  const nextY = Math.round((drag.startY + dyMm) / step) * step
  item.x_mm = clamp(round1(nextX), 0, drag.maxX)
  item.y_mm = clamp(round1(nextY), 0, drag.maxY)
}

function stopDrag() {
  window.removeEventListener('pointermove', onPointerMove)
  window.removeEventListener('pointerup', stopDrag)
  window.removeEventListener('pointercancel', stopDrag)
  dragState.value = null
}

function onSheetPointerDown(event) {
  if (event.button !== 0) return
  // 元素内部会 stopPropagation，这里只会收到画布空白 / 偏移留白区域的点击
  clearSelection()
}

function onStagePointerDown(event) {
  if (event.target === event.currentTarget || event.target.classList?.contains('canvas-frame')) {
    clearSelection()
  }
}

watch(() => [doc.value.width_mm, doc.value.height_mm, doc.value.offset_x_mm], fitZoom)

onMounted(() => {
  fitZoom()
  window.addEventListener('resize', onResize)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  window.clearTimeout(resizeTimer)
  stopDrag()
})
</script>

<template>
  <section class="canvas-stage" @pointerdown="onStagePointerDown">
    <div ref="scrollEl" class="canvas-scroll">
      <div class="canvas-frame">
        <div class="ruler-corner">mm</div>

        <div class="ruler ruler-h" :style="{ width: `${sheetW}px` }" aria-hidden="true">
          <div
            v-for="tick in hTicks"
            :key="tick.key"
            class="tick"
            :class="{ 'is-major': tick.major }"
            :style="{ left: `${tick.pos}px` }"
          >
            <span v-if="tick.label" class="tick-label">{{ tick.label }}</span>
          </div>
        </div>

        <div class="ruler ruler-v" :style="{ height: `${sheetH}px` }" aria-hidden="true">
          <div
            v-for="tick in vTicks"
            :key="tick.key"
            class="tick"
            :class="{ 'is-major': tick.major }"
            :style="{ top: `${tick.pos}px` }"
          >
            <span v-if="tick.label" class="tick-label">{{ tick.label }}</span>
          </div>
        </div>

        <div class="sheet-column">
          <div
            class="label-sheet"
            :style="{ width: `${sheetW}px`, height: `${sheetH}px` }"
            @pointerdown="onSheetPointerDown"
          >
            <!-- 打印头原点 x=0：竖向虚线 -->
            <div class="print-origin" aria-hidden="true">
              <span v-if="showOriginTag" class="origin-tag">打印头原点 x=0</span>
            </div>

            <!-- 纸卷偏移留白（不可打印区）：浅灰斜纹，宽度 = offset_x_mm -->
            <div
              v-if="offsetPx > 0"
              class="roll-gutter"
              :style="{ width: `${offsetPx}px` }"
              :title="`纸卷偏移 ${offsetMm} mm：40mm 卷纸在 50mm 纸仓中右移 10mm，50mm 卷纸为 0`"
            >
              <span v-if="showGutterNote" class="gutter-note">纸卷偏移 {{ offsetMm }} mm · 不打印</span>
            </div>

            <!-- 标签区域：元素、网格、边框都只在这里（border 不会画到偏移留白上） -->
            <div
              class="label-region"
              :style="{ left: `${offsetPx}px`, width: `${labelPx}px`, height: `${sheetH}px` }"
            >
              <div class="sheet-grid" :style="gridStyle"></div>

              <LabelElement
                v-for="(item, index) in doc.items"
                :key="item.id"
                :item="item"
                :doc="doc"
                :zoom="zoom"
                :index="index"
                :selected="item.id === state.selectedId"
                @pointerdown="onItemPointerDown($event, item)"
              />

              <div
                v-if="doc.border"
                class="label-border"
                :style="{ borderWidth: `${Math.max(1, zoom * 0.4)}px` }"
              ></div>

              <div v-if="isEmpty" class="sheet-empty">
                <strong>空白标签</strong>
                <span>从左侧元素库添加内容，或载入模板</span>
              </div>
            </div>
          </div>

          <div class="sheet-caption">
            {{ doc.width_mm }} × {{ doc.height_mm }} mm
            <span>·</span>
            纸卷偏移 {{ offsetMm }} mm
            <span>·</span>
            位图 {{ totalDotW }} × {{ dotH }} dot @ 203dpi
            <span>·</span>
            打印头坐标 0–{{ totalMm }} mm
          </div>
        </div>
      </div>
    </div>

    <div class="canvas-tools">
      <button class="zoom-btn" title="缩小" @click="zoomBy(1 / 1.25)">
        <AppIcon name="zoomOut" :size="14" />
      </button>
      <span class="zoom-value">{{ Math.round(zoom * 100) }}%</span>
      <button class="zoom-btn" title="放大" @click="zoomBy(1.25)">
        <AppIcon name="zoomIn" :size="14" />
      </button>
      <button class="zoom-btn zoom-btn--fit" title="适应窗口" @click="fitZoom">适应</button>
    </div>
  </section>
</template>
