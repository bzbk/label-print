<script setup>
import { computed } from 'vue'
import AppIcon from './AppIcon.vue'
import { API_BASE } from '../api'
import {
  clearPrinterSelection,
  closePrinterDialog,
  connectPrinter,
  loadKnownDevices,
  printerConnection,
  printerModelLabel,
  refreshStatus,
  scanPrinters,
  setPrinterParam,
  state,
  writePrinterParams,
} from '../stores/store'

const dialog = computed(() => state.printerDialog)
const printer = computed(() => state.printer)
const params = computed(() => state.printerParams)
const connection = printerConnection

function onParamChange(key, event) {
  setPrinterParam(key, event.target.value)
}

const currentAddr = computed(() => (state.printer.addr || '').toUpperCase())
const isScanning = computed(() => dialog.value.scanning)
const isLoading = computed(() => dialog.value.loading)

const deviceList = computed(() => {
  const list = [...(dialog.value.devices || [])]
  if (state.printer.addr && !list.some((device) => device.address.toUpperCase() === currentAddr.value)) {
    list.unshift({
      address: state.printer.addr,
      name: state.printer.name || '上次选择的打印机',
      isM1: state.printer.isM1 || /\bm1\b|hprt/i.test(state.printer.name),
      stored: true,
    })
  }
  return list
})

const connectionError = computed(() => {
  if (dialog.value.error) return dialog.value.error
  if (state.backend.phase === 'offline') {
    return `后端未启动或无法连接：${state.backend.error || API_BASE}`
  }
  return ''
})

function retryBackend() {
  refreshStatus()
  loadKnownDevices()
}

const currentStatusText = computed(() => {
  if (!state.printer.addr) return '尚未选择打印机'
  const parts = [printer.value.name || printer.value.addr]
  if (printer.value.name && printer.value.name !== printer.value.addr) {
    parts.push(`(${printer.value.addr})`)
  }
  if (printerModelLabel.value) parts.push(`· ${printerModelLabel.value}`)
  if (printer.value.statusLabel) parts.push(`· ${printer.value.statusLabel}`)
  return parts.join(' ')
})

function isConnected(device) {
  return (
    currentAddr.value === device.address.toUpperCase() &&
    printer.value.phase === 'connected' &&
    state.backend.phase !== 'offline'
  )
}

function isConnecting(device) {
  return dialog.value.connectingAddr === device.address
}

function deviceError(device) {
  const error = dialog.value.connectError
  return error && error.addr === device.address ? error.message : ''
}

function connect(device) {
  connectPrinter(device)
}
</script>

