import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

export default defineConfig(({ command }) => ({
  plugins: [vue()],

  // build 产物落在 FastAPI 的 /static 下，所以静态资源必须带 /static/ 前缀，
  // 否则 index.html 会去请求 /assets/xxx.js —— 那个路径后端没挂，直接 404。
  base: command === 'build' ? '/static/' : '/',

  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) }
  },

  build: {
    outDir: '../server/static',
    emptyOutDir: true,
    chunkSizeWarningLimit: 1500,
    rollupOptions: {
      output: {
        manualChunks: {
          vue: ['vue', 'pinia', 'axios'],
          element: ['element-plus', '@element-plus/icons-vue'],
          md: ['md-editor-v3'],
          chart: ['echarts/core', 'echarts/charts', 'echarts/components', 'echarts/renderers']
        }
      }
    }
  },

  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['src/test/setup.js'],
    include: ['src/**/*.test.js']
  },

  server: {
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true }
    }
  }
}))
