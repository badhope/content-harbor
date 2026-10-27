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
