<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import AppIcon from './AppIcon.vue'
import LabelElement from './LabelElement.vue'
import { LIMITS, itemBoxMm, printableZoneMm } from '../label'
import { DOT_PER_MM, clamp, round1 } from '../units'
import { clearSelection, markHistory, selectItem, state } from '../stores/store'

const doc = computed(() => state.doc)
const zoom = computed(() => state.zoom)

const scrollEl = ref(null)
const dragState = ref(null)
const resizeState = ref(null)
let resizeTimer = null

/** 多选整体包围盒（仅显示，不参与拖拽命中） */
const groupSelectionBox = computed(() => {
  if (state.selectedIds.length < 2) return null
  const selected = state.doc.items.filter((item) => state.selectedIds.includes(item.id))
  if (!selected.length) return null
  let minX = Infinity
  let minY = Infinity
  let maxX = -Infinity
  let maxY = -Infinity
  for (const item of selected) {
    const box = itemBoxMm(item, state.doc)
    const x = Number(item.x_mm) || 0
    const y = Number(item.y_mm) || 0
    minX = Math.min(minX, x)
    minY = Math.min(minY, y)
    maxX = Math.max(maxX, x + box.w_mm)
    maxY = Math.max(maxY, y + box.h_mm)
  }
  const px = (mm) => mm * DOT_PER_MM * zoom.value
  return {
    // label-region 本身已包含纸卷偏移，组包围盒坐标不再叠加 offset
    left: `${px(minX)}px`,
    top: `${px(minY)}px`,
    width: `${px(maxX - minX)}px`,
    height: `${px(maxY - minY)}px`,
    count: selected.length,
  }
})

/** 纸卷偏移：40mm 卷纸在 50mm 纸仓中相对打印头原点右移 10mm；内容从偏移线之后开始 */
const offsetMm = computed(() => Number(doc.value.offset_x_mm) || 0)
const offsetY = computed(() => Number(doc.value.offset_y_mm) || 0)
const absOffsetY = computed(() => Math.abs(offsetY.value))
/** 底部少走：打印时不下发的底部区域（固件不支持反向走纸，用少走等效回退） */
const trimMm = computed(() => Number(doc.value.trim_bottom_mm) || 0)
const trimPx = computed(() => trimMm.value * DOT_PER_MM * zoom.value)
const printHeightMm = computed(() => Math.max(0, round1(doc.value.height_mm - trimMm.value)))

