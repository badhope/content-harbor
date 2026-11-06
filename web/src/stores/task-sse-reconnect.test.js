import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

// 模拟 fetch 流：第一次连接返回两条带 id 的事件后断开，
// 第二次连接必须带上 Last-Event-ID 才能补缺口。
function streamOf(chunks) {
  let i = 0
  return {
    getReader: () => ({
      read: async () => {
        if (i < chunks.length) return { value: new TextEncoder().encode(chunks[i++]), done: false }
        return { value: undefined, done: true }
      },
    }),
  }
}

describe('SSE 断线重连', () => {
  let calls
  beforeEach(() => {
    calls = []
    setActivePinia(createPinia())
    vi.useFakeTimers()
  })
  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('重连时带上 Last-Event-ID，收到 resync 时整体刷新', async () => {
    global.fetch = vi.fn(async (url, opts) => {
      calls.push({ url: String(url), headers: { ...(opts?.headers || {}) } })
      if (calls.length === 1) {
        return { ok: true, body: streamOf(['id: 7\ndata: {"type":"task.done","data":{}}\n\n']) }
      }
      return { ok: true, body: streamOf(['event: resync\ndata: {"reason":"x"}\n\n']) }
    })

    const { useTaskStore } = await import('@/stores/task')
    const store = useTaskStore()
    const loadSpy = vi.spyOn(store, 'loadTasks').mockResolvedValue()

    store.connectSSE()
    await vi.advanceTimersByTimeAsync(50)

    expect(calls[0].headers['Last-Event-ID']).toBeUndefined()

    // 第一次流结束 → 触发重连
    await vi.advanceTimersByTimeAsync(2000)
    expect(calls.length).toBeGreaterThanOrEqual(2)
    expect(calls[1].headers['Last-Event-ID']).toBe('7')

    await vi.advanceTimersByTimeAsync(100)
    expect(loadSpy).toHaveBeenCalled()

    // 收尾：停掉长连接与所有挂起的重连定时器，避免污染后续测试
    store.disconnectSSE()
    store._sseRetry = 0
    await vi.advanceTimersByTimeAsync(0)
  })
})
