import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

// 标签治理面板曾整块渲染失败：模板读 hub.clusters / hub.loadingClusters，
// 但 hub store 没有把这两个 getter 透出来，读到 undefined 后 v-if 直接抛错，
// 面板空白且控制台无提示。这组用例把「store 必须透出模板用到的字段」锁住。

vi.mock('@/api', () => ({
  api: {
    tags: vi.fn(async () => [{ tag: 'Python', count: 3 }, { tag: 'python', count: 1 }]),
    tagsClusters: vi.fn(async () => ([
      { canonical: '异步', members: ['异步编程'], reason: ['包含关系'] },
      { canonical: 'JavaScript', members: ['Javscript'], reason: ['拼写近似'] },
    ])),
    mergeTag: vi.fn(async () => ({ affected: 1 })),
    syncTags: vi.fn(async () => ({ synced: 2 })),
    renameTag: vi.fn(async () => ({ affected: 1 })),
  },
}))

describe('hub store 标签治理字段', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('透出 clusters 与 loadingClusters 给模板', async () => {
    const { useHubStore } = await import('@/stores/hub')
    const hub = useHubStore()

    // 未加载时必须是空数组而不是 undefined——模板的 .length 依赖这个
    expect(hub.clusters).toEqual([])
    expect(hub.loadingClusters).toBe(false)

    await hub.loadClusters()
    expect(hub.clusters).toHaveLength(2)
    expect(hub.loadingClusters).toBe(false)
  })

  it('簇结构包含 canonical / members / reason', async () => {
    const { useHubStore } = await import('@/stores/hub')
    const hub = useHubStore()
    await hub.loadClusters()

    const first = hub.clusters[0]
    expect(first).toHaveProperty('canonical', '异步')
    expect(first.members).toContain('异步编程')
    expect(Array.isArray(first.reason)).toBe(true)
  })

  it('全部合并只合并非 canonical 成员', async () => {
    const { api } = await import('@/api')
    const { useHubStore } = await import('@/stores/hub')
    const hub = useHubStore()
    await hub.loadClusters()

    await hub.mergeAllClusters()

    // 2 组，每组 1 个非 canonical 成员 → 恰好 2 次 merge
    expect(api.mergeTag).toHaveBeenCalledTimes(2)
    expect(api.mergeTag).toHaveBeenCalledWith('异步编程', '异步')
    expect(api.mergeTag).toHaveBeenCalledWith('Javscript', 'JavaScript')
  })
})
