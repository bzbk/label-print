<script setup>
import { ref } from 'vue'
import AppIcon from './AppIcon.vue'
import {
  addImageElementFromFile,
  addItem,
  applySavedTemplate,
  applyTemplate,
  deleteSavedTemplate,
  openBatchPrint,
  saveCurrentAsTemplate,
  state,
  updateDoc,
} from '../stores/store'

const imageInput = ref(null)
const templateName = ref('')

function onSaveTemplate() {
  const saved = saveCurrentAsTemplate(templateName.value)
  if (saved) templateName.value = ''
}

function templateDate(iso) {
  if (!iso) return ''
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  return `${date.getMonth() + 1}/${date.getDate()}`
}

function pickImage() {
  if (state.imageProcessing) return
  imageInput.value?.click()
}

async function onImageChange(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (file) await addImageElementFromFile(file)
}

const TYPES = [
  { type: 'text', icon: 'text', label: '文本', hint: '物料名 / 规格 / 编号' },
  { type: 'barcode', icon: 'barcode', label: '一维条码', hint: 'Code128 / EAN13 / Code39' },
  { type: 'qr', icon: 'qr', label: '二维码', hint: '网址 / 序列号 / 追溯码' },
]

function toggleBorder(event) {
  updateDoc({ border: event.target.checked })
}
</script>

<template>
  <aside class="palette">
    <div class="palette-head">
      <h2>元素库</h2>
      <span class="palette-sub">点击添加</span>
    </div>

    <div class="palette-items">
      <button
        v-for="entry in TYPES"
        :key="entry.type"
        class="palette-card"
        :title="`添加${entry.label}元素`"
        @click="addItem(entry.type)"
      >
        <span class="palette-icon"><AppIcon :name="entry.icon" :size="18" /></span>
        <span class="palette-copy">
          <strong>{{ entry.label }}</strong>
          <small>{{ entry.hint }}</small>
        </span>
      </button>

      <button
        class="palette-card"
        :disabled="state.imageProcessing"
        title="添加图片元素（PNG / JPG / SVG，作为画布元素参与排版）"
        @click="pickImage()"
      >
        <span class="palette-icon"><AppIcon name="image" :size="18" /></span>
        <span class="palette-copy">
          <strong>{{ state.imageProcessing ? '处理中…' : '图片' }}</strong>
          <small>PNG / JPG / SVG · 参与排版</small>
        </span>
      </button>
      <input
        ref="imageInput"
        class="visually-hidden"
        type="file"
        accept=".png,.jpg,.jpeg,.svg,image/png,image/jpeg,image/svg+xml"
        @change="onImageChange"
      />
    </div>

    <div class="palette-section-head">
      <h2>标签模板</h2>
    </div>
    <button class="palette-card palette-card--template" @click="applyTemplate('material')">
      <span class="palette-icon"><AppIcon name="template" :size="18" /></span>
      <span class="palette-copy">
        <strong>物料标签</strong>
        <small>40 × 30 mm · 文本 + 条码 + 二维码</small>
      </span>
    </button>
    <button class="palette-card palette-card--template" @click="applyTemplate('logistics')">
      <span class="palette-icon"><AppIcon name="template" :size="18" /></span>
      <span class="palette-copy">
        <strong>物流标签</strong>
        <small>40 × 60 mm · 多行文本 + 条码</small>
      </span>
    </button>

    <div class="palette-section-head">
      <h2>我的模板</h2>
      <span class="palette-sub">{{ state.savedTemplates.length }} 个</span>
    </div>
    <div class="template-save-row">
      <input
        v-model="templateName"
        class="template-name-input"
        type="text"
        maxlength="40"
        placeholder="模板名称（可留空）"
        @keydown.enter="onSaveTemplate"
      />
      <button class="mini-btn" title="把当前标签保存为本地模板" @click="onSaveTemplate">
        保存
      </button>
    </div>
    <div
      v-for="template in state.savedTemplates"
      :key="template.id"
      class="saved-template-row"
    >
      <button
        class="palette-card palette-card--template"
        :title="`一键新建：${template.name}`"
        @click="applySavedTemplate(template.id)"
      >
        <span class="palette-icon"><AppIcon name="template" :size="18" /></span>
        <span class="palette-copy">
          <strong>{{ template.name }}</strong>
          <small>{{ templateDate(template.createdAt) }} · {{ (template.spec.items || []).length }} 个元素</small>
        </span>
      </button>
      <button
        class="icon-btn template-delete"
        title="删除模板"
        @click.stop="deleteSavedTemplate(template.id)"
      >
        <AppIcon name="trash" :size="14" />
      </button>
    </div>
    <p v-if="!state.savedTemplates.length" class="palette-empty">
      暂无保存的模板；编辑好标签后点上方「保存」。
    </p>

    <div class="palette-section-head">
      <h2>打印工具</h2>
    </div>
    <button
      class="palette-card"
      title="导入 CSV / 粘贴表格，逐行生成标签并连续打印"
      @click="openBatchPrint()"
    >
      <span class="palette-icon"><AppIcon name="table" :size="18" /></span>
      <span class="palette-copy">
        <strong>批量打印</strong>
        <small>CSV / 粘贴表格 · 逐行打印</small>
      </span>
    </button>

    <div class="palette-toggle">
      <label class="check-row">
        <input type="checkbox" :checked="state.doc.border" @change="toggleBorder" />
        <span>显示标签外框</span>
      </label>
    </div>

    <div class="palette-footer">
      <p class="palette-footer-title">操作提示</p>
      <ul>
        <li>拖动元素移动，自动吸附 1 mm 网格</li>
        <li><kbd>Delete</kbd> 删除 · <kbd>Ctrl</kbd>+<kbd>D</kbd> 复制</li>
        <li>方向键微调 0.5 mm，<kbd>Shift</kbd> 改为 0.1 mm</li>
        <li><kbd>Ctrl</kbd>+<kbd>Z</kbd> 撤销 · <kbd>Ctrl</kbd>+<kbd>A</kbd> 全选 · Shift 点选多选</li>
      </ul>
    </div>
  </aside>
</template>
