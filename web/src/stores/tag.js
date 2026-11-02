import { defineStore } from 'pinia'
import { ElMessage } from 'element-plus'
import { api } from '@/api'

/** 标签治理 */
export const useTagStore = defineStore('tag', {
  state: () => ({
    tags: [],
    clusters: [],
    loadingClusters: false,
  }),

  actions: {
    async loadTags(limit = 50) { this.tags = await api.tags(limit) },
    async loadClusters(threshold = 72) {
      this.loadingClusters = true
      try {
        this.clusters = await api.tagsClusters(threshold) || []
      } finally {
        this.loadingClusters = false
      }
    },
    /** 把每个候选簇的成员合并到 canonical */
    async mergeAllClusters() {
      let affected = 0
      for (const c of this.clusters) {
        const target = c.canonical
        if (!target) continue
        for (const member of (c.members || [])) {
          if (member === target) continue
          const r = await api.mergeTag(member, target)
          affected += r?.affected || 0
        }
      }
      await this.loadTags()
      await this.loadClusters()
      ElMessage.success(`已合并近似标签，影响 ${affected} 篇文章`)
      return affected
    },
    async syncTags() {
      const r = await api.syncTags()
      ElMessage.success(`标签重新统计完成：${r.synced} 个标签`)
      return r
    },
    async renameTag(oldName, newName) {
      const r = await api.renameTag(oldName, newName)
      ElMessage.success(`已将「${oldName}」重命名为「${newName}」（影响 ${r.affected} 篇文章）`)
      return r
    },
    async mergeTag(from, to) {
      const r = await api.mergeTag(from, to)
      ElMessage.success(`已合并「${from}」→「${to}」（影响 ${r.affected} 篇文章）`)
      return r
    },
    async suggestTags(title, contentMd) { return await api.suggestTags(title, contentMd) },
  }
})
