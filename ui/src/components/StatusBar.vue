<script setup>
import { computed } from 'vue'
import AppIcon from './AppIcon.vue'
import { API_BASE } from '../api'
import { learnPaper, refreshStatus, state } from '../stores/store'

const phase = computed(() => state.backend.phase)
const info = computed(() => state.backend.info)

const dotClass = computed(() => {
  if (phase.value === 'checking') return 'is-checking'
  if (phase.value === 'offline') return 'is-err'
  if (phase.value === 'online') {
    if (!info.value) return 'is-idle'
    return info.value.tone === 'ok' ? 'is-ok' : info.value.tone === 'warn' ? 'is-warn' : 'is-err'
  }
  return 'is-idle'
})

const statusText = computed(() => {
  if (phase.value === 'checking') return '正在检测打印机…'
  if (phase.value === 'offline') return '后端未启动'
  if (phase.value === 'online' && info.value) {
    const parts = [info.value.model || '打印机', info.value.stateLabel]
    if (info.value.errors.length > 0) parts.push(info.value.errors.join('、'))
    return parts.join(' · ')
  }
  return '等待检测'
})

const statusDetail = computed(() => {
  if (phase.value === 'offline') return state.backend.error || `无法连接 ${API_BASE}`
  if (info.value) {
    try {
      return JSON.stringify(info.value.raw).slice(0, 900)
    } catch {
      return ''
    }
  }
  return ''
})

const lastCheckText = computed(() => {
  if (!state.backend.lastCheck) return ''
  const date = new Date(state.backend.lastCheck)
  return date.toLocaleTimeString('zh-CN', { hour12: false })
})

const offsetMm = computed(() => Number(state.doc.offset_x_mm) || 0)
const offsetY = computed(() => Number(state.doc.offset_y_mm) || 0)
const trimMm = computed(() => Number(state.doc.trim_bottom_mm) || 0)
// 位图宽 = 纸卷偏移 + 标签宽（40mm 纸 + 10mm 偏移 = 400 dot）
const dotWidth = computed(() => Math.round((state.doc.width_mm + offsetMm.value) * 8))
const dotHeight = computed(() => Math.round(state.doc.height_mm * 8))
</script>

<template>
  <footer class="statusbar">
    <div class="status-group">
      <span class="status-dot" :class="dotClass" aria-hidden="true"></span>
      <span class="status-main" :title="statusDetail">{{ statusText }}</span>
      <span v-if="phase === 'offline'" class="status-error">{{ state.backend.error }}</span>
      <span v-else-if="state.backend.error" class="status-error">{{ state.backend.error }}</span>
      <span v-if="lastCheckText" class="status-time">最近检测 {{ lastCheckText }}</span>
      <button class="mini-btn" :disabled="phase === 'checking'" title="重新获取打印机状态" @click="refreshStatus()">
        <AppIcon name="refresh" :size="12" :class="{ spin: phase === 'checking' }" />
        <span>刷新</span>
      </button>
      <button class="mini-btn" :disabled="state.backend.learning" title="POST /api/learn — 走纸并重新定位标签" @click="learnPaper()">
        <AppIcon name="learn" :size="12" :class="{ spin: state.backend.learning }" />
        <span>{{ state.backend.learning ? '测纸中…' : '测纸' }}</span>
      </button>
    </div>

    <div class="status-meta">
      <span>标签 <strong>{{ state.doc.width_mm }} × {{ state.doc.height_mm }} mm</strong></span>
      <span>偏移 {{ offsetMm }} mm</span>
      <span v-if="offsetY !== 0">垂直 {{ offsetY }} mm</span>
      <span>少走 {{ trimMm }} mm</span>
      <span>位图 {{ dotWidth }} × {{ dotHeight }} dot</span>
      <span>元素 {{ state.doc.items.length }}</span>
      <span>缩放 {{ Math.round(state.zoom * 100) }}%</span>
      <span class="status-api" :title="API_BASE">{{ API_BASE.replace('http://', '') }}</span>
    </div>
  </footer>
</template>
