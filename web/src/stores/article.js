import { defineStore } from 'pinia'
import { ElMessageBox, ElMessage } from 'element-plus'
import { api } from '@/api'
import { useHubStore } from './hub'

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
      this.loadingList = true
      try {
        const r = await api.articlesPage({
          limit: this.PAGE_SIZE, offset: 0, status: this.filterStatus || undefined
        }) || {}
        this.articles = r.items || []
        this.total = r.total || this.articles.length
        this.hasMore = !!r.has_more
      } finally { this.loadingList = false }
    },

    async loadMore() {
      if (this.loadingMore || !this.hasMore) return
      this.loadingMore = true
      try {
        const r = await api.articlesPage({
          limit: this.PAGE_SIZE, offset: this.articles.length,
          status: this.filterStatus || undefined
        }) || {}
        const known = new Set(this.articles.map(a => a.id))
        this.articles = this.articles.concat((r.items || []).filter(a => !known.has(a.id)))
        this.total = r.total || this.total
        this.hasMore = !!r.has_more
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

    async loadAllPubs() {
      try { this.allPubs = await api.publications() || [] }
      catch { /* 静默降级 */ }
    },

    newArticle() {
      this._openGen++
      this.currentId = null
      this.current = { title: '未命名文章', content_md: '', summary: '', tags: '', status: 'draft', source: 'human' }
      this.publications = []
      this.dirty = true
    },

    markDirty() { this.dirty = true },

    async save() {
      if (!this.current) return
      this.saving = true
      try {
        if (this.currentId) {
          await api.updateArticle(this.currentId, {
            title: this.current.title, content_md: this.current.content_md,
            summary: this.current.summary, tags: this.current.tags, status: this.current.status,
          })
        } else {
          const r = await api.createArticle({ ...this.current, source: 'human' })
          this.currentId = r.id
          this.current = { ...this.current, id: r.id }
        }
        this.dirty = false
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
