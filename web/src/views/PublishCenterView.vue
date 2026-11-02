<template>
  <div class="center-wrap">
    <!-- 顶部统计 -->
    <div class="stat-row">
      <div class="stat-card" :class="{ warn: waitTotal > 0 }">
        <div class="sc-num">{{ waitTotal }}</div>
        <div class="sc-lbl">等待人工</div>
      </div>
      <div class="stat-card">
        <div class="sc-num">{{ pendingSync.length }}</div>
        <div class="sc-lbl">待同步平台</div>
      </div>
      <div class="stat-card">
        <div class="sc-num">{{ okPubs }}</div>
        <div class="sc-lbl">已上线实例</div>
      </div>
      <div class="stat-card">
        <div class="sc-num">{{ hub.stats?.published_articles || 0 }}</div>
        <div class="sc-lbl">已发布文章</div>
      </div>
      <div class="stat-ops">
        <el-button :icon="Refresh" :loading="reloading" @click="reloadAll">刷新</el-button>
        <el-button v-if="pendingSync.length" type="primary" :icon="Refresh"
                   :loading="hub.publishing" @click="hub.syncPending()">一键同步全部</el-button>
      </div>
    </div>

    <!-- ① 等待人工（任务挂起 + 平台草稿等人点发布） -->
    <section v-if="waitTotal" class="pane blk">
      <div class="pane-head"><span class="title">需要人工处置</span></div>
      <div class="pane-body blk-body">
        <div class="human-grid">
          <div v-for="t in hub.waitingTasks" :key="t.task_id" class="human-card run-card">
            <div class="hc-head">
              <el-tag size="small" type="danger" effect="dark">{{ kindText(t.kind) }}</el-tag>
              <el-tag size="small" effect="plain">{{ (t.platforms || []).join('、') || '?' }}</el-tag>
              <span class="hc-title">{{ t.article_id ? `文章 #${t.article_id}` : '平台任务' }}</span>
            </div>
            <div class="hc-sub">
              {{ t.message || '任务挂起，等待人工处理' }}
              <div v-if="t.error" class="hc-err">{{ t.error }}</div>
            </div>
            <div class="hc-ops">
              <el-button size="small" type="primary" :loading="resuming === t.task_id"
                         @click="doResume(t, true)">已处理，继续</el-button>
              <el-button size="small" text @click="doResume(t, false)">放弃</el-button>
            </div>
          </div>

          <div v-for="it in hub.pendingHuman" :key="'ph' + it.platform + it.article_id + (it.post_id || '')"
               class="human-card">
            <div class="hc-head">
              <el-tag size="small" type="warning" effect="dark">{{ platName(it.platform) }}</el-tag>
              <span class="hc-title">{{ it.title || '未命名文章' }}</span>
            </div>
            <div class="hc-sub">自动化已到草稿，差最后一步人工发布（打开后登录点「确定并发布」）</div>
            <div class="hc-ops">
              <el-button size="small" type="warning" @click="goHandle(it)">打开去发布</el-button>
              <el-button size="small" text @click="hub.loadPendingHuman()">我处理完了</el-button>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- ②b 等你确认（中断模式：平台页已打开并预填，等你改配置/传附件后自己发布） -->
    <section v-if="pendingUser.length" class="pane blk">
      <div class="pane-head">
        <span class="title">等你确认</span>
        <span class="spacer" />
        <span class="dim small">平台页已打开并预填，改好配置/传完附件后自己点发布</span>
      </div>
      <div class="pane-body blk-body">
        <div v-for="g in pendingUser" :key="g.key" class="sync-row">
          <span class="p-title">{{ g.title || ('文章 #' + g.article_id) }}</span>
          <el-tag v-for="pf in g.platforms" :key="pf" size="small" type="warning" effect="dark">
            {{ platName(pf) }}
          </el-tag>
          <span class="spacer" />
          <el-button size="small" text @click="hub.open(g.article_id)">查看文章</el-button>
          <el-button size="small" text type="primary" @click="openPlatform(g)">打开发布页</el-button>
        </div>
      </div>
    </section>

    <!-- ② 待同步（改过正文、平台实例是旧版） -->
    <section v-if="pendingSync.length" class="pane blk">
      <div class="pane-head"><span class="title">待同步的发布实例</span></div>
      <div class="pane-body blk-body">
        <div v-for="g in pendingSync" :key="g.key" class="sync-row">
          <span class="p-title">{{ g.title || ('文章 #' + g.article_id) }}</span>
          <el-tag v-for="pf in g.platforms" :key="pf" size="small" type="warning" effect="plain">
            {{ platName(pf) }}
          </el-tag>
          <span class="spacer" />
          <el-button size="small" text @click="hub.open(g.article_id)">打开文章</el-button>
          <el-button size="small" text type="primary" :loading="hub.publishing"
                     @click="hub.updateArticleRemote(g.article_id, g.platforms)">同步这些平台</el-button>
        </div>
      </div>
    </section>

    <!-- ③ 可视化概览 -->
    <section class="viz-row">
      <section class="pane blk viz-card">
        <div class="pane-head"><span class="title">平台发布分布</span></div>
        <div class="pane-body blk-body">
          <ChartPanel v-if="platformChart.series[0].data.length" :option="platformChart" :h="210" />
          <EmptyState v-else :px="34" title="暂无发布数据" desc="发布成功后这里按平台展示占比" />
        </div>
      </section>
      <section class="pane blk viz-card">
        <div class="pane-head"><span class="title">任务状态构成</span></div>
        <div class="pane-body blk-body">
          <ChartPanel :option="statusChart" :h="210" />
        </div>
      </section>
    </section>

    <!-- ③ 发布记录 -->
    <section class="pane blk grow">
      <div class="pane-head">
        <span class="title">发布记录</span>
        <span class="spacer" />
        <el-input v-model="keyword" size="small" placeholder="过滤文章/平台" clearable class="filter-in" />
      </div>
      <div class="pane-body blk-body">
        <el-table v-if="filteredPubs.length" :data="filteredPubs" size="default" max-height="100%">
          <el-table-column label="文章" min-width="240">
            <template #default="{ row }"><span class="p-title">{{ row.title || ('#' + row.article_id) }}</span></template>
          </el-table-column>
          <el-table-column label="平台" width="100">
            <template #default="{ row }">{{ platName(row.platform) }}</template>
          </el-table-column>
          <el-table-column label="状态" width="96">
            <template #default="{ row }">
              <el-tag size="small" :type="pubTagType(row.status)" effect="dark">{{ pubText(row.status) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="链接" min-width="150">
            <template #default="{ row }">
              <a v-if="row.post_url" class="p-link" :href="safeUrl(row.post_url)" target="_blank" rel="noopener">打开文章</a>
              <a v-else-if="row.edit_url" class="p-link" :href="safeUrl(row.edit_url)" target="_blank" rel="noopener">草稿/编辑页</a>
              <span v-else class="dim">—</span>
            </template>
          </el-table-column>
          <el-table-column label="更新时间" width="130">
            <template #default="{ row }">{{ fmtTime(row.updated_at) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="120">
            <template #default="{ row }">
              <el-button v-if="row.post_id" size="small" text :loading="hub.publishing"
                         @click="hub.updateArticleRemote(row.article_id, [row.platform])">更新</el-button>
            </template>
          </el-table-column>
        </el-table>
        <EmptyState v-else title="还没有发布记录" desc="写作台右侧发布轨发出第一篇后，这里会出现全平台的发布账本" :px="80" />
      </div>
    </section>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { ElMessageBox } from 'element-plus'
import { useHubStore } from '@/stores/hub'
import EmptyState from '@/components/EmptyState.vue'
import ChartPanel from '@/components/ChartPanel.vue'
import { fmtDateTime } from '@/utils/format'

const hub = useHubStore()

const keyword = ref('')
const reloading = ref(false)
const resuming = ref('')

const waitTotal = computed(() => hub.pendingHuman.length + hub.waitingTasks.length)
const pendingSync = computed(() => {
  const groups = new Map()
  for (const p of hub.allPubs) {
    if (p.status !== 'pending') continue
    const k = p.article_id
    if (!groups.has(k)) groups.set(k, { key: k, article_id: p.article_id, title: p.title, platforms: [] })
    groups.get(k).platforms.push(p.platform)
  }
  return [...groups.values()]
})
// 中断模式实例：平台页已打开等用户操作
const pendingUser = computed(() => {
  const groups = new Map()
  for (const p of hub.allPubs) {
    if (p.status !== 'pending_user') continue
    const k = p.article_id
    if (!groups.has(k)) groups.set(k, { key: k, article_id: p.article_id, title: p.title,
                                        platforms: [], url: p.edit_url || p.post_url })
    groups.get(k).platforms.push(p.platform)
  }
  return [...groups.values()]
})
const okPubs = computed(() => hub.allPubs.filter(p => p.status === 'ok').length)

const platformChart = computed(() => {
  const counts = new Map()
  for (const p of hub.allPubs) {
    if (p.status !== 'ok') continue
    const k = platName(p.platform) || p.platform
    counts.set(k, (counts.get(k) || 0) + 1)
  }
  return {
    tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
    legend: { bottom: 0, textStyle: { fontSize: 11 } },
    series: [{
      type: 'pie', radius: ['46%', '68%'], center: ['50%', '44%'],
      avoidLabelOverlap: true, label: { show: false },
      emphasis: { label: { show: true, fontSize: 12, fontWeight: 600 } },
      data: [...counts].map(([name, value]) => ({ name, value }))
    }]
  }
})

const statusChart = computed(() => {
  const order = [['pending', '排队'], ['running', '执行中'], ['waiting_human', '等人工'], ['failed', '失败'], ['done', '完成']]
  const counts = new Map(order)
  const tally = new Map(order.map(([k]) => [k, 0]))
  for (const t of hub.tasks) if (tally.has(t.status)) tally.set(t.status, tally.get(t.status) + 1)
  const labels = [...tally.keys()].map(k => counts.get(k) || k)
  const values = [...tally.values()]
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: 8, right: 14, top: 18, bottom: 8, containLabel: true },
    xAxis: { type: 'category', data: labels, axisTick: { show: false }, axisLine: { lineStyle: { color: '#D3DAE5' } }, axisLabel: { fontSize: 11, color: '#5A6478' } },
    yAxis: { type: 'value', minInterval: 1, splitLine: { lineStyle: { color: '#EDF1F7' } }, axisLabel: { fontSize: 11, color: '#5F6A78' } },
    series: [{
      type: 'bar', data: values, barMaxWidth: 34,
      itemStyle: { color: '#2A5CE0', borderRadius: [6, 6, 0, 0] }
    }]
  }
})
const filteredPubs = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  if (!kw) return hub.allPubs
  return hub.allPubs.filter(p =>
    (p.title || '').toLowerCase().includes(kw) ||
    (p.platform || '').toLowerCase().includes(kw))
})