/** 可配置的不可打印边距（上/下/右）：仅标注与软提醒，不限制拖拽 */
const printZone = computed(() => printableZoneMm(doc.value))
const topBandPx = computed(() => printZone.value.topMarginMm * DOT_PER_MM * zoom.value)
const bottomBandPx = computed(() => printZone.value.bottomMarginMm * DOT_PER_MM * zoom.value)
const rightBandPx = computed(() => printZone.value.rightMarginMm * DOT_PER_MM * zoom.value)
const printableTopPx = computed(() => printZone.value.topMm * DOT_PER_MM * zoom.value)
const printableWidthPx = computed(() => printZone.value.widthMm * DOT_PER_MM * zoom.value)
const printableHeightPx = computed(() => printZone.value.heightMm * DOT_PER_MM * zoom.value)
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

  // Shift / Ctrl / Cmd + 点击：多选切换（不启动拖拽）
  const additive = event.shiftKey || event.metaKey || event.ctrlKey
  if (additive) {
    selectItem(item.id, true)
    return
  }

  if (!state.selectedIds.includes(item.id)) selectItem(item.id)
  const ids = state.selectedIds.slice()
  const targets = state.doc.items
    .filter((entry) => ids.includes(entry.id))
    .map((entry) => ({
      id: entry.id,
      startX: Number(entry.x_mm) || 0,
      startY: Number(entry.y_mm) || 0,
      box: itemBoxMm(entry, state.doc),
    }))
  if (!targets.length) return

  let minX = Infinity
  let minY = Infinity
  let maxX = -Infinity
  let maxY = -Infinity
  for (const target of targets) {
    minX = Math.min(minX, target.startX)
    minY = Math.min(minY, target.startY)
    maxX = Math.max(maxX, target.startX + target.box.w_mm)
    maxY = Math.max(maxY, target.startY + target.box.h_mm)
  }

  dragState.value = {
    targets,
    startClientX: event.clientX,
    startClientY: event.clientY,
    sessionId: Date.now(),
    minDeltaX: -minX,
    maxDeltaX: state.doc.width_mm - maxX,
    minDeltaY: -minY,
    maxDeltaY: state.doc.height_mm - maxY,
    moved: false,
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
  // 默认吸附 1mm 网格；按住 Shift 时精细到 0.1mm
  const step = event.shiftKey ? 0.1 : 1
  let dx = Math.round(dxMm / step) * step
  let dy = Math.round(dyMm / step) * step
  // 整组约束在标签范围内（若元素原始就超界，则允许反向移动）
  dx = clamp(dx, Math.min(drag.minDeltaX, drag.maxDeltaX), Math.max(drag.minDeltaX, drag.maxDeltaX))
  dy = clamp(dy, Math.min(drag.minDeltaY, drag.maxDeltaY), Math.max(drag.minDeltaY, drag.maxDeltaY))
  if (!drag.moved) {
    drag.moved = true
    markHistory(`drag:${drag.sessionId}`)
  }
  for (const target of drag.targets) {
    const entry = state.doc.items.find((item) => item.id === target.id)
    if (!entry) continue
    entry.x_mm = round1(target.startX + dx)
    entry.y_mm = round1(target.startY + dy)
  }
}

function stopDrag() {
  window.removeEventListener('pointermove', onPointerMove)
  window.removeEventListener('pointerup', stopDrag)
  window.removeEventListener('pointercancel', stopDrag)
  dragState.value = null
}

/* 条码拖右边缘改宽度 / 二维码拖右下角改尺寸（实时更新 mm，防抖重新请求服务端渲染） */
function onResizeStart(event, item) {
  if (event.button !== 0) return
  event.preventDefault()
  event.stopPropagation()
  selectItem(item.id)
  resizeState.value = {
    id: item.id,
    startClientX: event.clientX,
    startWidth: Number(item.width_mm) || 20,
    startSize: Number(item.size_mm) || 14,
    sessionId: Date.now(),
    moved: false,
  }
  window.addEventListener('pointermove', onResizeMove)
  window.addEventListener('pointerup', stopResize, { once: true })
  window.addEventListener('pointercancel', stopResize, { once: true })
}

function onResizeMove(event) {
  const resize = resizeState.value
  if (!resize) return
  const item = state.doc.items.find((entry) => entry.id === resize.id)
  if (!item) return
  const deltaMm = (event.clientX - resize.startClientX) / (DOT_PER_MM * state.zoom)
  const step = event.shiftKey ? 0.5 : 1
  if (item.type === 'barcode') {
    const next = Math.round((resize.startWidth + deltaMm) / step) * step
    item.width_mm = clamp(next, LIMITS.barcodeWidth[0], LIMITS.barcodeWidth[1])
  } else if (item.type === 'qr') {
    const next = Math.round((resize.startSize + deltaMm) / step) * step
    item.size_mm = clamp(next, LIMITS.qrSize[0], LIMITS.qrSize[1])
  }
  if (!resize.moved) {
    resize.moved = true
    markHistory(`resize:${resize.sessionId}`)
  }
}

