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
        // 只把"永远首屏需要"的框架拆出来做长期缓存；编辑器与图表交给
        // Rollup 按 dynamic import 自动分包。手工把 md-editor-v3 固定成
        // 一个 chunk 会把它与 element-plus 的共享依赖绑在一起，导致
        // index.html 里仍然 modulepreload 整个 900KB 编辑器——懒加载白做。
        manualChunks: {
          vue: ['vue', 'pinia', 'axios'],
          element: ['element-plus', '@element-plus/icons-vue']
        }
      }
    }
  },

  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['src/test/setup.js'],
    include: ['src/**/*.test.js'],
    // 默认 5s 对首次动态 import 的用例偏紧：机器负载高时（例如同时跑 569 个
    // 后端测试）单条用例的模块编译会被拖到 1.5s 以上，触发假失败。
    // 提到 15s 不影响"真卡死"的判断，只是给冷启动留出余量。
    testTimeout: 15000,
    hookTimeout: 15000
  },

  server: {
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true }
    }
  }
}))
