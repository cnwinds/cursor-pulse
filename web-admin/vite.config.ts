import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'
import { resolveBuildInfo } from './build-info'

export default defineConfig(({ mode, command }) => ({
  base: mode === 'production' ? '/admin/' : '/',
  plugins: [vue()],
  define: {
    __APP_BUILD__: JSON.stringify(resolveBuildInfo(command === 'serve')),
  },
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  // 概览 / 用量分析懒加载时会拉 vue-echarts + echarts；预构建可避免 dev 下
  // 「Failed to fetch dynamically imported module …DashboardView.vue」类错误。
  optimizeDeps: {
    include: [
      'vue-echarts',
      'echarts/core',
      'echarts/charts',
      'echarts/components',
      'echarts/renderers',
    ],
  },
  // Ship into the Python package so `pip install` / Docker share one path.
  build: {
    outDir: fileURLToPath(new URL('../pulse/web/static', import.meta.url)),
    emptyOutDir: true,
  },
  server: {
    host: '0.0.0.0',
    port: 5173,
    // 局域网用 IP 访问时（如 http://192.168.x.x:5173），可设 VITE_DEV_HMR_HOST=该 IP
    hmr: process.env.VITE_DEV_HMR_HOST
      ? { host: process.env.VITE_DEV_HMR_HOST, clientPort: 5173 }
      : undefined,
    proxy: {
      '/api': 'http://127.0.0.1:8080',
      '/health': 'http://127.0.0.1:8080',
    },
  },
}))
