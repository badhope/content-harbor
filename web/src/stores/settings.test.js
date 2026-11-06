import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('@/api', () => ({
  api: {
    aiConfig: vi.fn(),
    aiGate: vi.fn().mockResolvedValue({}),
  },
}))

import { api } from '@/api'
import { useSettingsStore } from './settings'

describe('设置 store 的加载状态', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('加载成功时清空错误', async () => {
    api.aiConfig.mockResolvedValue({ providers: [{ key: 'x' }], ready: true })
    const s = useSettingsStore()
    await s.loadAI()
    expect(s.loadError).toBe('')
    expect(s.providers.length).toBe(1)
    expect(s.loading).toBe(false)
  })

  it('加载失败时记录可见原因而不是静默空列表', async () => {
    api.aiConfig.mockRejectedValue(new Error('Network Error'))
    const s = useSettingsStore()
    await s.loadAI()
    expect(s.loadError).toBe('Network Error')
    expect(s.loading).toBe(false)
  })

  it('重试成功后错误被清除', async () => {
    api.aiConfig.mockRejectedValueOnce(new Error('boom'))
    const s = useSettingsStore()
    await s.loadAI()
    expect(s.loadError).toBe('boom')
    api.aiConfig.mockResolvedValue({ providers: [], ready: false })
    await s.loadAI()
    expect(s.loadError).toBe('')
  })
})
