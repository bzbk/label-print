<script setup>
import { computed } from 'vue'

const props = defineProps({
  name: { type: String, required: true },
  size: { type: [Number, String], default: 16 },
})

/** 全部图标为原创简单线稿（24×24 网格），不引用任何第三方图标资源 */
const ICONS = {
  new: ['M12 5v14', 'M5 12h14'],
  open: ['M3 7.5A2.5 2.5 0 0 1 5.5 5H9l2 2.5h7.5A2.5 2.5 0 0 1 21 10v7.5A2.5 2.5 0 0 1 18.5 20h-13A2.5 2.5 0 0 1 3 17.5z'],
  save: ['M5 3.5h11L20.5 8v12.5h-17z', 'M8 3.5V10h8V3.5', 'M8 20.5V14h8v6.5'],
  preview: ['M2.5 12S6 6.5 12 6.5 21.5 12 21.5 12 18 17.5 12 17.5 2.5 12 2.5 12z', 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z'],
  print: ['M7 9V3.5h10V9', 'M5 9h14a2 2 0 0 1 2 2v6h-4v4H7v-4H3v-6a2 2 0 0 1 2-2z', 'M7 17h10'],
  refresh: ['M20 12a8 8 0 1 1-2.34-5.66', 'M20 4v5h-5'],
  learn: ['M6 3.5h12v17H6z', 'M9 7h6', 'M9 11h6', 'M9 15h4', 'M15 15l2 2-2 2'],
  text: ['M5 6.5V5h14v1.5', 'M12 5v14', 'M9 19h6'],
  barcode: ['M3.5 5v14', 'M6.5 5v14', 'M9.5 5v10', 'M12.5 5v14', 'M15.5 5v10', 'M18.5 5v14', 'M20.5 5v10'],
  qr: ['M4 4h6v6H4z', 'M14 4h6v6h-6z', 'M4 14h6v6H4z', 'M14 14h2v2h-2z', 'M18 14h2v2', 'M14 18h2v2', 'M18 18h2v2'],
  trash: ['M4.5 7h15', 'M9.5 7V4.5h5V7', 'M6.5 7l1 13h9l1-13', 'M10.5 11v5', 'M13.5 11v5'],
  duplicate: ['M8.5 8.5H19a2 2 0 0 1 2 2V19a2 2 0 0 1-2 2h-8.5a2 2 0 0 1-2-2z', 'M4 15.5V5a2 2 0 0 1 2-2h9'],
  copy: ['M8.5 8.5H19a2 2 0 0 1 2 2V19a2 2 0 0 1-2 2h-8.5a2 2 0 0 1-2-2z', 'M4 15.5V5a2 2 0 0 1 2-2h9'],
  zoomIn: ['M10.5 4a6.5 6.5 0 1 1 0 13 6.5 6.5 0 0 1 0-13z', 'M20 20l-4.2-4.2', 'M10.5 7.5v6', 'M7.5 10.5h6'],
  zoomOut: ['M10.5 4a6.5 6.5 0 1 1 0 13 6.5 6.5 0 0 1 0-13z', 'M20 20l-4.2-4.2', 'M7.5 10.5h6'],
  fit: ['M4 9V4h5', 'M20 15v5h-5', 'M15 4h5v5', 'M9 20H4v-5'],
  close: ['M6 6l12 12', 'M18 6L6 18'],
  template: ['M4 5h16v4H4z', 'M4 13h9v6H4z', 'M17 13h3v6h-3z'],
  warning: ['M12 3.5 21 20H3z', 'M12 9.5v5', 'M12 17.2h.01'],
  offline: ['M3 3l18 18', 'M8.4 8.5A9.6 9.6 0 0 0 2.5 12S6 17.5 12 17.5c1.7 0 3.2-.4 4.5-1', 'M20 8.5A9.8 9.8 0 0 1 21.5 12a16 16 0 0 1-3.2 3.6'],
  printer: ['M7 9V4h10v5', 'M5 9h14a2 2 0 0 1 2 2v6h-4v3H7v-3H3v-6a2 2 0 0 1 2-2z'],
  image: [
    'M4 5h16a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2z',
    'M8.5 10.5a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3z',
    'M3.5 16.5l4.5-4.5 3 3L15.5 10.5l5 5',
  ],
  check: ['M5 12.5l4.5 4.5L19 7'],
  sliders: ['M4 7h10', 'M18 7h2', 'M4 12h4', 'M12 12h8', 'M4 17h12', 'M20 17h0', 'M16 5v4', 'M10 10v4', 'M18 15v4'],
  align: ['M4 4v16', 'M8 8h10', 'M8 16h6'],
  download: ['M12 4v12', 'M8 12l4 4 4-4', 'M4 20h16'],
}

const paths = computed(() => ICONS[props.name] || [])
const px = computed(() => `${props.size}px`)
</script>

<template>
  <svg
    class="app-icon"
    :style="{ width: px, height: px }"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.8"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
  >
    <path v-for="(d, index) in paths" :key="index" :d="d" />
  </svg>
</template>
