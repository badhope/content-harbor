import axios from 'axios'
import { ElMessage } from 'element-plus'

// hub_router 挂载在 /api/v1/hub (api/hub.py L56)，前后端路径必须对齐。
// 桌面模式：读 window.__TAURI_PORT__（等 sidecar 注入后动态获取）
// Web 模式：与后端同域，直接用 /api/v1/hub
function getBaseURL() {
  if (window.__TAURI_PORT__) {
    return `http://127.0.0.1:${window.__TAURI_PORT__}/api/v1/hub`
  }
  return '/api/v1/hub'
}

// 部署在外网/容器时后端要求 X-API-Token；令牌只从本机浏览器存储读取，
// 不写进构建产物，也不放进 URL。
const TOKEN_KEY = 'harbour_api_token'

export function getApiToken() {
  try {
    return window.localStorage.getItem(TOKEN_KEY) || ''
  } catch {
    return ''
  }
}

export function setApiToken(token) {
  try {
    if (token) window.localStorage.setItem(TOKEN_KEY, token)
    else window.localStorage.removeItem(TOKEN_KEY)
  } catch { /* 隐私模式下忽略 */ }
}

const http = axios.create({
  baseURL: '/api/v1/hub',
  timeout: 120000   // 发布/更新要开浏览器，慢是正常的
})

// 每次请求前动态设置 baseURL（处理 Tauri 端口延迟注入）并附带令牌
http.interceptors.request.use(config => {
  config.baseURL = getBaseURL()
  const token = getApiToken()
  if (token) config.headers['X-API-Token'] = token
  return config
})

// 重试配置：网络错误 / 5xx 时自动重试
const RETRYABLE = [408, 429, 500, 502, 503, 504]
const MAX_RETRIES = 3

http.interceptors.response.use(
  r => r.data,
  async err => {
    const cfg = err.config
    const status = err.response?.status
    // 审计 B3：只有幂等的 GET 允许自动重试；POST（发布/登录/同步）重试会重复建任务
    const method = (cfg.method || 'get').toLowerCase()
    const retryable = method === 'get' && RETRYABLE.includes(status)
    if (retryable && (!cfg._retry || cfg._retry < MAX_RETRIES)) {
      cfg._retry = (cfg._retry || 0) + 1
      await new Promise(r => setTimeout(r, 500 * cfg._retry))
      return http(cfg)
    }
    // 后端不可达（桌面端可能 sidecar 还没起来）→ 显示明确提示
    if (!err.response && window.__TAURI_PORT__) {
      ElMessage.error('后端服务未启动，请检查桌面端 sidecar')
    }
    // 后端自定义异常包是 {code, message, success, data}；FastAPI 默认是 {detail}
    const d = err.response?.data
    const msg = d?.message || d?.detail || err.message || '请求失败'
    ElMessage.error(typeof msg === 'string' ? msg : JSON.stringify(msg))
    return Promise.reject(err)
  }
)

export default http

