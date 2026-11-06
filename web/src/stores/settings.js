import { defineStore } from 'pinia'
import { api } from '@/api'

/**
 * 设置 store：AI 配置（密钥/模型/路由/门禁）为主，供设置页与发布轨共用。
 * 后端 GET /ai/config 永远只回掩码，明文密钥只在 PUT 时上行、绝不回读。
 */
export const useSettingsStore = defineStore('settings', {
  state: () => ({
    aiConfig: null,     // {providers[], routing{}, review{}, legacy{}, style{}, ready}
    aiGate: null,       // /ai/gate 快照（高危词数/最近拒绝）
    loading: false,
    loadError: '',      // 读取失败的可见原因（空串=无错误）
    saving: false,
    testing: '',        // 正在测连通的 provider key
  }),

  getters: {
    providers: (s) => s.aiConfig?.providers || [],
    ready: (s) => !!s.aiConfig?.ready,
    routing: (s) => s.aiConfig?.routing || {},
    review: (s) => s.aiConfig?.review || {},
    directPublish: (s) => !!s.aiConfig?.review?.ai_direct_publish,
  },

  actions: {
    async loadAI() {
      this.loading = true
      this.loadError = ''
      try {
        this.aiConfig = await api.aiConfig()
      } catch (e) {
        // 以前这里静默吞掉异常：后端不可达时设置页只是一片空白，
        // 用户无法区分"没有 provider"和"请求失败"。现在把原因带给界面。
        this.loadError = e?.message || '网络错误'
      }
      try {
        this.aiGate = await api.aiGate()
      } catch { this.aiGate = null }
      finally { this.loading = false }
    },

    /** 白名单写回；返回掩码后的新配置 */
    async saveAI(patch) {
      this.saving = true
      try {
        this.aiConfig = await api.aiConfigSave(patch)
        return this.aiConfig
      } finally { this.saving = false }
    },

    /** 连通性测试（可带未保存的 draft 字段）；返回 {ok, latency_ms, model, error} */
    async testProvider(body) {
      this.testing = body.provider
      try {
        return await api.aiConfigTest(body)
      } finally { this.testing = '' }
    },
  }
})
