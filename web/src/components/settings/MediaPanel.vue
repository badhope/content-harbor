<template>
  <div class="media-panel">
    <div class="hint-bar">
      <span class="dim small">
        图片按内容寻址去重存储；发布时会改写为对外可访问的地址。
        删除文章不会自动删图，这里可以清掉没被任何文章引用的旧图。
      </span>
      <span class="spacer" />
      <el-button size="small" :loading="loading" :icon="Refresh" @click="loadStats">刷新</el-button>
    </div>

    <!-- 统计 -->
    <div class="stat-row" v-if="stats">
      <div class="stat-cell">
        <div class="stat-num">{{ stats.count }}</div>
        <div class="stat-label">图片数</div>
      </div>
      <div class="stat-cell">
        <div class="stat-num">{{ formatBytes(stats.bytes) }}</div>
        <div class="stat-label">占用空间</div>
      </div>
      <div class="stat-cell wide">
        <div class="stat-num small">{{ stats.public_base }}</div>
        <div class="stat-label">对外地址</div>
      </div>
    </div>
    <div v-if="stats?.hint" class="dim small hint-line">{{ stats.hint }}</div>

    <!-- 孤儿清理 -->
    <div class="orphan-box">
      <div class="orphan-head">
        <span class="dim small">
          未被引用的图片<template v-if="orphans">：{{ orphans.count }} 张，{{ formatBytes(orphans.bytes) }}</template>
        </span>
        <span class="spacer" />
        <el-button size="small" :loading="scanning" @click="scanOrphans">扫描</el-button>
        <el-button v-if="orphans && orphans.count > 0" size="small" type="danger"
                   plain :loading="purging" @click="purge">清理</el-button>
      </div>
      <div v-if="orphans && orphans.count === 0" class="dim small ok-line">
        没有可清理的图片，所有图片都还被文章引用着。
      </div>
      <div v-else-if="orphans && orphans.count > 0" class="orphan-list">
        <div v-for="o in orphans.items.slice(0, 20)" :key="o.sha256" class="orphan-row">
          <code class="mono">{{ o.sha256.slice(0, 12) }}…</code>
          <span class="dim small">{{ formatBytes(o.bytes) }}</span>
          <span class="dim small ellipsis">{{ o.source || '(本地文件)' }}</span>
        </div>
        <div v-if="orphans.count > 20" class="dim small">
          另有 {{ orphans.count - 20 }} 张未显示
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '@/api'

const stats = ref(null)
const orphans = ref(null)
const loading = ref(false)
const scanning = ref(false)
const purging = ref(false)

function formatBytes(n) {
  const v = Number(n) || 0
  if (v < 1024) return `${v} B`
  if (v < 1024 * 1024) return `${(v / 1024).toFixed(1)} KB`
  if (v < 1024 * 1024 * 1024) return `${(v / 1024 / 1024).toFixed(1)} MB`
  return `${(v / 1024 / 1024 / 1024).toFixed(2)} GB`
}

async function loadStats() {
  loading.value = true
  try {
    stats.value = await api.mediaStats()
  } catch (e) {
    ElMessage.error('读取媒体统计失败：' + (e?.message || e))
  } finally {
    loading.value = false
  }
}

async function scanOrphans() {
  scanning.value = true
  try {
    orphans.value = await api.mediaOrphans()
    if (orphans.value.count === 0) ElMessage.success('没有未被引用的图片')
  } catch (e) {
    ElMessage.error('扫描失败：' + (e?.message || e))
  } finally {
    scanning.value = false
  }
}

async function purge() {
  try {
    await ElMessageBox.confirm(
      `将永久删除 ${orphans.value.count} 张未被引用的图片（${formatBytes(orphans.value.bytes)}）。` +
      '这些图片不会出现在任何文章里，删除后不可恢复。',
      '清理图片',
      { type: 'warning', confirmButtonText: '永久删除', cancelButtonText: '取消',
        confirmButtonClass: 'el-button--danger' }
    )
  } catch { return }

  purging.value = true
  try {
    const r = await api.mediaPurgeOrphans()
    ElMessage.success(`已清理 ${r.deleted} 张，释放 ${formatBytes(r.bytes)}`)
    orphans.value = null
    await loadStats()
  } catch (e) {
    ElMessage.error('清理失败：' + (e?.message || e))
  } finally {
    purging.value = false
  }
}

onMounted(loadStats)
</script>

<style scoped lang="scss">
.media-panel { display: flex; flex-direction: column; gap: 14px; }
.hint-bar { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.hint-line { margin-top: -6px; }
.stat-row { display: flex; gap: 12px; flex-wrap: wrap; }
.stat-cell {
  flex: 1; min-width: 110px; padding: 14px 16px; border-radius: 10px;
  background: var(--el-fill-color-lighter); border: 1px solid var(--el-border-color-lighter);
}
.stat-cell.wide { flex: 2; min-width: 220px; }
.stat-num { font-size: 22px; font-weight: 650; letter-spacing: -0.02em; }
.stat-num.small { font-size: 13px; font-weight: 500; word-break: break-all; }
.stat-label { font-size: 12px; color: var(--el-text-color-secondary); margin-top: 2px; }
.orphan-box { border: 1px solid var(--el-border-color-lighter); border-radius: 10px; padding: 12px 14px; }
.orphan-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.ok-line { margin-top: 8px; }
.orphan-list { margin-top: 10px; display: flex; flex-direction: column; gap: 6px; }
.orphan-row { display: flex; align-items: center; gap: 12px; font-size: 12px; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.ellipsis { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 40ch; }
</style>
