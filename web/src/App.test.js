import { describe, it, expect, beforeEach, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('@/api', () => ({
  api: {
    listArticles: vi.fn().mockResolvedValue([]),
    articlesPage: vi.fn().mockResolvedValue({ items: [], total: 0, has_more: false }),
    getArticle: vi.fn().mockResolvedValue({}),
    createArticle: vi.fn(), updateArticle: vi.fn(),
    publications: vi.fn().mockResolvedValue([]), allPublications: vi.fn().mockResolvedValue([]),
    status: vi.fn().mockResolvedValue({ accounts: [] }), platforms: vi.fn().mockResolvedValue([]),
    pendingHuman: vi.fn().mockResolvedValue([]), publish: vi.fn(), task: vi.fn(),
    tasks: vi.fn().mockResolvedValue([]),
    tasksPage: vi.fn().mockResolvedValue({ items: [], total: 0, has_more: false }), aiStatus: vi.fn().mockResolvedValue({ ready: false, providers: [] }),
    tags: vi.fn().mockResolvedValue([]), schedules: vi.fn().mockResolvedValue([]),
    webhooks: vi.fn().mockResolvedValue([]), notifications: vi.fn().mockResolvedValue([])
  },
  default: {}
}))

import App from '@/App.vue'
import { useHubStore } from '@/stores/hub'

// 子视图全部替换为可识别的占位，避免引入 Element Plus / md-editor 重量级依赖
const stubs = {
  SideNav: { template: '<nav data-view="sidenav" />' },
  WriteView: { template: '<div data-view="write" />' },
  PublishCenterView: { template: '<div data-view="publish" />' },
  TasksView: { template: '<div data-view="tasks" />' },
  SettingsView: { template: '<div data-view="settings" />' },
  AIWriteDialog: { template: '<div />' },
  'el-config-provider': { template: '<div><slot /></div>' }
}

const mountApp = () => mount(App, { global: { stubs } })
const views = w => w.findAll('[data-view]').map(n => n.attributes('data-view')).filter(v => v !== 'sidenav')

describe('App 视图互斥渲染（回归：v-show 与 v-if 链并存导致叠加）', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it.each([
    ['write', 'write'],
    ['publish', 'publish'],
    ['tasks', 'tasks'],
    ['settings', 'settings']
  ])('view=%s 时只渲染 %s 一个视图', async (view, expected) => {
    const hub = useHubStore()
    hub.view = view
    const w = mountApp()
    await w.vm.$nextTick()
    expect(views(w)).toEqual([expected])
  })

  it('切换视图后不残留上一个视图 DOM', async () => {
    const hub = useHubStore()
    hub.view = 'write'
    const w = mountApp()
    await w.vm.$nextTick()
    expect(views(w)).toEqual(['write'])

    hub.view = 'publish'
    await w.vm.$nextTick()
    expect(views(w)).toEqual(['publish'])
    expect(w.find('[data-view="write"]').exists()).toBe(false)
  })
})

// 侧栏徽标是唯一把「全局计数」暴露给用户的入口，必须走服务端统计：
// 列表已分页，用 hub.articles 数出来的结果只有当前页。
const mountWithNav = () => mount(App, {
  global: {
    stubs: {
      ...stubs,
      SideNav: {
        props: ['view', 'aiReady', 'waitCount', 'publishedCount'],
        template: '<nav data-view="sidenav" :data-wait="waitCount" :data-pub="publishedCount" />'
      }
    }
  }
})

describe('App 侧栏计数口径', () => {
  // boot() 会拉真实数据覆盖测试设置；这里只测「计数怎么算」，把 boot 关掉
  let hub, article, account, task
  beforeEach(async () => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    hub = useHubStore()
    vi.spyOn(hub, 'boot').mockResolvedValue()
    const { useArticleStore } = await import('@/stores/article')
    const { useAccountStore } = await import('@/stores/account')
    const { useTaskStore } = await import('@/stores/task')
    article = useArticleStore()
    account = useAccountStore()
    task = useTaskStore()
  })

  it('已发布篇数取 stats.published_articles，而不是平台实例数 published', async () => {
    account.stats = { published: 9, published_articles: 3 }
    const w = mountWithNav()
    await w.vm.$nextTick()
    expect(w.find('[data-view="sidenav"]').attributes('data-pub')).toBe('3')
  })

  it('已发布篇数不依赖已加载的文章列表（分页后只剩当前页）', async () => {
    account.stats = { published_articles: 42 }
    article.articles = [{ id: 1, status: 'published' }, { id: 2, status: 'draft' }]
    const w = mountWithNav()
    await w.vm.$nextTick()
    expect(w.find('[data-view="sidenav"]').attributes('data-pub')).toBe('42')
  })

  it('待人工数 = pendingHuman + waitingTasks', async () => {
    account.pendingHuman = [{ platform: 'juejin' }, { platform: 'csdn' }]
    task.tasks = [{ task_id: 'a', status: 'waiting_human' }]
    const w = mountWithNav()
    await w.vm.$nextTick()
    expect(w.find('[data-view="sidenav"]').attributes('data-wait')).toBe('3')
  })
})
