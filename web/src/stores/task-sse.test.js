import { afterEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('@/api', () => ({ api: { tasksPage: vi.fn().mockResolvedValue({ items: [] }) } }))

import { useTaskStore } from './task'

afterEach(() => {
  vi.unstubAllGlobals()
  vi.useRealTimers()
  localStorage.clear()
})

describe('authenticated task stream', () => {
  it('sends the token as a header, handles data frames and falls back on EOF', async () => {
    vi.useFakeTimers()
    setActivePinia(createPinia())
    localStorage.setItem('harbor_api_token', 'test-only-token')
    const encoder = new TextEncoder()
    const reader = {
      read: vi.fn()
        .mockResolvedValueOnce({ value: encoder.encode('data: {"type":"task.ok"}\n\n'), done: false })
        .mockResolvedValueOnce({ done: true }),
    }
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, body: { getReader: () => reader } })
    vi.stubGlobal('fetch', fetchMock)
    const store = useTaskStore()
    store.loadTasks = vi.fn()
    store.connectSSE()
    await vi.waitFor(() => expect(store.loadTasks).toHaveBeenCalledWith(true))
    expect(fetchMock.mock.calls[0][1].headers['X-API-Token']).toBe('test-only-token')
    await vi.waitFor(() => expect(store._es).toBeNull())
    expect(store._pollTimer).toBeTruthy()
    store.disconnectSSE()
  })
})
