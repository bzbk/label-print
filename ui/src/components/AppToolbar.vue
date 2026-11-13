<script setup>
import { computed, ref } from 'vue'
import AppIcon from './AppIcon.vue'
import { DENSITIES, LABEL_PRESETS } from '../label'
import {
  alignSelected,
  newDoc,
  openJsonFile,
  openPreview,
  printLabel,
  saveJson,
  setPreset,
  state,
  updateDoc,
} from '../stores/store'

const fileInput = ref(null)
const alignChoice = ref('')

const presetValue = computed({
  get() {
    const doc = state.doc
    const preset = LABEL_PRESETS.find(
      (entry) => entry.width_mm === doc.width_mm && entry.height_mm === doc.height_mm,
    )
    return preset ? preset.id : 'custom'
  },
  set(value) {
    if (value === 'custom') return
    const preset = LABEL_PRESETS.find((entry) => entry.id === value)
    if (preset) setPreset(preset)
  },
})

function triggerOpen() {
  fileInput.value?.click()
}

async function onFileChange(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (file) await openJsonFile(file)
}

function onAlignChange(event) {
  const mode = event.target.value
  alignChoice.value = ''
  if (mode) alignSelected(mode)
}

function onCopiesChange(event) {
  state.copies = Math.min(99, Math.max(1, Math.round(Number(event.target.value) || 1)))
}

function onOffsetChange(event) {
  updateDoc({ offset_x_mm: Number(event.target.value) })
}

function onOffsetYChange(event) {
  updateDoc({ offset_y_mm: Number(event.target.value) })
}

function onTrimChange(event) {
  updateDoc({ trim_bottom_mm: Number(event.target.value) })
}
</script>

<template>
  <header class="toolbar">
    <div class="brand">
      <span class="brand-mark">共</span>
      <span class="brand-text">
        <strong>共风 GoFor</strong>
        <small>Label Print · 标签设计器</small>
      </span>
    </div>

    <div class="toolbar-group">
      <button class="tool-btn" title="新建空白标签" @click="newDoc()">
        <AppIcon name="new" :size="15" />
        <span>新建</span>
      </button>
      <button class="tool-btn" title="打开本地 spec JSON" @click="triggerOpen">
        <AppIcon name="open" :size="15" />
        <span>打开 JSON</span>
      </button>
      <button class="tool-btn" title="导出 spec JSON（Ctrl+S）" @click="saveJson()">
        <AppIcon name="save" :size="15" />
        <span>保存 JSON</span>
      </button>
      <input
        ref="fileInput"
        class="visually-hidden"
        type="file"
        accept=".json,application/json"
        @change="onFileChange"
      />
    </div>

    <span class="toolbar-sep" aria-hidden="true"></span>

    <label class="toolbar-field">
      <span>标签</span>
      <select v-model="presetValue" title="标签尺寸预设">
        <option v-for="preset in LABEL_PRESETS" :key="preset.id" :value="preset.id">
          {{ preset.label }}
        </option>
      </select>
    </label>

    <label
      class="toolbar-field"
      title="纸卷相对打印头原点的右移量：40mm 卷纸在 50mm 纸仓 = 10，50mm 卷纸 = 0"
    >
      <span>纸卷偏移</span>
      <input
        class="toolbar-num"
        type="number"
        min="0"
        max="20"
        step="0.5"
        :value="state.doc.offset_x_mm"
        @change="onOffsetChange"
      />
      <span class="toolbar-suffix">mm</span>
    </label>

    <label
      class="toolbar-field"
      title="内容整体上下微调，不影响间隙检测；正=下移，负=上移（范围 -15~15mm，步进 0.5）"
    >
      <span>垂直微调</span>
      <input
        class="toolbar-num"
        type="number"
        min="-15"
        max="15"
        step="0.5"
        :value="state.doc.offset_y_mm"
        @change="onOffsetYChange"
      />
      <span class="toolbar-suffix">mm</span>
    </label>

    <label
      class="toolbar-field"
      title="固件不支持反向走纸；此值 = 打印后少走多少 mm，等效回退。默认 0（打印流程已自动走到下一张缝隙）；如撕纸位置有偏差可手动调到 1~2mm。"
    >
      <span>底部少走</span>
      <input
        class="toolbar-num"
        type="number"
        min="0"
        max="5"
        step="0.5"
        :value="state.doc.trim_bottom_mm"
        @change="onTrimChange"
      />
      <span class="toolbar-suffix">mm</span>
    </label>

    <label class="toolbar-field">
      <span>份数</span>
      <input
        class="toolbar-num"
        type="number"
        min="1"
        max="99"
        :value="state.copies"
        title="打印份数"
        @change="onCopiesChange"
      />
    </label>

    <label class="toolbar-field">
      <span>浓度</span>
      <select v-model.number="state.doc.density" title="打印浓度（后端映射为 heat/speed）">
        <option v-for="density in DENSITIES" :key="density" :value="density">{{ density }}</option>
      </select>
    </label>

    <label class="toolbar-field">
      <span>对齐</span>
      <select
        v-model="alignChoice"
        :disabled="!state.selectedId"
        :title="state.selectedId ? '相对标签画布对齐所选元素' : '请先选中一个元素'"
        @change="onAlignChange"
      >
        <option value="">选择…</option>
        <option value="left">左对齐</option>
        <option value="center">水平居中</option>
        <option value="right">右对齐</option>
        <option value="top">上对齐</option>
        <option value="middle">垂直居中</option>
        <option value="bottom">下对齐</option>
      </select>
    </label>

    <div class="toolbar-actions">
      <button class="btn btn-ghost" title="调用后端生成 PNG 预览" @click="openPreview()">
        <AppIcon name="preview" :size="16" />
        <span>预览</span>
      </button>
      <button
        class="btn btn-primary"
        :disabled="state.printing"
        title="调用后端打印（份数 = 上方数值）"
        @click="printLabel()"
      >
        <AppIcon :name="state.printing ? 'refresh' : 'print'" :size="16" :class="{ spin: state.printing }" />
        <span>{{ state.printing ? '打印中…' : '打印' }}</span>
      </button>
    </div>
  </header>
</template>