onMounted(reloadAll)

async function reloadAll() {
  reloading.value = true
  try {
    await Promise.all([hub.loadPendingHuman(), hub.loadTasks(), hub.loadAllPubs(),
                       hub.loadStatus(), hub.loadPlatforms(), hub.loadArticles()])
  } finally { reloading.value = false }
}

async function doResume(task, approved) {
  if (!approved) {
    try {
      await ElMessageBox.confirm(
        `放弃「${kindText(task.kind)} #${task.task_id}」这个任务？该平台会记为失败。`,
        '确认放弃', { type: 'warning', confirmButtonText: '放弃', cancelButtonText: '再想想' })
    } catch { return }
  }
  resuming.value = task.task_id
  try { await hub.resumeTask(task.task_id, approved) }
  finally { resuming.value = '' }
}

function goHandle(it) {
  const url = it.edit_url || it.post_url
  if (url) window.open(safeUrl(url), '_blank', 'noopener,noreferrer')
}

function openPlatform(g) {
  if (g.url) window.open(safeUrl(g.url), '_blank', 'noopener,noreferrer')
}

// XSS 防护：仅放行 http/https，拒绝 javascript: / data: 等伪协议
function safeUrl(u) {
  try {
    const p = new URL(u)
    return (p.protocol === 'http:' || p.protocol === 'https:') ? u : '#'
  } catch { return '#' }
}

