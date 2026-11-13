<script setup>
import { computed } from 'vue'
import AppIcon from './AppIcon.vue'
import { API_BASE } from '../api'
import {
  closePreview,
  copySpecJson,
  printLabel,
  refreshPreview,
  state,
} from '../stores/store'

const preview = computed(() => state.preview)

function downloadPng() {
  if (!preview.value.url) return
  const anchor = document.createElement('a')
  const name = String(state.doc.name || 'label').replace(/[\\/:*?"<>|\s]+/g, '-')
  anchor.href = preview.value.url
  anchor.download = `${name || 'label'}-preview.png`
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
}
</script>

<template>
  <Transition name="fade">
    <div v-if="preview.open" class="modal-mask" @click.self="closePreview">
      <div class="modal" role="dialog" aria-modal="true" aria-label="打印预览">
        <header class="modal-head">
          <div>
            <h2>打印预览</h2>
            <p class="modal-sub">POST {{ API_BASE }}/api/preview · 后端按「纸卷偏移 + 标签宽」渲染 PNG</p>
          </div>
          <button class="icon-btn" title="关闭" @click="closePreview">
            <AppIcon name="close" :size="16" />
          </button>
        </header>

        <div class="modal-body">
          <div v-if="preview.loading" class="preview-state">
            <span class="spinner"></span>
            <p>正在生成预览…</p>
          </div>

          <div v-else-if="preview.error" class="preview-state">
            <span class="state-icon state-icon--error"><AppIcon name="warning" :size="20" /></span>
            <p class="preview-error-text">{{ preview.error }}</p>
            <p class="preview-hint">
              请确认标签后端已启动（默认 {{ API_BASE }}），并允许跨域访问，然后重试。
            </p>
            <button class="btn btn-primary" @click="refreshPreview()">重试</button>
          </div>

          <figure v-else-if="preview.url" class="preview-figure">
            <img class="preview-img" :src="preview.url" alt="标签 PNG 预览" />
            <figcaption>
              {{ state.doc.width_mm }} × {{ state.doc.height_mm }} mm · 纸卷偏移
              {{ state.doc.offset_x_mm }} mm · 位图
              {{ Math.round((state.doc.width_mm + state.doc.offset_x_mm) * 8) }} ×
              {{ Math.round(state.doc.height_mm * 8) }} dot
            </figcaption>
          </figure>

          <div v-else class="preview-state">
            <p>暂无预览</p>
          </div>
        </div>

        <footer class="modal-foot">
          <button class="btn btn-light" @click="copySpecJson()">
            <AppIcon name="copy" :size="14" />
            <span>复制 spec</span>
          </button>
          <button class="btn btn-light" :disabled="!preview.url" @click="downloadPng">
            <AppIcon name="download" :size="14" />
            <span>保存 PNG</span>
          </button>
          <span class="modal-foot-spacer"></span>
          <button class="btn btn-light" @click="closePreview">关闭</button>
          <button class="btn btn-primary" :disabled="state.printing" @click="printLabel()">
            <AppIcon name="print" :size="14" />
            <span>{{ state.printing ? '打印中…' : '直接打印' }}</span>
          </button>
        </footer>
      </div>
    </div>
  </Transition>
</template>
