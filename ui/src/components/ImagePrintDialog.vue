<script setup>
import { computed } from 'vue'
import AppIcon from './AppIcon.vue'
import { closeImagePrint, printCurrentImage, state } from '../stores/store'

const img = computed(() => state.imagePrint)

const printerLabel = computed(() => {
  if (!state.printer.addr) return '未选择打印机'
  const name = state.printer.name || state.printer.addr
  return name === state.printer.addr ? name : `${name} (${state.printer.addr})`
})

const kindLabel = computed(() =>
  img.value.kind === 'svg' ? 'SVG（已在浏览器栅格化）' : 'PNG / JPEG',
)
</script>

<template>
  <Transition name="fade">
    <div v-if="img.open" class="modal-mask" @click.self="closeImagePrint">
      <div class="modal image-print-modal" role="dialog" aria-modal="true" aria-label="导入图片打印确认">
        <header class="modal-head">
          <div>
            <h2>导入图片打印</h2>
            <p class="modal-sub">
              PNG / JPG / SVG · 目标 {{ img.widthMm }} × {{ img.heightMm }} mm + 纸卷偏移
              {{ img.offsetMm }} mm · 由服务端缩放打印
            </p>
          </div>
          <button class="icon-btn" title="关闭" @click="closeImagePrint">
            <AppIcon name="close" :size="16" />
          </button>
        </header>

        <div class="image-print-body">
          <div v-if="img.error" class="dialog-banner">
            <AppIcon name="warning" :size="16" />
            <span>{{ img.error }}</span>
          </div>

          <figure class="image-print-figure">
            <img class="preview-img" :src="img.previewUrl" alt="图片打印预览" />
            <figcaption>预览为最终位图（含纸卷偏移）；打印由服务端按当前标签尺寸缩放</figcaption>
          </figure>

          <div class="image-print-meta">
            <span>文件：<strong>{{ img.fileName }}</strong>（{{ kindLabel }}）</span>
            <span>
              位图：<strong>{{ img.fullWidthPx }} × {{ img.labelHeightPx }} px</strong>（标签
              {{ img.labelWidthPx }} × {{ img.labelHeightPx }} + 偏移 {{ img.offsetMm }} mm）
            </span>
            <span>打印机：<strong>{{ printerLabel }}</strong></span>
            <span>份数：<strong>{{ state.copies }}</strong> · 浓度：<strong>{{ state.doc.density }}</strong></span>
          </div>

          <p class="image-print-note">
            打印前请确认版式；如需调整位置或尺寸，请修改标签设置 / 纸卷偏移后重新导入。
          </p>
        </div>

        <footer class="modal-foot">
          <button class="btn btn-light" :disabled="img.printing" @click="closeImagePrint()">取消</button>
          <span class="modal-foot-spacer"></span>
          <button class="btn btn-primary" :disabled="img.printing" @click="printCurrentImage()">
            <AppIcon :name="img.printing ? 'refresh' : 'print'" :size="14" :class="{ spin: img.printing }" />
            <span>{{ img.printing ? '打印中…' : '打印图片' }}</span>
          </button>
        </footer>
      </div>
    </div>
  </Transition>
</template>
