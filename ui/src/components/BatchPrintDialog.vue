<script setup>
import { computed, ref } from 'vue'
import AppIcon from './AppIcon.vue'
import { api } from '../api'
import { toSpec } from '../label'
import { buildRowSpec, extractPlaceholders, parseTable } from '../batch'
import { closeBatchPrint, openPrinterDialog, showToast, state } from '../stores/store'

const csvText = ref('')
const fileName = ref('')
const fileInput = ref(null)
const running = ref(false)
const stopRequested = ref(false)
const processed = ref(0)
const successCount = ref(0)
const failed = ref([])

const parsed = computed(() => parseTable(csvText.value))
const headers = computed(() => parsed.value.headers)
const rows = computed(() => parsed.value.rows)
const parseError = computed(() => parsed.value.error)
const placeholders = computed(() => extractPlaceholders(state.doc))
const missingColumns = computed(() =>
  placeholders.value.filter((name) => !headers.value.includes(name)),
)
const target = computed(() =>
  state.printer.name ? `${state.printer.name} (${state.printer.addr})` : state.printer.addr,
)
const canStart = computed(
  () => !running.value && rows.value.length > 0 && !!state.printer.addr,
)
const firstRowTexts = computed(() => {
  if (!rows.value.length) return []
  const built = buildRowSpec(toSpec(state.doc), rows.value[0])
  return built.items
    .filter((item) => item.type === 'text')
    .map((item) => item.text)
    .filter(Boolean)
})

function pickCsv() {
  if (running.value) return
  fileInput.value?.click()
}

async function onCsvFile(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file) return
  try {
    const text = await file.text()
    csvText.value = text
    fileName.value = file.name
    showToast(`已读取 ${file.name}`, 'success', 1800)
  } catch (error) {
    showToast(`读取 CSV 失败：${error.message || error}`, 'error', 5000)
  }
}

function sleep(ms) {
  return new Promise((resolve) => {
    setTimeout(resolve, ms)
  })
}

function firstError(result) {
  return result?.error || result?.errors?.join?.('、') || result?.message || '打印失败'
}

async function startBatch() {
  if (running.value) return
  if (!state.printer.addr) {
    showToast('请先选择打印机', 'warn', 4500)
    openPrinterDialog()
    return
  }
  if (!rows.value.length) {
    showToast('没有可打印的数据行', 'warn', 4000)
    return
  }
  running.value = true
  state.batchPrint.running = true
  stopRequested.value = false
  processed.value = 0
  successCount.value = 0
  failed.value = []
  const total = rows.value.length
  const spec = toSpec(state.doc)
  for (let index = 0; index < total; index += 1) {
    if (stopRequested.value) break
    try {
      const payload = {
        ...buildRowSpec(spec, rows.value[index]),
        addr: state.printer.addr,
        copies: 1,
      }
      const result = await api.printLabel(payload)
      if (!result || result.ok === false) throw new Error(firstError(result))
      successCount.value += 1
    } catch (error) {
      failed.value.push({ row: index + 1, message: error.message || String(error) })
    }
    processed.value = index + 1
    if (index < total - 1) await sleep(1000)
  }
  running.value = false
  state.batchPrint.running = false
  if (failed.value.length) {
    showToast(
      `批量打印完成：成功 ${successCount.value} 行，失败 ${failed.value.length} 行`,
      'warn',
      6500,
    )
  } else {
    showToast(`批量打印完成：${successCount.value} 行已发送`, 'success', 5000)
  }
}

function requestStop() {
  if (!running.value) return
  stopRequested.value = true
  showToast('将在当前行打印完成后停止', 'info', 3000)
}

function close() {
  if (running.value) return
  closeBatchPrint()
}
</script>

