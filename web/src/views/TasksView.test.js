import { describe, it, expect, beforeEach, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('@/api', () => ({
  api: {
    tasksPage: vi.fn(),
    task: vi.fn(),
    resumeTask: vi.fn(),
    articlesPage: vi.fn().mockResolvedValue({ items: [], total: 0, has_more: false }),
    publications: vi.fn().mockResolvedValue([])
  },
  default: {}
}))

import { api } from '@/api'
import TasksView from '@/views/TasksView.vue'
import { useTaskStore } from '@/stores/task'

const task = (over = {}) => ({
  task_id: 'tk00000001', kind: 'publish', article_id: 7,
  platforms: ['juejin', 'csdn'], status: 'failed',
  message: '未分类错误（已重试 2 次）', error: 'GateError: 篇幅过短（<200字）',
  attempts: 2, updated_at: 1700000000, ...over
})

describe('TasksView 卡片列表（回归：表格在 390px 只剩三列）', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    api.tasksPage.mockResolvedValue({ items: [], total: 0, has_more: false })
  })

  it('渲染为卡片而不是表格，且平台名可读', async () => {
    const store = useTaskStore()
    store.tasks = [task()]
    store.total = 1
    const w = mount(TasksView)
    await w.vm.$nextTick()
    expect(w.findAll('.task-card').length).toBe(1)
    expect(w.find('.el-table').exists()).toBe(false)
    // 平台 id 应映射成可读名（平台列表为空时退回 id 本身，也不能是 JSON 原文）
    expect(w.find('.tc-target').text()).toContain('juejin')
    expect(w.find('.tc-target').text()).not.toContain('["')
  })

  it('platforms 是 JSON 字符串时也能渲染（后端老数据/降级路径）', async () => {
    const store = useTaskStore()
    store.tasks = [task({ platforms: '["juejin","csdn"]' })]
    const w = mount(TasksView)
    await w.vm.$nextTick()
    const txt = w.find('.tc-target').text()
    expect(txt).toContain('juejin')
    expect(txt).not.toContain('["')
  })

  it('卡片是可聚焦的按钮语义，Enter 打开详情', async () => {
    const store = useTaskStore()
    store.tasks = [task()]
    api.task.mockResolvedValue(task())
    const w = mount(TasksView)
    await w.vm.$nextTick()
    const card = w.find('.task-card')
    expect(card.attributes('role')).toBe('button')
    expect(card.attributes('tabindex')).toBe('0')
    await card.trigger('keydown', { key: 'Enter' })
    await w.vm.$nextTick()
    expect(api.task).toHaveBeenCalledWith('tk00000001')
  })

  it('等待人工的任务在卡片上直接给出操作', async () => {
    const store = useTaskStore()
    store.tasks = [task({ status: 'waiting_human', message: '需要人工过验证码' })]
    const w = mount(TasksView)
    await w.vm.$nextTick()
    const ops = w.find('.tc-ops')
    expect(ops.exists()).toBe(true)
    expect(ops.text()).toContain('已处理，继续')
  })

  it('失败任务显示错误摘要（message 优先，否则 error）', async () => {
    const store = useTaskStore()
    store.tasks = [task({ message: '', error: 'GateError: 篇幅过短' })]
    const w = mount(TasksView)
    await w.vm.$nextTick()
    expect(w.find('.tc-msg').text()).toContain('篇幅过短')
  })
})
