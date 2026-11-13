import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 纯 vue + vite：不引入 konva/fabric 等重型画布库，画布用 DOM + CSS 绝对定位实现。
export default defineConfig({
  plugins: [vue()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
  },
  build: {
    outDir: 'dist',
    assetsDir: 'assets',
    chunkSizeWarningLimit: 900,
  },
})
