<script setup>
import { computed } from 'vue'
import AppIcon from './AppIcon.vue'
import { DENSITIES, LABEL_PRESETS, SYMBOLOGIES, TYPE_LABELS, itemBoxMm } from '../label'
import {
  copySpecJson,
  duplicateItem,
  removeItem,
  selectedItem,
  setPreset,
  specJson,
  state,
  updateDoc,
  updateItem,
} from '../stores/store'

const item = selectedItem

const presetId = computed(() => {
  const preset = LABEL_PRESETS.find(
    (entry) => entry.width_mm === state.doc.width_mm && entry.height_mm === state.doc.height_mm,
  )
  return preset ? preset.id : 'custom'
})

const typeLabel = computed(() => (item.value ? TYPE_LABELS[item.value.type] : ''))

const box = computed(() => (item.value ? itemBoxMm(item.value, state.doc) : null))

const offsetMm = computed(() => Number(state.doc.offset_x_mm) || 0)
const bitmapMm = computed(() => Math.round((state.doc.width_mm + offsetMm.value) * 10) / 10)
const bitmapDotW = computed(() => Math.round(bitmapMm.value * 8))
const geometryWarning = computed(() =>
  bitmapMm.value > 50
    ? `标签宽 ${state.doc.width_mm} + 纸卷偏移 ${offsetMm.value} = ${bitmapMm.value} mm（${bitmapDotW.value} dot），超过 400px 位图上限，打印可能被拒绝或右缘缺字。`
    : '',
)

function fmt(value) {
  return (Math.round(Number(value) * 10) / 10).toFixed(1)
}

function patchItem(patch) {
  if (item.value) updateItem(item.value.id, patch)
}

function numberValue(event, fallback) {
  const value = Number(event.target.value)
  return Number.isFinite(value) ? value : fallback
}

function onPresetChange(event) {
  const preset = LABEL_PRESETS.find((entry) => entry.id === event.target.value)
  if (preset) setPreset(preset)
}
</script>