// 所有长任务统一走任务引擎：提交返回 {task_id}，轮询 task() 看进度。
// 登录、发布、更新、同步、抓取都这么用，前端不再有第二套轮询逻辑。
export const api = {
  // ---------------- 总览 ----------------
  status: () => http.get('/status'),
  platforms: () => http.get('/platforms'),
  accounts: () => http.get('/accounts'),
  checkAccount: (platform, account = 'default') =>
    http.get(`/accounts/${platform}/check`, { params: { account } }),

  // ---------------- 账号 / 登录（统一任务语义） ----------------
  startLogin: (platform, account = 'default', opts = {}) =>
    http.post(`/accounts/${platform}/login`, {
      account,
      timeout: opts.timeout || 300,
      on_captcha: opts.onCaptcha || 'handoff',
    }),
  assistOpen: (platform, url) =>
    http.post(`/accounts/${platform}/assist`, { url }),
  refresh: (platform) => http.post(`/refresh/${platform}`),
  logout: (platform, account = 'default') =>
    http.delete(`/accounts/${platform}`, { params: { account } }),
  diagnose: (platform) => http.get(`/accounts/${platform}/diagnose`),
  bootstrapCnblogs: (username, password) =>
    http.post('/accounts/cnblogs/bootstrap', { username, password }),
  bootstrapCnblogsJob: (jobId) =>
    http.get(`/accounts/cnblogs/bootstrap/${jobId}`),

  // ---------------- 统一任务查询 ----------------
  tasks: (limit = 50, kind, status) =>
    http.get('/tasks', { params: { limit, kind, status } }),
  tasksPage: (params = {}) => http.get('/tasks/page', { params }),
  task: (taskId) => http.get(`/tasks/${taskId}`),
  resumeTask: (taskId, approved = true) =>
    http.post(`/tasks/${taskId}/resume`, { approved }),

  // ---------------- 文章 ----------------
  listArticles: (status) => http.get('/articles', { params: { status } }),
  articlesPage: (params = {}) => http.get('/articles/page', { params }),
  getArticle: (id) => http.get(`/articles/${id}`),
  createArticle: (data) => http.post('/articles', data),
  updateArticle: (id, data) => http.put(`/articles/${id}`, data),
  search: (kw) => http.get(`/articles/search/${kw}`),

  // ---------------- 发布 / 更新 / 同步（统一任务语义） ----------------
  // humanConfirmed：AI 稿「我已核验」显式确认；mode：auto 全自动 / assist 中断改配置 / manual 全手动
  publish: (id, platforms, draftOnly = false, account = 'default', settings = {},
            humanConfirmed = false, mode = 'auto') =>
    http.post(`/articles/${id}/publish`,
              { platforms, draft_only: draftOnly, account, settings,
                human_confirmed: humanConfirmed, mode }),
  update: (id, platforms, account = 'default') =>
    http.post(`/articles/${id}/update`, { platforms, account }),
  syncPending: (account = 'default') =>
    http.post('/sync/pending', { account }),

  publications: (articleId) => http.get('/publications', { params: { article_id: articleId } }),
  // 需要人工处理的发布实例（掘金草稿等人点"确定并发布"）
  pendingHuman: () => http.get('/pending-human'),

  // ---------------- AI Provider / 模型选择 ----------------
  aiProviders: () => http.get('/ai/providers'),
  // ---------------- AI 配置（密钥 / 模型 / 路由 / 门禁开关） ----------------
  aiConfig: () => http.get('/ai/config'),
  aiConfigSave: (patch) => http.put('/ai/config', patch),
  aiConfigTest: (body) => http.post('/ai/config/test', body),
  aiGate: () => http.get('/ai/gate'),

  // ---------------- AI 写稿 ----------------
  aiWrite: (data) => http.post('/ai/write', data),
  aiRewrite: (id, instruction, publishTo, model, provider) =>
    http.post(`/articles/${id}/ai-rewrite`, {
      instruction, publish_to: publishTo,
      model: model || '', preferred_provider: provider || ''
    }),
  aiPolish: (id, model, provider) =>
    http.post(`/articles/${id}/ai-polish`, {
      model: model || '', preferred_provider: provider || ''
    }),
  aiStatus: () => http.get('/ai/status'),

  // ---------------- AI 增强工具 ----------------
  aiTranslate: (id, lang = 'en', model, provider) => http.post(`/articles/${id}/ai-translate`, {
    model: model || '', preferred_provider: provider || ''
  }, { params: { target_lang: lang } }),
  aiImagePrompts: (id, n = 3, model, provider) => http.get(`/articles/${id}/ai-image-prompts`, {
    params: { n, model: model || undefined, preferred_provider: provider || undefined }
  }),
  aiOutline: (id) => http.get(`/articles/${id}/ai-outline`),
  aiSeo: (id) => http.get(`/articles/${id}/ai-seo`),
  cloneArticle: (id) => http.post(`/articles/${id}/clone`),
  aiTemplates: () => http.get('/ai/templates'),
  aiWriteTemplate: (data) => http.post('/ai/write-template', data),

  // ---------------- 版本历史 ----------------
  versions: (id, limit = 50) => http.get(`/articles/${id}/versions`, { params: { limit } }),
  version: (id, vid) => http.get(`/articles/${id}/versions/${vid}`),
  versionDiff: (id, v1, v2) => http.get(`/articles/${id}/versions/diff`, { params: { v1, v2 } }),
  rollback: (id, vid) => http.post(`/articles/${id}/versions/${vid}/rollback`),

  // ---------------- 定时发布调度器 ----------------
  schedules: (includeDisabled = false) => http.get('/schedules', { params: { include_disabled: includeDisabled } }),
  createSchedule: (data) => http.post('/schedules', data),
  updateSchedule: (sid, data) => http.put(`/schedules/${sid}`, data),
  pauseSchedule: (sid) => http.post(`/schedules/${sid}/pause`),
  resumeSchedule: (sid) => http.post(`/schedules/${sid}/resume`),
  triggerSchedule: (sid) => http.post(`/schedules/${sid}/trigger`),
  deleteSchedule: (sid) => http.delete(`/schedules/${sid}`),

  // ---------------- 标签治理 ----------------
  tags: (limit = 50, category) => http.get('/tags', { params: { limit, category } }),
  tagsTrending: (limit = 10) => http.get('/tags/trending', { params: { limit } }),
  syncTags: () => http.post('/tags/sync'),
  renameTag: (oldName, newName) => http.post('/tags/rename', null, { params: { old: oldName, new: newName } }),
  mergeTag: (from, to) => http.post('/tags/merge', null, { params: { _from: from, to } }),
  addTagAlias: (alias, canonical) => http.post('/tags/alias', null, { params: { alias, canonical } }),
  suggestTags: (title, contentMd) => http.post('/tags/suggest', null, { params: { title, content_md: contentMd } }),

  jobs: () => http.get('/jobs'),

  // ---------------- 媒体资产（图片入库 / 本地托管） ----------------
  mediaUpload: (file) => {
    const fd = new FormData()
    fd.append('file', file)
    return http.post('/media/upload', fd, { headers: { 'Content-Type': 'multipart/form-data' } })
  },
  mediaIngest: (url) => http.post('/media/ingest', { url }),
  mediaList: (limit = 100) => http.get('/media', { params: { limit } }),
  mediaStats: () => http.get('/media/stats'),

  // ---------------- 导入（本地 Markdown / 网页 URL） ----------------
  importArticle: (body) => http.post('/articles/import', body),

  // ---------------- 内容质检 ----------------
  qaArticle: (aid) => http.post(`/articles/${aid}/qa`),
  qaAnalyze: (contentMd, title, summary, tags) =>
    http.post('/qa/analyze', { content_md: contentMd, title, summary, tags }),

  // ---------------- Webhook / 事件通知 ----------------
  webhooks: () => http.get('/webhooks'),
  createWebhook: (data) => http.post('/webhooks', data),
  updateWebhook: (wid, data) => http.put(`/webhooks/${wid}`, data),
  deleteWebhook: (wid) => http.delete(`/webhooks/${wid}`),
  testWebhook: (wid) => http.post(`/webhooks/${wid}/test`),
  notifications: (since, limit = 50) =>
    http.get('/notifications', { params: { since, limit } })
}
