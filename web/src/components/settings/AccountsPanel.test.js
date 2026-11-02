import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { setActivePinia, createPinia } from 'pinia'

// 组件与 account store 都从 @/api 拿请求层——整模块 mock 掉，不打真后端
vi.mock('@/api', () => ({
  api: {
    platforms: vi.fn().mockResolvedValue([
      { id: 'cnblogs', name: '博客园', needs_browser: false, capabilities: {} },
      { id: 'juejin', name: '掘金', needs_browser: true, capabilities: {} },
    ]),
    status: vi.fn().mockResolvedValue({ accounts: [], auth_sweep: {} }),
    accounts: vi.fn().mockResolvedValue([]),
    bootstrapCnblogs: vi.fn().mockResolvedValue({ job_id: 'j1' }),
    bootstrapCnblogsJob: vi.fn().mockResolvedValue({
      job_id: 'j1', status: 'succeeded', result: { outcome: 'success' }, error: '',
    }),
    diagnose: vi.fn().mockResolvedValue({}),
  },
}))

import AccountsPanel from '@/components/settings/AccountsPanel.vue'

describe('AccountsPanel cnblogs 自动抠令牌', () => {
  let w
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.useFakeTimers()
  })
  afterEach(() => {
    w?.unmount()
    vi.useRealTimers()
    vi.clearAllMocks()
  })

  const mountPanel = async () => {
    w = mount(AccountsPanel)
    await flushPromises()
    return w
  }

  it('cnblogs 卡片有「自动抠令牌」入口，其它平台没有', async () => {
    w = await mountPanel()
    const btns = w.findAll('button').filter(b => b.text().includes('自动抠令牌'))
    expect(btns.length).toBe(1)
  })

  it('提交后轮询 job，成功清表单关弹窗并刷新状态', async () => {
    const { api } = await import('@/api')
    w = await mountPanel()
    w.vm.bsForm.username = 'myname'
    w.vm.bsForm.password = 'mypass'
    const p = w.vm.startBootstrap()
    // 轮询间隔 3s：推进假时钟走完一轮
    await vi.advanceTimersByTimeAsync(3100)
    await p

    expect(api.bootstrapCnblogs).toHaveBeenCalledWith('myname', 'mypass')
    expect(api.bootstrapCnblogsJob).toHaveBeenCalledWith('j1')
    expect(w.vm.bsForm.username).toBe('')       // 成功后清表单
    expect(w.vm.bsOpen).toBe(false)             // 成功后关弹窗
  })

  it('job 失败把后端 error 显示出来，不假成功', async () => {
    const { api } = await import('@/api')
    api.bootstrapCnblogsJob.mockResolvedValue({
      job_id: 'j1', status: 'failed', result: null, error: '密码错误',
    })
    w = await mountPanel()
    w.vm.bsForm.username = 'myname'
    w.vm.bsForm.password = 'mypass'
    const p = w.vm.startBootstrap()
    await vi.advanceTimersByTimeAsync(3100)
    await p

    expect(w.vm.bsError).toBe('密码错误')        // 错误可见，不是假成功
    expect(w.vm.bsForm.username).toBe('myname') // 失败不清表单，可改了重试
  })

  it('接口异常（如 409 并发）同样落到可见错误', async () => {
    const { api } = await import('@/api')
    api.bootstrapCnblogs.mockRejectedValue({
      response: { data: { detail: '已有 bootstrap 任务在跑，等它结束再试' } },
    })
    w = await mountPanel()
    w.vm.bsForm.username = 'u'
    w.vm.bsForm.password = 'p'
    const p = w.vm.startBootstrap()
    await vi.advanceTimersByTimeAsync(100)
    await p
    expect(w.vm.bsError).toContain('已有 bootstrap 任务在跑')
  })
})
