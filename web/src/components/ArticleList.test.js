import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import ArticleList from '@/components/ArticleList.vue'

// R4/R5：列表分页入口 + 键盘可达
const articles = [
  { id: 2, title: '第二篇', status: 'draft', updated_at: 1700000000 },
  { id: 1, title: '第一篇', status: 'published', updated_at: 1700000000 }
]

describe('ArticleList 分页与可达性', () => {
  it('还有更多时显示加载更多，并带剩余篇数', () => {
    const w = mount(ArticleList, {
      props: { articles, total: 5, hasMore: true }
    })
    const btn = w.findAll('button').filter(b => b.text().includes('加载更多'))
    expect(btn.length).toBe(1)
    expect(btn[0].text()).toContain('还有 3 篇')
  })

  it('没有更多时不显示加载更多', () => {
    const w = mount(ArticleList, { props: { articles, total: 2, hasMore: false } })
    expect(w.findAll('button').some(b => b.text().includes('加载更多'))).toBe(false)
  })

  it('点击加载更多向父组件发事件', async () => {
    const w = mount(ArticleList, { props: { articles, total: 5, hasMore: true } })
    const btn = w.findAll('button').find(b => b.text().includes('加载更多'))
    await btn.trigger('click')
    expect(w.emitted('load-more')).toHaveLength(1)
  })

  it('列表项按 Enter / 空格触发 open，且具备按钮语义', async () => {
    const w = mount(ArticleList, { props: { articles, total: 2, hasMore: false } })
    const item = w.findAll('.art-item')[0]
    expect(item.attributes('role')).toBe('button')
    expect(item.attributes('tabindex')).toBe('0')
    await item.trigger('keydown', { key: 'Enter' })
    await item.trigger('keydown', { key: ' ' })
    expect(w.emitted('open')).toHaveLength(2)
    expect(w.emitted('open')[0]).toEqual([2])
  })
})

describe('ArticleList 搜索与状态筛选', () => {
  const many = [
    { id: 3, title: 'Vue 3 组合式 API', status: 'draft', updated_at: 1700000000, summary: '重写编辑器' },
    { id: 2, title: 'SQLite 实测', status: 'published', updated_at: 1700000000, summary: '单机够用吗' },
    { id: 1, title: '无障碍改造', status: 'draft', updated_at: 1700000000, summary: '对比度' }
  ]

  it('按关键字过滤（标题或摘要）', async () => {
    const w = mount(ArticleList, { props: { articles: many, total: 3 } })
    await w.find('input').setValue('sqlite')
    const titles = w.findAll('.art-item .t').map(x => x.text())
    expect(titles).toEqual(['SQLite 实测'])
  })

  it('按状态筛选，且「全部」恢复', async () => {
    const w = mount(ArticleList, { props: { articles: many, total: 3 } })
    const chips = w.findAll('.chip')
    expect(chips.map(c => c.text())).toEqual(['全部', '草稿', '已发布', '待审'])
    await chips[2].trigger('click')          // 已发布
    expect(w.findAll('.art-item').length).toBe(1)
    await chips[0].trigger('click')          // 全部
    expect(w.findAll('.art-item').length).toBe(3)
  })

  it('筛选无结果时给出提示与一键清空', async () => {
    const w = mount(ArticleList, { props: { articles: many, total: 3 } })
    await w.find('input').setValue('不存在的关键字')
    expect(w.find('.no-match').exists()).toBe(true)
    const reset = w.findAll('.no-match button').find(b => b.text().includes('清空筛选'))
    await reset.trigger('click')
    expect(w.findAll('.art-item').length).toBe(3)
  })

  it('筛选态下不隐藏「加载更多」，但提示只作用于已加载页', async () => {
    const w = mount(ArticleList, { props: { articles: many, total: 30, hasMore: true } })
    await w.find('input').setValue('vue')
    expect(w.find('.more-hint').exists()).toBe(true)
    expect(w.find('.more-hint').text()).toContain('已加载的 3 篇')
  })
})