function stopResize() {
  window.removeEventListener('pointermove', onResizeMove)
  window.removeEventListener('pointerup', stopResize)
  window.removeEventListener('pointercancel', stopResize)
  resizeState.value = null
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
  stopResize()
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

              <!-- 硬件不可打印区（浅红斜纹）与可打印区域细线框（软提示，不限制拖拽） -->
              <div
                class="no-print-band no-print-band--top"
                :style="{ height: `${topBandPx}px` }"
                :title="`不可打印边距：顶部 ${printZone.topMarginMm} mm`"
              >
                <span class="no-print-band-tag">不可打印（顶部 {{ printZone.topMarginMm }} mm）</span>
              </div>
              <div
                class="no-print-band no-print-band--bottom"
                :style="{ height: `${bottomBandPx}px` }"
                :title="`不可打印边距：底部 ${printZone.bottomMarginMm} mm`"
              >
                <span v-if="bottomBandPx >= 12" class="no-print-band-tag no-print-band-tag--bottom">
                  不可打印（底部 {{ printZone.bottomMarginMm }} mm）
                </span>
              </div>
              <div
                class="no-print-band no-print-band--right"
                :style="{ width: `${rightBandPx}px` }"
                :title="`不可打印边距：右侧 ${printZone.rightMarginMm} mm`"
              >
                <span v-if="rightBandPx >= 12" class="no-print-band-tag no-print-band-tag--right">
                  不可打印（右 {{ printZone.rightMarginMm }} mm）
                </span>
              </div>
              <div
                class="printable-zone"
                :style="{
                  top: `${printableTopPx}px`,
                  width: `${printableWidthPx}px`,
                  height: `${printableHeightPx}px`,
                }"
                :title="`可打印区域：x 0–${printZone.rightMm} mm，y ${printZone.topMm}–${printZone.bottomMm} mm（${printZone.widthMm} × ${printZone.heightMm} mm）`"
              >
                <span class="printable-zone-tag">
                  可打印区域 {{ printZone.widthMm }} × {{ printZone.heightMm }} mm
                </span>
              </div>

              <LabelElement
                v-for="(item, index) in doc.items"
                :key="item.id"
                :item="item"
                :doc="doc"
                :zoom="zoom"
                :index="index"
                :selected="state.selectedIds.includes(item.id)"
                @pointerdown="onItemPointerDown($event, item)"
                @resize-start="onResizeStart"
              />

              <!-- 垂直微调：只在可能被裁切的一侧画一条虚线提示，不改变网格/元素坐标 -->
              <!-- 底部少走：打印时不下发 trim 区域（预览仍是完整图像，这里只做提示） -->
              <div
                v-if="trimMm > 0"
                class="trim-zone"
                :style="{ height: `${trimPx}px` }"
                :title="`底部少走 ${trimMm} mm：打印时只下发 ${printHeightMm} mm 的行（固件不支持反向走纸，等效回退）；该区域不打印`"
              >
                <span class="trim-tag">底部少走 {{ trimMm }} mm · 不打印</span>
              </div>

              <div
                v-if="offsetY !== 0"
                class="v-offset-hint"
                :class="offsetY > 0 ? 'is-down' : 'is-up'"
                :title="`内容整体${offsetY > 0 ? '下移' : '上移'} ${absOffsetY} mm，打印时${offsetY > 0 ? '底部' : '顶部'}可能被裁切（位图高度不变）`"
              >
                <span class="v-offset-tag">
                  内容{{ offsetY > 0 ? '下移' : '上移' }} {{ absOffsetY }} mm · {{ offsetY > 0 ? '底部' : '顶部' }}可能被裁切
                </span>
              </div>

              <div v-if="groupSelectionBox" class="selection-group-box" :style="groupSelectionBox">
                <span class="selection-group-tag">已选 {{ groupSelectionBox.count }} 个元素</span>
              </div>

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
            <span v-if="offsetY !== 0">· 垂直微调 {{ offsetY }} mm</span>
            <span v-if="trimMm > 0">· 底部少走 {{ trimMm }} mm（不打印）</span>
            <span>
              · 可打印区 {{ printZone.widthMm }} × {{ printZone.heightMm }} mm（上
              {{ printZone.topMarginMm }}/下 {{ printZone.bottomMarginMm }}/右
              {{ printZone.rightMarginMm }}）
            </span>
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