const platName = (id) => hub.platforms.find(p => p.id === id)?.name || id
const kindText = (k) => ({ publish: '发布', update: '更新', sync: '同步', refresh: '抓取', login: '登录', assist: '人工接管' }[k] || k || '—')
const pubTagType = (s) => s === 'ok' ? 'success'
  : (s === 'pending' || s === 'pending_user') ? 'warning'
  : s === 'failed' ? 'danger' : 'info'
const pubText = (s) => ({
  ok: '已发布', pending: '待同步', failed: '失败',
  pending_human: '待人工', pending_user: '等你确认'
}[s] || s)
const fmtTime = (ts) => fmtDateTime(ts)
</script>

<style scoped lang="scss">
.center-wrap {
  flex: 1; min-height: 0; display: flex; flex-direction: column;
  gap: 12px; padding: 14px 16px; overflow-y: auto;
}

.stat-row {
  display: grid;
  grid-template-columns: repeat(4, minmax(120px, 1fr)) auto;
  align-items: stretch;
  gap: 12px;
}
.stat-ops { display: flex; align-items: center; gap: 8px; }
.viz-row { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.viz-card { min-height: 0; }
.stat-card {
  padding: 10px 16px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--r-md);
  box-shadow: var(--shadow);
  .sc-num { font-size: 22px; font-weight: 700; color: var(--tx-1); font-variant-numeric: tabular-nums; line-height: 1.3; }
  .sc-lbl { font-size: var(--fs-xs); color: var(--tx-3); }
  &.warn { border-color: rgba(138, 85, 16, .35); .sc-num { color: var(--warn); } }
}

