import { defineStore } from 'pinia'
import { ElNotification } from 'element-plus'
import { api } from '@/api'

// 令牌只从本机浏览器存储读取；单独取值，避免测试 mock 必须导出它。
function readToken() {
  try {
    return window.localStorage.getItem('harbour_api_token') || ''
  } catch {
    return ''
  }
}

/** SSE 替代 polling 的任务状态管理。 */
export const useTaskStore = defineStore('task', {
  state: () => ({
    tasks: [],
    total: 0,
    truncated: false,   // 首页条数 < 总数时说明还有更老的没加载
    _prevWaiting: [],   // 上一轮 waiting_human 快照，用于只弹新增通知
    loginTasks: {},   // platform -> {task_id, status, message}
    _es: null,         // EventSource
    _lastEventTs: 0,
  }),

  getters: {
    waitingTasks: (s) => s.tasks.filter(t => t.status === 'waiting_human'),
  },

  actions: {
    // 初始化时拉一次全量，后续走 SSE 增量
    async loadTasks(notify = false) {
      try {
        const limit = 200
        const [r, waiting] = await Promise.all([
          api.tasksPage({ limit, offset: 0 }),
          // 待人工任务必须单独查：它可能排在第 200 条之外，只翻首页会漏掉角标和待办
          api.tasksPage({ limit: 200, offset: 0, status: 'waiting_human' })
        ])
        const list = r?.items || []
        const waitingList = waiting?.items || []
        this.total = r?.total ?? list.length
        this.truncated = !!r?.has_more
        const known = new Set(list.map(t => t.task_id))
        // 首页之外还有 waiting_human 时补进来，保证 waitingTasks getter 完整
        this.tasks = list.concat(waitingList.filter(t => !known.has(t.task_id)))
        if (notify) {
          const prevWaiting = new Set(this._prevWaiting || [])
          for (const t of this.tasks.filter(x => x.status === 'waiting_human' && !prevWaiting.has(x.task_id))) {
            ElNotification({ title: '任务等待人工处理', message: `${t.kind} 任务「${t.message || '挂起中'}」→ 到任务中心处理`, type: 'warning', duration: 0 })
          }
        }
        this._prevWaiting = this.tasks.filter(t => t.status === 'waiting_human').map(t => t.task_id)
      } catch { /* 轮询失败静默 */ }
    },

    async resumeTask(taskId, approved = true) {
      await api.resumeTask(taskId, approved)
      await Promise.all([this.loadTasks()])
    },

    // SSE 长连接：后端推送新事件时更新 tasks
    connectSSE() {
      if (this._es) return
      // EventSource 不能带自定义请求头，改用 fetch 流以携带 X-API-Token。
      const base = window.__TAURI_PORT__
        ? `http://127.0.0.1:${window.__TAURI_PORT__}/api/v1/hub`
        : '/api/v1/hub'
      const controller = new AbortController()
      this._es = { abort: () => controller.abort() }
      const headers = {}
      const token = readToken()
      if (token) headers['X-API-Token'] = token
      const pump = async () => {
        try {
          const resp = await fetch(`${base}/events/stream`, {
            headers, signal: controller.signal,
          })
          if (!resp.ok || !resp.body) throw new Error(`SSE ${resp.status}`)
          const reader = resp.body.getReader()
          const decoder = new TextDecoder()
          let buf = ''
          for (;;) {
            const { value, done } = await reader.read()
            if (done) throw new Error('SSE stream ended')
            buf += decoder.decode(value, { stream: true })
            const chunks = buf.split('\n\n')
            buf = chunks.pop() || ''
            for (const chunk of chunks) {
              const dataLine = chunk.split('\n').find(l => l.startsWith('data:'))
              if (!dataLine) continue
              try {
                const evt = JSON.parse(dataLine.slice(5).trim())
                this._lastEventTs = Date.now()
                if (evt.type?.startsWith('task.') || evt.type?.startsWith('publish.')) {
                  this.loadTasks(/*notify*/ true)
                }
              } catch { /* ignore parse error */ }
            }
          }
        } catch (e) {
          if (controller.signal.aborted) return
          this._es = null
          if (!this._pollTimer) {
            this._pollTimer = setInterval(() => this.loadTasks(true), 30000)
          }
        }
      }
      pump()
    },

    disconnectSSE() {
      if (this._es) { this._es.abort(); this._es = null }
      if (this._pollTimer) { clearInterval(this._pollTimer); this._pollTimer = null }
    },

    // ---------------- 登录 ----------------
    async startLogin(platform) {
      if (this.loginTasks[platform]?.status === 'running') return
      const t = await api.startLogin(platform)
      this.loginTasks[platform] = { task_id: t.task_id, status: 'running', message: '等待扫码…' }
      this._pollLogin(t.task_id, platform)
    },

    // 轮询任务到终态（兜底路径；SSE 可用时由 connectSSE 处理增量推送）
    async _waitTask(taskId, deadlineMs = 600000) {
      const deadline = Date.now() + deadlineMs
      while (Date.now() < deadline) {
        const t = await api.task(taskId)
        if (t.status === 'ok' || t.status === 'failed' || t.status === 'waiting_human') return t
        await new Promise(res => setTimeout(res, 2000))
      }
      throw new Error(`任务超时（${deadlineMs / 1000}s），可到任务中心继续查看`)
    },

    _pollLogin(taskId, platform) {
      // 登录通常很快（扫码），1 秒一次轮询；
      // 接了 SSE 后这条路径会被 onmessage 替代
      api.task(taskId).then(st => {
        this.loginTasks[platform] = { task_id: taskId, ...st }
        if (st.status === 'ok') {
          const hub = useHubStore()
          hub.loadStatus()
          hub.loadPlatforms()
        } else if (st.status === 'running' || st.status === 'pending') {
          setTimeout(() => this._pollLogin(taskId, platform), 1000)
        }
      }).catch(() => {
        this.loginTasks[platform] = { task_id: taskId, status: 'failed', message: '登录任务超时' }
      })
    },
  }
})

// 延迟引用，避免循环依赖
import { useHubStore } from './hub'
