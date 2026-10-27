import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('@/api', () => ({
  api: {
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