<template>
  <Transition name="fade">
    <div v-if="dialog.open" class="modal-mask" @click.self="closePrinterDialog">
      <div class="modal printer-modal" role="dialog" aria-modal="true" aria-label="选择打印机">
        <header class="modal-head">
          <div>
            <h2>选择打印机</h2>
            <p class="modal-sub">扫描蓝牙设备 → 选择 → 连接 · 后端 {{ API_BASE }}</p>
          </div>
          <button class="icon-btn" title="关闭" @click="closePrinterDialog">
            <AppIcon name="close" :size="16" />
          </button>
        </header>

        <div class="printer-modal-body">
          <div class="printer-current">
            <span class="printer-current-label">当前打印机</span>
            <strong class="printer-current-name">{{ currentStatusText }}</strong>
            <span class="printer-state" :class="`is-${connection.key}`">{{ connection.label }}</span>
          </div>

          <div class="scan-row">
            <button class="btn btn-primary" :disabled="isScanning" @click="scanPrinters(8)">
              <AppIcon name="refresh" :size="15" :class="{ spin: isScanning }" />
              <span>{{ isScanning ? '扫描中…（约 8 秒）' : '扫描蓝牙设备' }}</span>
            </button>
            <button class="btn btn-light" :disabled="isScanning || isLoading" @click="loadKnownDevices()">
              <AppIcon name="refresh" :size="14" :class="{ spin: isLoading }" />
              <span>{{ isLoading ? '读取中…' : '已知设备' }}</span>
            </button>
            <span v-if="dialog.hint" class="scan-hint" :title="dialog.hint">{{ dialog.hint }}</span>
          </div>

          <div v-if="connectionError" class="dialog-banner">
            <AppIcon name="warning" :size="16" />
            <span>{{ connectionError }}</span>
            <button class="mini-btn" @click="retryBackend()">重试</button>
          </div>

          <div v-if="deviceList.length" class="device-list">
            <div
              v-for="device in deviceList"
              :key="device.address"
              class="device-row"
              :class="{
                'is-m1': device.isM1,
                'is-current': currentAddr === device.address.toUpperCase(),
              }"
            >
              <span class="device-icon"><AppIcon name="printer" :size="16" /></span>
              <div class="device-main">
                <div class="device-name">
                  <span class="device-name-text">{{ device.name || device.address }}</span>
                  <span v-if="device.isM1" class="device-badge">M1</span>
                  <span v-if="currentAddr === device.address.toUpperCase()" class="device-badge device-badge--current">当前</span>
                </div>
                <div class="device-addr">{{ device.address }}</div>
                <div v-if="isConnected(device)" class="device-ok">
                  <AppIcon name="check" :size="13" />
                  <span>已连接 · {{ printerModelLabel }} · {{ printer.statusLabel || '正常' }}</span>
                </div>
                <div v-else-if="deviceError(device)" class="device-error">{{ deviceError(device) }}</div>
                <div v-else-if="currentAddr === device.address.toUpperCase() && printer.error" class="device-error">
                  {{ printer.error }}
                </div>
              </div>
              <span v-if="isConnected(device)" class="device-state-ok">
                <AppIcon name="check" :size="15" />
                已连接
              </span>
              <button
                v-else
                class="btn btn-light btn--small"
                :disabled="!!dialog.connectingAddr || isScanning"
                @click="connect(device)"
              >
                <AppIcon v-if="isConnecting(device)" name="refresh" :size="13" class="spin" />
                <span>{{ isConnecting(device) ? '连接中…' : '连接' }}</span>
              </button>
            </div>
          </div>

          <div v-else class="printer-empty">
            <AppIcon name="printer" :size="28" />
            <p>{{ isLoading ? '正在读取已知设备…' : '暂无设备。请确认打印机已开机，点击「扫描蓝牙设备」。' }}</p>
            <p class="printer-empty-hint">若从未配对，请先在系统蓝牙里配对一次，再扫描。</p>
          </div>

          <section class="printer-settings">
            <div class="printer-settings-head">
              <h3>打印机设置（写入设备）</h3>
              <span>官方 setkey 207 / 208 · 仅写入当前打印机</span>
            </div>
            <p class="printer-settings-warn">
              <AppIcon name="warning" :size="14" />
              <span>写入会触发打印机重新定位，可能走掉几张纸，属正常；208 建议 -2mm（40×30 标签）。</span>
            </p>
            <div class="printer-settings-grid">
              <label class="field">
                起始打印位置 207（mm）
                <input
                  type="number"
                  min="-5"
                  max="5"
                  step="0.5"
                  :value="params.start_pos_mm"
                  :disabled="params.writing"
                  @change="onParamChange('start_pos_mm', $event)"
                />
              </label>
              <label class="field">
                定位后走纸长度 208（mm）
                <input
                  type="number"
                  min="-5"
                  max="5"
                  step="0.5"
                  :value="params.feed_after_pos_mm"
                  :disabled="params.writing"
                  @change="onParamChange('feed_after_pos_mm', $event)"
                />
              </label>
            </div>
            <div class="printer-settings-actions">
              <button
                class="btn btn-primary"
                :disabled="params.writing || !state.printer.addr"
                @click="writePrinterParams()"
              >
                <AppIcon :name="params.writing ? 'refresh' : 'save'" :size="14" :class="{ spin: params.writing }" />
                <span>{{ params.writing ? '写入中…' : '写入设备' }}</span>
              </button>
              <span v-if="!state.printer.addr" class="printer-settings-hint">
                请先在上方选择并连接打印机
              </span>
              <span v-else class="printer-settings-hint">
                已选：{{ state.printer.name || state.printer.addr }}；写入后回填并持久化
              </span>
            </div>
            <p v-if="params.written.length" class="printer-settings-ok">
              <AppIcon name="check" :size="13" />
              <span>已写入：{{ params.written.join('，') }}</span>
            </p>
            <p v-if="params.error" class="printer-settings-error">{{ params.error }}</p>
          </section>
        </div>

        <footer class="modal-foot">
          <button v-if="state.printer.addr" class="btn btn-light" @click="clearPrinterSelection()">
            <AppIcon name="trash" :size="14" />
            <span>清除选择</span>
          </button>
          <span class="modal-foot-spacer"></span>
          <button class="btn btn-light" @click="closePrinterDialog">关闭</button>
          <button class="btn btn-primary" :disabled="!state.printer.addr" @click="closePrinterDialog">完成</button>
        </footer>
      </div>
    </div>
  </Transition>
</template>