<template>
  <div
    v-if="state.batchPrint.open"
    class="modal-mask"
    @click.self="close"
  >
    <div class="modal batch-modal" role="dialog" aria-modal="true" aria-label="批量打印">
      <header class="modal-head">
        <div>
          <h2>批量打印</h2>
          <p class="modal-sub">
            CSV / 粘贴表格 · 第一行为表头 · 文本元素用 <code v-text="'{{列名}}'"></code> 占位 ·
            每行打印一张（copies=1，间隔 1s）
          </p>
        </div>
        <button class="icon-btn" title="关闭" :disabled="running" @click="close">
          <AppIcon name="close" :size="16" />
        </button>
      </header>

      <div class="modal-body batch-body">
        <div class="batch-source-row">
          <button class="btn btn-light btn--small" :disabled="running" @click="pickCsv">
            <AppIcon name="table" :size="14" />
            <span>选择 CSV / TXT 文件</span>
          </button>
          <input
            ref="fileInput"
            class="visually-hidden"
            type="file"
            accept=".csv,.txt,text/csv,text/plain"
            @change="onCsvFile"
          />
          <span class="batch-file-name">{{ fileName || '或直接粘贴下方表格' }}</span>
        </div>

        <textarea
          v-model="csvText"
          class="batch-textarea"
          rows="6"
          spellcheck="false"
          :disabled="running"
          placeholder="第一行为表头，例如：&#10;物料,规格,编号&#10;物料 A,24×18,001&#10;物料 B,30×20,002&#10;&#10;也可以直接从 Excel 复制粘贴（制表符分隔）"
        ></textarea>
        <p v-if="parseError" class="readout readout--warn">{{ parseError }}</p>

        <div v-if="headers.length" class="batch-summary">
          <span>表头（{{ headers.length }} 列）：{{ headers.join(' / ') }}</span>
          <span>数据行：{{ rows.length }}</span>
          <span>打印机：{{ target || '未选择' }}</span>
        </div>

        <div v-if="placeholders.length" class="batch-placeholders">
          <span>检测到占位符：{{ placeholders.join('、') }}</span>
          <span v-if="missingColumns.length" class="batch-missing">
            缺少列：{{ missingColumns.join('、') }}（将保留原占位文字）
          </span>
        </div>
        <p v-else-if="rows.length" class="readout">
          当前文本元素没有 <code v-text="'{{占位符}}'"></code>，将把同一张标签连续打印
          {{ rows.length }} 次。
        </p>

        <div v-if="rows.length" class="batch-table-wrap">
          <table class="batch-table">
            <thead>
              <tr>
                <th v-for="header in headers" :key="header">{{ header }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(row, rowIndex) in rows.slice(0, 3)" :key="rowIndex">
                <td v-for="header in headers" :key="header">{{ row[header] }}</td>
              </tr>
            </tbody>
          </table>
          <p v-if="rows.length > 3" class="readout">仅预览前 3 行，共 {{ rows.length }} 行。</p>
        </div>

        <div v-if="firstRowTexts.length" class="batch-preview">
          <span class="batch-preview-title">第 1 行文本预览</span>
          <span v-for="(text, index) in firstRowTexts" :key="index" class="batch-preview-item">{{ text }}</span>
        </div>

        <div v-if="running || processed > 0" class="batch-progress">
          <progress :value="processed" :max="rows.length"></progress>
          <span>
            第 {{ processed }} / {{ rows.length }} 行 · 成功 {{ successCount }} · 失败
            {{ failed.length }}
          </span>
        </div>

        <div v-if="failed.length" class="batch-failed">
          <p class="batch-failed-title">失败行</p>
          <ul>
            <li v-for="entry in failed" :key="entry.row">
              第 {{ entry.row }} 行：{{ entry.message }}
            </li>
          </ul>
        </div>
      </div>

      <footer class="modal-foot">
        <button class="btn btn-light" :disabled="running" @click="close">关闭</button>
        <span class="modal-foot-spacer"></span>
        <button v-if="running" class="btn btn-light" @click="requestStop">停止</button>
        <button
          class="btn btn-primary"
          :disabled="!canStart"
          :title="rows.length ? `连续打印 ${rows.length} 张，每张间隔 1s` : '请先粘贴或导入表格'"
          @click="startBatch"
        >
          <AppIcon :name="running ? 'refresh' : 'print'" :size="15" :class="{ spin: running }" />
          <span>{{ running ? `打印中… ${processed}/${rows.length}` : '开始打印' }}</span>
        </button>
      </footer>
    </div>
  </div>
</template>
