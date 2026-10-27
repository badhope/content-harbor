import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('@/api', () => ({
  api: {
    listArticles: vi.fn().mockResolvedValue([]),
    getArticle: vi.fn().mockResolvedValue({}),
    createArticle: vi.fn().mockResolvedValue({ id: 1 }),
    updateArticle: vi.fn().mockResolvedValue({}),
    publications: vi.fn().mockResolvedValue([]),
    allPublications: vi.fn().mockResolvedValue([]),
    status: vi.fn().mockResolvedValue({ accounts: [] }),
    platforms: vi.fn().mockResolvedValue([]),
    pendingHuman: vi.fn().mockResolvedValue([]),
    publish: vi.fn().mockResolvedValue({ task_id: 'tk' }),
    task: vi.fn().mockResolvedValue({ status: 'ok' }),
    tasks: vi.fn().mockResolvedValue([]),
    listTasks: vi.fn().mockResolvedValue([]),
    aiStatus: vi.fn().mockResolvedValue({ ready: false, providers: [] }),
    tags: vi.fn().mockResolvedValue([]),
    schedules: vi.fn().mockResolvedValue([]),
    webhooks: vi.fn().mockResolvedValue([]),
    notifications: vi.fn().mockResolvedValue([])
  },
  default: {}
}))

import { api } from '@/api'
import { useHubStore } from '@/stores/hub'
import { useArticleStore } from '@/stores/article'
import { useTaskStore } from '@/stores/task'

describe('hub store publish（审计 B2）', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  const stubTask = () => {
    const task = useTaskStore()
    vi.spyOn(task, '_waitTask').mockResolvedValue({ status: 'ok' })
    return task
  }

  it('已有文章 dirty 时必须先保存再发布，否则会发旧稿', async () => {
    const hub = useHubStore()
    const article = useArticleStore()
    article.current = { id: 3, title: '改过的标题', content_md: 'body' }
    article.currentId = 3
    article.dirty = true
    const saveSpy = vi.spyOn(article, 'save').mockImplementation(async () => {
      article.dirty = false
    })
    stubTask()

    await hub.publish(['cnblogs'], true, {}, true, 'auto')

    expect(saveSpy).toHaveBeenCalledTimes(1)
    expect(api.publish).toHaveBeenCalledTimes(1)
  })

  it('未改动的已有文章不重复保存', async () => {
    const hub = useHubStore()
    const article = useArticleStore()
    article.current = { id: 3, title: 't', content_md: 'b' }
    article.currentId = 3
    article.dirty = false
    const saveSpy = vi.spyOn(article, 'save')
    stubTask()

    await hub.publish(['cnblogs'], true, {}, true, 'auto')

    expect(saveSpy).not.toHaveBeenCalled()
    expect(api.publish).toHaveBeenCalledTimes(1)
  })

  it('新文章（无 currentId）会先保存再发布', async () => {
    const hub = useHubStore()
    const article = useArticleStore()
    article.current = { title: 'new', content_md: '' }
    article.currentId = null
    const saveSpy = vi.spyOn(article, 'save').mockImplementation(async () => {
      article.currentId = 11
    })
    stubTask()

    await hub.publish(['cnblogs'], true, {}, true, 'auto')

    expect(saveSpy).toHaveBeenCalledTimes(1)
    expect(api.publish).toHaveBeenCalled()
  })
})
