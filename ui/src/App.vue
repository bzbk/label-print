<script setup>
import { onBeforeUnmount, onMounted } from 'vue'
import AppToolbar from './components/AppToolbar.vue'
import ElementPalette from './components/ElementPalette.vue'
import LabelCanvas from './components/LabelCanvas.vue'
import PropertyPanel from './components/PropertyPanel.vue'
import StatusBar from './components/StatusBar.vue'
import PrinterDialog from './components/PrinterDialog.vue'
import BatchPrintDialog from './components/BatchPrintDialog.vue'
import PreviewDialog from './components/PreviewDialog.vue'
import ToastStack from './components/ToastStack.vue'
import {
  clearSelection,
  closeBatchPrint,
  closePreview,
  closePrinterDialog,
  copySelected,
  duplicateSelected,
  nudgeSelected,
  pasteClipboard,
  redo,
  refreshStatus,
  removeSelected,
  saveJson,
  selectAll,
  startStatusPolling,
  state,
  undo,
} from './stores/store'

let stopPolling = null

function isTypingTarget(target) {
  const tag = (target?.tagName || '').toLowerCase()
  return tag === 'input' || tag === 'textarea' || tag === 'select' || target?.isContentEditable
}

function onKeydown(event) {
  const meta = event.ctrlKey || event.metaKey
  const key = event.key

  // 撤销/重做：即使在输入框内也优先应用级撤销（需求要求全局 Ctrl+Z）
  if (meta && (key === 'z' || key === 'Z')) {
    event.preventDefault()
    if (event.shiftKey) redo()
    else undo()
    return
  }
  if (meta && (key === 'y' || key === 'Y')) {
    event.preventDefault()
    redo()
    return
  }

  if (isTypingTarget(event.target)) return
  if (meta && (key === 'a' || key === 'A')) {
    event.preventDefault()
    selectAll()
    return
  }
  if (meta && (key === 'c' || key === 'C')) {
    event.preventDefault()
    copySelected()
    return
  }
  if (meta && (key === 'v' || key === 'V')) {
    event.preventDefault()
    pasteClipboard()
    return
  }
  if (meta && (key === 'd' || key === 'D')) {
    event.preventDefault()
    duplicateSelected()
    return
  }
  if (meta && (key === 's' || key === 'S')) {
    event.preventDefault()
    saveJson()
    return
  }
  if (key === 'Delete' || key === 'Backspace') {
    if (state.selectedIds.length) {
      event.preventDefault()
      removeSelected()
    }
    return
  }
  if (key === 'Escape') {
    if (state.batchPrint.open) {
      closeBatchPrint()
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
  const delta = arrows[key]
  if (delta && state.selectedIds.length) {
    event.preventDefault()
    // 方向键 0.5mm，按住 Shift 0.1mm
    const step = event.shiftKey ? 0.1 : 0.5
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
    <BatchPrintDialog />
    <PrinterDialog />
    <ToastStack />
  </div>
</template>
