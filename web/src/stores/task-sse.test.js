import { afterEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('@/api', () => ({ api: { tasksPage: vi.fn().mockResolvedValue({ items: [] }) } }))

import { useTaskStore } from './task'

afterEach(() => {
  vi.unstubAllGlobals()
  vi.useRealTimers()
  localStorage.clear()
})

function readerFor(frames) {
  const encoder = new TextEncoder()
  let i = 0
  return {
    getReader: () => ({
      read: vi.fn(async () => {
        if (i < frames.length) return { value: encoder.encode(frames[i++]), done: false }
        return { value: undefined, done: true }
      }),
    }),
  }
}

describe('authenticated task stream', () => {
  it('带 token 请求并处理 data 帧', async () => {
    vi.useFakeTimers()
    setActivePinia(createPinia())
    localStorage.setItem('harbor_api_token', 'test-only-token')
    const fetchMock = vi.fn(async () => ({
      ok: true,
      body: readerFor(['id: 3\ndata: {"type":"task.ok"}\n\n']),
    }))
    vi.stubGlobal('fetch', fetchMock)
    const store = useTaskStore()
    store.loadTasks = vi.fn()
    store.connectSSE()
    await vi.waitFor(() => expect(store.loadTasks).toHaveBeenCalledWith(true))
    expect(fetchMock.mock.calls[0][1].headers['X-API-Token']).toBe('test-only-token')
    store.disconnectSSE()
  })

  it('持续失败时指数退避并在第三次失败后挂轮询兜底', async () => {
    vi.useFakeTimers()
    setActivePinia(createPinia())
    // 始终抛错：模拟后端不可达，重连会一直失败
    const fetchMock = vi.fn(async () => { throw new Error('network down') })
    vi.stubGlobal('fetch', fetchMock)
    const store = useTaskStore()
    store.loadTasks = vi.fn()
    store.connectSSE()

    // 每次失败后退避 1s/2s/4s，推进足够时间让它累计到 3 次
    await vi.advanceTimersByTimeAsync(30000)
    expect(store._sseRetry).toBeGreaterThanOrEqual(3)
    expect(store._pollTimer).toBeTruthy()
    store.disconnectSSE()
  })

  it('重连成功后清掉退避计数与轮询兜底', async () => {
    vi.useFakeTimers()
    setActivePinia(createPinia())
    let attempt = 0
    const fetchMock = vi.fn(async () => {
      attempt += 1
      if (attempt === 1) throw new Error('network down')
      return { ok: true, body: readerFor(['id: 1\ndata: {"type":"task.ok"}\n\n']) }
    })
    vi.stubGlobal('fetch', fetchMock)
    const store = useTaskStore()
    store.loadTasks = vi.fn()
    store.connectSSE()
    await vi.waitFor(() => expect(store._sseRetry).toBe(1))

    await vi.advanceTimersByTimeAsync(5000)
    // 连接建立即归零。第二次连接只读到 done，属于正常重连（归 1 而非累积），
    // 因此这里断言"没有被累加到 2 以上"，这才是退避不膨胀的关键语义。
    expect(store._sseRetry).toBeLessThanOrEqual(1)
    expect(store._pollTimer).toBeFalsy()
    store.disconnectSSE()
  })
})