@media (max-width: 1023px) {
  .viz-row { grid-template-columns: 1fr; }
  .stat-row { grid-template-columns: repeat(2, 1fr); }
  .stat-ops { grid-column: 1 / -1; }
}
@media (max-width: 767px) {
  .center-wrap { padding: 8px; }
  .stat-card { padding: 8px 12px; .sc-num { font-size: 19px; } }
  .blk-body { padding: 4px 10px 12px; }
}

.blk { flex-shrink: 0; &.grow { flex: 1; min-height: 320px; } }
.blk-body { padding: 4px 16px 14px; }

.human-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 12px; }
.human-card {
  border: 1px solid var(--line); border-radius: 12px; padding: 12px 14px;
  background: var(--surface-2);
  .hc-head { display: flex; align-items: center; gap: 8px; }
  .hc-title { font-size: var(--fs-sm); color: var(--tx-1); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .hc-sub { font-size: var(--fs-xs); color: var(--tx-3); margin: 8px 0 10px; line-height: 1.6; }
  .hc-ops { display: flex; gap: 8px; }
  &.run-card { border-color: rgba(224, 82, 82, .35); }
  .hc-err { color: var(--bad); margin-top: 4px; word-break: break-all; }
}

.sync-row {
  display: flex; align-items: center; gap: 8px; padding: 8px 0;
  & + .sync-row { border-top: 1px solid var(--line-soft); }
  .spacer { flex: 1; }
  .p-title { color: var(--tx-1); font-size: var(--fs-sm); }
}

.p-title { color: var(--tx-1); }
.p-link { color: var(--accent); text-decoration: none; &:hover { text-decoration: underline; } }
.dim { color: var(--tx-4); }
.filter-in { width: 200px; }
</style>
