import { defineStore } from 'pinia'
import { ElMessageBox, ElMessage } from 'element-plus'
import { api } from '@/api'
import { useHubStore } from './hub'

// 草稿自动保存：每 30 秒把 dirty 文章内容写 localStorage，防崩溃/断电丢稿
const DRAFT_KEY_PREFIX = 'harbor_draft_'
let _autoSaveTimer = null

function _draftKey(id) { return DRAFT_KEY_PREFIX + (id || 'new') }

function _saveDraftLocal(state) {
  if (!state.dirty || !state.current) return
  try {
    localStorage.setItem(_draftKey(state.currentId), JSON.stringify({
      title: state.current.title, content_md: state.current.content_md,
      summary: state.current.summary, tags: state.current.tags,
      cover: state.current.cover, ai_model: state.current.ai_model,
      saved_at: Date.now()
    }))
  } catch { /* localStorage 满了不影响主流程 */ }
}

function _loadDraftLocal(id) {
  try {
    const raw = localStorage.getItem(_draftKey(id))
    return raw ? JSON.parse(raw) : null
  } catch { return null }
}

function _clearDraft(id) {
  try { localStorage.removeItem(_draftKey(id)) } catch { /* ignore */ }
}

/** 文章 CRUD、版本管理、AI 增强 */
export const useArticleStore = defineStore('article', {
  state: () => ({
    articles: [],
    currentId: null,
    _openGen: 0,
    current: null,
    publications: [],
    allPubs: [],
    keyword: '',
    filterStatus: '',
    dirty: false,
    saving: false,
    loadingList: false,
    total: 0,
    hasMore: false,
    loadingMore: false,
    serverOffset: 0,   // 已消费的服务端行数；不能用去重后的本地条数，否则会漂移
    _listGen: 0,       // 列表请求代际，避免旧响应覆盖新列表
    PAGE_SIZE: 50,
  }),

  getters: {
    filtered(s) {
      const kw = s.keyword.trim().toLowerCase()
      return s.articles.filter(a => {
        if (s.filterStatus && a.status !== s.filterStatus) return false
        if (!kw) return true
        return (a.title || '').toLowerCase().includes(kw) ||
               (a.summary || '').toLowerCase().includes(kw)
      })
    },
    currentPubs: (s) => s.publications,
    updatable: (s) => s.publications.filter(p => p.post_id).map(p => p.platform),
  },

  actions: {
    async loadList() {
      const gen = ++this._listGen
      this.loadingList = true
      try {
        const r = await api.articlesPage({
          limit: this.PAGE_SIZE, offset: 0, status: this.filterStatus || undefined
        }) || {}
        if (gen !== this._listGen) return
        this.articles = r.items || []
        this.serverOffset = this.articles.length
        this.total = r.total ?? this.articles.length
        this.hasMore = !!r.has_more
      } finally {
        if (gen === this._listGen) this.loadingList = false
      }
    },

    async loadMore() {
      if (this.loadingMore || !this.hasMore) return
      const gen = this._listGen
      this.loadingMore = true
      try {
        const r = await api.articlesPage({
          limit: this.PAGE_SIZE, offset: this.serverOffset,
          status: this.filterStatus || undefined
        }) || {}
        // 期间列表被重置（保存/刷新/切筛选）就丢弃这次结果，否则会拼到新列表上
        if (gen !== this._listGen) return
        const items = r.items || []
        const known = new Set(this.articles.map(a => a.id))
        this.articles = this.articles.concat(items.filter(a => !known.has(a.id)))
        // offset 以服务端消费行数为准；即使有条目被去重也要前进，否则永远卡住
        this.serverOffset += items.length
        this.total = r.total ?? this.total
        this.hasMore = !!r.has_more && items.length > 0
      } finally { this.loadingMore = false }
    },

    async open(id) {
      if (this.dirty) {
        try {
          await ElMessageBox.confirm(
            '当前文章有未保存的改动，切换会丢失这些改动。确定切换吗？',
            '未保存的改动', { type: 'warning', confirmButtonText: '确定切换', cancelButtonText: '留在这' }
          )
        } catch { return }
      }
      const gen = ++this._openGen
      try { this.current = await api.getArticle(id) } catch { return }
      if (!this.current || gen !== this._openGen) return
      this.currentId = id
      await this.loadPubs()
      if (gen === this._openGen) this.dirty = false
    },

    async loadPubs() {
      const id = this.currentId
      const pubs = id ? (await api.publications(id) || []) : []
      if (id !== this.currentId) return
      this.publications = pubs
    },

    /** 删除文章。已发布到平台的副本不受影响，只清本地账本。 */
    async remove(id) {
      const target = this.articles.find(a => a.id === id)
      const label = target?.title || `#${id}`
      try {
        await ElMessageBox.confirm(
          `删除《${label}》？本地版本历史与发布记录一并清除；平台侧已发布的文章不会删除。`,
          '删除文章',
          { type: 'warning', confirmButtonText: '永久删除本地记录', cancelButtonText: '保留',
            confirmButtonClass: 'el-button--danger' }
        )
      } catch { return false }

      await api.deleteArticle(id)
      this.articles = this.articles.filter(a => a.id !== id)
      this.total = Math.max(0, this.total - 1)
      this.serverOffset = Math.max(0, this.serverOffset - 1)
      if (this.currentId === id) {
        this._openGen++
        this.currentId = null
        this.current = null
        this.publications = []
        this.dirty = false
      }
      ElMessage.success('已删除')
      return true
    },


    async loadAllPubs() {
      try { this.allPubs = await api.publications() || [] }
      catch { /* 静默降级 */ }
    },

    newArticle() {
      this._openGen++
      this.currentId = null
      this.current = { title: '未命名文章', content_md: '', summary: '', tags: '',
                       cover: '', ai_model: '', status: 'draft', source: 'human' }
      this.publications = []
      this.dirty = true
    },

    markDirty() {
      this.dirty = true
      // 防抖自动保存：每次标记 dirty 后 30 秒写一次 localStorage
      clearTimeout(_autoSaveTimer)
      _autoSaveTimer = setTimeout(() => _saveDraftLocal(this.$state), 30000)
    },

    /** 检查是否有未恢复的草稿，有则返回草稿内容 */
    peekDraft(id) {
      return _loadDraftLocal(id)
    },

    /** 清除已保存文章对应的草稿（保存成功后调用） */
    clearDraft(id) {
      _clearDraft(id)
    },

    async save() {
      if (!this.current) return
      this.saving = true
      try {
        if (this.currentId) {
          await api.updateArticle(this.currentId, {
            title: this.current.title, content_md: this.current.content_md,
            summary: this.current.summary, tags: this.current.tags, status: this.current.status,
            // 发布设置：封面 / AI 辅助标识也要随保存提交，否则发布轨读到的是旧值
            cover: this.current.cover || '', ai_model: this.current.ai_model || '',
          })
        } else {
          const r = await api.createArticle({ ...this.current, source: 'human' })
          this.currentId = r.id
          this.current = { ...this.current, id: r.id }
        }
        this.dirty = false
        _clearDraft(this.currentId)
        ElMessage.success('已保存')
        await Promise.all([this.loadList(), useHubStore().loadStatus(), this.loadPubs()])
      } finally { this.saving = false }
    },

    async search(kw) {
      return await api.search(kw)
    },

    // ---------------- AI ----------------
    async aiTranslate(id, lang = 'en', model, provider) {
      const r = await api.aiTranslate(id, lang, model, provider)
      ElMessage.success(`已翻译为 ${lang}（${r.chars} 字）`)
      return r
    },
    async aiImagePrompts(id, n = 3, model, provider) { return await api.aiImagePrompts(id, n, model, provider) },
    async aiOutline(id) { return await api.aiOutline(id) },
    async aiSeo(id) { return await api.aiSeo(id) },
    async cloneArticle(id) {
      const r = await api.cloneArticle(id)
      ElMessage.success(`已克隆为「${r.title}」（#${r.new_id}）`)
      await this.loadList()
      return r
    },

    // ---------------- 版本历史 ----------------
    async loadVersions(aid) { return await api.versions(aid) },
    async rollbackVersion(aid, vid) {
      const r = await api.rollback(aid, vid)
      ElMessage.success(r.message || '已回滚')
      await this.open(aid)
      return r
    },
    async versionDiff(aid, v1, v2) { return await api.versionDiff(aid, v1, v2) },

    // ---------------- 质检 ----------------
    async qaArticle(aid) { return await api.qaArticle(aid) },
    async qaAnalyze(contentMd, title, summary, tags) { return await api.qaAnalyze(contentMd, title, summary, tags) },
  }
})
