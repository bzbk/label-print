<script setup>
import { onBeforeUnmount, onMounted } from 'vue'
import AppToolbar from './components/AppToolbar.vue'
import ElementPalette from './components/ElementPalette.vue'
import LabelCanvas from './components/LabelCanvas.vue'
import PropertyPanel from './components/PropertyPanel.vue'
import StatusBar from './components/StatusBar.vue'
import ImagePrintDialog from './components/ImagePrintDialog.vue'
import PrinterDialog from './components/PrinterDialog.vue'
import PreviewDialog from './components/PreviewDialog.vue'
import ToastStack from './components/ToastStack.vue'
import {
  clearSelection,
  closeImagePrint,
  closePreview,
  closePrinterDialog,
  duplicateItem,
  nudgeSelected,
  refreshStatus,
  removeItem,
  saveJson,
  startStatusPolling,
  state,
} from './stores/store'

let stopPolling = null

function isTypingTarget(target) {
  const tag = (target?.tagName || '').toLowerCase()
  return tag === 'input' || tag === 'textarea' || tag === 'select' || target?.isContentEditable
}

function onKeydown(event) {
  if (isTypingTarget(event.target)) return
  const meta = event.ctrlKey || event.metaKey

  if (meta && (event.key === 'd' || event.key === 'D')) {
    event.preventDefault()
    if (state.selectedId) duplicateItem(state.selectedId)
    return
  }
  if (meta && (event.key === 's' || event.key === 'S')) {
    event.preventDefault()
    saveJson()
    return
  }
  if (event.key === 'Delete' || event.key === 'Backspace') {
    if (state.selectedId) {
      event.preventDefault()
      removeItem(state.selectedId)
    }
    return
  }
  if (event.key === 'Escape') {
    if (state.imagePrint.open) {
      closeImagePrint()
      return
    }
    if (state.printerDialog.open) {
      closePrinterDialog()
      return
    }
    if (state.preview.open) {
      closePreview()
      return
    }
    clearSelection()
    return
  }
  const arrows = {
    ArrowLeft: [-1, 0],
    ArrowRight: [1, 0],
    ArrowUp: [0, -1],
    ArrowDown: [0, 1],
  }
  const delta = arrows[event.key]
  if (delta && state.selectedId) {
    event.preventDefault()
    const step = event.shiftKey ? 0.2 : 1
    nudgeSelected(delta[0] * step, delta[1] * step)
  }
}

onMounted(() => {
  window.addEventListener('keydown', onKeydown)
  refreshStatus()
  stopPolling = startStatusPolling(15000)
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeydown)
  if (stopPolling) stopPolling()
})
</script>

<template>
  <div class="app-shell">
    <AppToolbar />
    <main class="workspace">
      <ElementPalette class="panel-left" />
      <LabelCanvas class="canvas-area" />
      <PropertyPanel class="panel-right" />
    </main>
    <StatusBar />
    <PreviewDialog />
    <PrinterDialog />
    <ImagePrintDialog />
    <ToastStack />
  </div>
</template>