<template>
  <aside class="properties">
    <div class="panel-head">
      <h2>属性</h2>
      <span v-if="item" class="type-chip" :class="`type-chip--${item.type}`">{{ typeLabel }}</span>
      <span v-else class="type-chip">标签</span>
    </div>

    <!-- 选中元素 -->
    <section v-if="item" class="prop-section">
      <div class="item-heading">
        <div>
          <strong>{{ typeLabel }}元素</strong>
          <span class="item-id">{{ item.id }}</span>
        </div>
        <div class="item-actions">
          <button class="icon-btn" title="复制元素（Ctrl+D）" @click="duplicateItem(item.id)">
            <AppIcon name="duplicate" :size="15" />
          </button>
          <button class="icon-btn icon-btn--danger" title="删除元素（Delete）" @click="removeItem(item.id)">
            <AppIcon name="trash" :size="15" />
          </button>
        </div>
      </div>

      <div class="field-grid">
        <label class="field">
          X（mm · 相对标签）
          <input
            type="number"
            step="0.1"
            :value="item.x_mm"
            @change="patchItem({ x_mm: numberValue($event, item.x_mm) })"
          />
        </label>
        <label class="field">
          Y（mm）
          <input
            type="number"
            step="0.1"
            :value="item.y_mm"
            @change="patchItem({ y_mm: numberValue($event, item.y_mm) })"
          />
        </label>
      </div>
      <p class="readout">
        占位 {{ box.w_mm }} × {{ box.h_mm }} mm（{{ Math.round(box.w_mm * 8) }} ×
        {{ Math.round(box.h_mm * 8) }} dot）· 打印头坐标 X =
        {{ fmt(offsetMm + item.x_mm) }} mm
      </p>

      <!-- 文本 -->
      <template v-if="item.type === 'text'">
        <label class="field field--stack">
          内容
          <textarea
            rows="3"
            :value="item.text"
            placeholder="输入文字，可换行"
            @input="patchItem({ text: $event.target.value })"
          ></textarea>
        </label>
        <div class="field-grid">
          <label class="field">
            字号（dot px）
            <input
              type="number"
              min="6"
              max="120"
              step="1"
              :value="item.size"
              @change="patchItem({ size: numberValue($event, item.size) })"
            />
          </label>
          <label class="field">
            对齐
            <span class="segmented">
              <button :class="{ on: item.align === 'left' }" title="左对齐" @click="patchItem({ align: 'left' })">左</button>
              <button :class="{ on: item.align === 'center' }" title="居中" @click="patchItem({ align: 'center' })">中</button>
              <button :class="{ on: item.align === 'right' }" title="右对齐" @click="patchItem({ align: 'right' })">右</button>
            </span>
          </label>
        </div>
        <label class="check-row">
          <input type="checkbox" :checked="item.bold" @change="patchItem({ bold: $event.target.checked })" />
          <span>加粗</span>
        </label>
        <label class="check-row">
          <input type="checkbox" :checked="item.invert" @change="patchItem({ invert: $event.target.checked })" />
          <span>反白显示（黑底白字）</span>
        </label>
        <p class="readout">文本按标签右边界自动换行，超出的行会被裁切（画布上红色叹号提示）。</p>
      </template>

      <!-- 条码 -->
      <template v-else-if="item.type === 'barcode'">
        <label class="field field--stack">
          数据
          <input type="text" :value="item.data" placeholder="ABC123" @input="patchItem({ data: $event.target.value })" />
        </label>
        <label class="field field--stack">
          符号类型
          <select :value="item.symbology" @change="patchItem({ symbology: $event.target.value })">
            <option v-for="entry in SYMBOLOGIES" :key="entry.value" :value="entry.value">
              {{ entry.label }}
            </option>
          </select>
        </label>
        <label class="field field--stack">
          条码高度（mm）
          <input
            type="number"
            min="4"
            max="40"
            step="0.5"
            :value="item.height_mm"
            @change="patchItem({ height_mm: numberValue($event, item.height_mm) })"
          />
        </label>
        <label class="check-row">
          <input type="checkbox" :checked="item.show_text" @change="patchItem({ show_text: $event.target.checked })" />
          <span>显示人眼可读文字</span>
        </label>
        <p class="readout">示意宽约 {{ box.w_mm }} mm；真实条码宽度与编码由后端 zint 生成。</p>
      </template>

      <!-- 二维码 -->
      <template v-else-if="item.type === 'qr'">
        <label class="field field--stack">
          数据
          <input
            type="text"
            :value="item.data"
            placeholder="https://example.com"
            @input="patchItem({ data: $event.target.value })"
          />
        </label>
        <label class="field field--stack">
          尺寸（mm）
          <input
            type="number"
            min="6"
            max="60"
            step="0.5"
            :value="item.size_mm"
            @change="patchItem({ size_mm: numberValue($event, item.size_mm) })"
          />
        </label>
        <p class="readout">二维码为正方形；真实编码由后端 zint 生成。</p>
      </template>
    </section>

    <!-- 标签 -->
    <section class="prop-section">
      <h3 class="section-title">标签设置</h3>
      <label class="field field--stack">
        名称
        <input
          type="text"
          :value="state.doc.name"
          placeholder="标签名称"
          @input="updateDoc({ name: $event.target.value })"
        />
      </label>
      <div class="field-grid">
        <label class="field">
          宽度（mm）
          <input
            type="number"
            min="10"
            max="200"
            step="0.5"
            :value="state.doc.width_mm"
            @change="updateDoc({ width_mm: numberValue($event, state.doc.width_mm) })"
          />
        </label>
        <label class="field">
          高度（mm）
          <input
            type="number"
            min="10"
            max="300"
            step="0.5"
            :value="state.doc.height_mm"
            @change="updateDoc({ height_mm: numberValue($event, state.doc.height_mm) })"
          />
        </label>
      </div>
      <label class="field field--stack">
        尺寸预设
        <select :value="presetId" @change="onPresetChange">
          <option v-for="entry in LABEL_PRESETS" :key="entry.id" :value="entry.id">{{ entry.label }}</option>
        </select>
      </label>
      <label class="field field--stack">
        <span class="field-label">
          纸卷偏移（mm）
          <span
            class="info-dot"
            title="M1 纸筒挡板在左侧、纸仓 50mm：40mm 卷纸需整体右移 10mm（默认），50mm 卷纸填 0。该值写入 spec.offset_x_mm，后端按打印头坐标渲染位图（宽 = 偏移 + 标签宽）。"
            >i</span
          >
        </span>
        <input
          type="number"
          min="0"
          max="20"
          step="0.5"
          :value="state.doc.offset_x_mm"
          @change="updateDoc({ offset_x_mm: Number($event.target.value) })"
        />
      </label>
      <p class="readout">
        40 mm 卷纸 = 10 · 50 mm 卷纸 = 0。位图宽
        {{ bitmapDotW }} px（{{ bitmapMm }} mm = 偏移 {{ offsetMm }} + 标签 {{ state.doc.width_mm }}）。
      </p>
      <label class="field field--stack">
        打印对齐
        <select :value="state.doc.align" @change="updateDoc({ align: $event.target.value })">
          <option value="left">左对齐（默认）</option>
          <option value="center">居中</option>
          <option value="right">右对齐</option>
        </select>
      </label>
      <div class="field-grid">
        <label class="field">
          浓度
          <select :value="state.doc.density" @change="updateDoc({ density: Number($event.target.value) })">
            <option v-for="density in DENSITIES" :key="density" :value="density">{{ density }}</option>
          </select>
        </label>
        <label class="field">
          外框
          <span class="check-row check-row--boxed">
            <input
              type="checkbox"
              :checked="state.doc.border"
              @change="updateDoc({ border: $event.target.checked })"
            />
            <span>{{ state.doc.border ? '已开启' : '关闭' }}</span>
          </span>
        </label>
      </div>
      <p class="readout">
        画布 1 mm = 8 dot（203dpi）· 位图 {{ bitmapDotW }} ×
        {{ Math.round(state.doc.height_mm * 8) }} dot（{{ bitmapMm }} mm 宽 = 偏移
        {{ offsetMm }} + 标签 {{ state.doc.width_mm }}）
      </p>
      <p v-if="geometryWarning" class="readout readout--warn">{{ geometryWarning }}</p>
    </section>

    <!-- spec -->
    <section class="prop-section">
      <details class="spec-panel">
        <summary>查看 / 复制 spec JSON</summary>
        <pre class="spec-code">{{ specJson }}</pre>
        <button class="btn btn-light btn--small" @click="copySpecJson">
          <AppIcon name="copy" :size="14" />
          <span>复制 spec</span>
        </button>
      </details>
    </section>
  </aside>
</template>
