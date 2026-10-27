import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('@/api', () => ({
  api: {
    articlesPage: vi.fn(),
    listArticles: vi.fn(),
    getArticle: vi.fn(),
    createArticle: vi.fn(),
    updateArticle: vi.fn(),
    publications: vi.fn(),
    status: vi.fn(),
    publish: vi.fn(),
    task: vi.fn()
  },
  default: {}
}))

import { api } from '@/api'
import { useArticleStore } from '@/stores/article'

describe('article store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('markDirty 能真正把 dirty 置为 true（审计 B7：写只读 getter 会静默失败）', () => {
    const s = useArticleStore()
    expect(s.dirty).toBe(false)
    s.markDirty()
    expect(s.dirty).toBe(true)
  })

  it('save 对已有文章走 updateArticle 而非 createArticle', async () => {
    const s = useArticleStore()
    s.current = { id: 7, title: 'x', content_md: 'y' }
    s.currentId = 7
    s.dirty = true
    api.updateArticle.mockResolvedValue({ ok: true })
    api.listArticles.mockResolvedValue([])
    api.publications.mockResolvedValue([])
    api.status.mockResolvedValue({ accounts: [] })
    await s.save()
    expect(api.updateArticle).toHaveBeenCalledTimes(1)
    expect(api.createArticle).not.toHaveBeenCalled()
    expect(s.dirty).toBe(false)
  })

  it('save 成功后不抛 ReferenceError（审计 B1：useHubStore 缺 import）', async () => {
    const s = useArticleStore()
    s.current = { title: 'new', content_md: '' }
    s.currentId = null
    api.createArticle.mockResolvedValue({ id: 9 })
    api.listArticles.mockResolvedValue([])
    api.publications.mockResolvedValue([])
    api.status.mockResolvedValue({ accounts: [] })
    await expect(s.save()).resolves.toBeUndefined()
  })
})

describe('article store 分页（R4：列表不得静默截断）', () => {
  const page = (ids, total, hasMore) => ({
    items: ids.map(id => ({ id, title: `文${id}` })),
    total, has_more: hasMore
  })

  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('loadList 取第一页并记录 total/hasMore', async () => {
    api.articlesPage.mockResolvedValue(page([3, 2], 3, true))
    const s = useArticleStore()
    await s.loadList()
    expect(api.articlesPage).toHaveBeenCalledWith({ limit: 50, offset: 0, status: undefined })
    expect(s.articles.map(a => a.id)).toEqual([3, 2])
    expect(s.total).toBe(3)
    expect(s.hasMore).toBe(true)
  })

  it('loadMore 以已加载条数为 offset 追加，且去重', async () => {
    api.articlesPage
      .mockResolvedValueOnce(page([3, 2], 3, true))
      .mockResolvedValueOnce(page([2, 1], 3, false))
    const s = useArticleStore()
    await s.loadList()
    await s.loadMore()
    expect(api.articlesPage).toHaveBeenLastCalledWith({ limit: 50, offset: 2, status: undefined })
    expect(s.articles.map(a => a.id)).toEqual([3, 2, 1])
    expect(s.hasMore).toBe(false)
  })

  it('hasMore=false 时 loadMore 不再请求', async () => {
    api.articlesPage.mockResolvedValue(page([1], 1, false))
    const s = useArticleStore()
    await s.loadList()
    await s.loadMore()
    expect(api.articlesPage).toHaveBeenCalledTimes(1)
  })
})
