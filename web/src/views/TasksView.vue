<template>
  <div class="tasks-wrap">
    <div class="pane grow">
      <div class="pane-head">
        <span class="title">任务中心</span>
        <span class="pill mini">
          <span class="dot" :class="runningCount ? 'brand' : 'off'" />
          {{ runningCount ? `${runningCount} 个执行中` : '空闲' }}
        </span>
        <span class="spacer" />
        <span v-if="hub.taskTruncated" class="trunc-hint" role="status">
          仅显示最近 {{ hub.tasks.length }} / {{ hub.taskTotal }} 条
        </span>
        <el-select v-model="fKind" size="small" placeholder="类型" clearable class="filt">
          <el-option v-for="k in KINDS" :key="k.v" :label="k.t" :value="k.v" />
        </el-select>
        <el-select v-model="fStatus" size="small" placeholder="状态" clearable class="filt">
          <el-option v-for="s in STATUSES" :key="s.v" :label="s.t" :value="s.v" />
        </el-select>
        <el-button size="small" :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
      </div>

      <div class="pane-body body">
        <!-- 卡片列表：窄屏不再依赖表格横向滚动，宽屏也更易扫读 -->
        <div v-if="list.length" class="task-list">
          <article
            v-for="t in list" :key="t.task_id"
            class="task-card"
            :class="`st-${t.status}`"
            role="button"
            tabindex="0"
            :aria-label="`任务 ${t.task_id}，${kindText(t.kind)}，${taskTagText(t.status)}`"
            @click="showTask(t)"
            @keydown.enter.prevent="showTask(t)"
            @keydown.space.prevent="showTask(t)"
          >
            <div class="tc-head">
              <span class="tc-id mono">{{ t.task_id }}</span>
              <el-tag size="small" :type="taskTagType(t.status)" effect="dark">
                {{ taskTagText(t.status) }}
              </el-tag>
              <span class="tc-kind">{{ kindText(t.kind) }}</span>
              <span class="spacer" />
              <span class="tc-time">{{ fmtTime(t.updated_at) }}</span>
            </div>

            <div class="tc-target">
              <span v-if="t.article_id" class="tc-art">文章 #{{ t.article_id }}</span>
              <el-tag v-for="pf in platList(t)" :key="pf" size="small" effect="plain" type="info">
                {{ platName(pf) }}
              </el-tag>
              <span v-if="!t.article_id && !platList(t).length" class="dim">—</span>
            </div>

            <div v-if="t.message || t.error" class="tc-msg" :class="{ err: !t.message && t.error }">
              {{ t.message || t.error }}
            </div>

            <div v-if="t.status === 'waiting_human'" class="tc-ops">
              <el-button size="small" type="primary" @click.stop="doResume(t, true)">已处理，继续</el-button>
              <el-button size="small" text @click.stop="doResume(t, false)">放弃</el-button>
            </div>
          </article>
        </div>
        <EmptyState v-else title="还没有任务"
                    desc="发布、更新、登录、同步都会产生任务，进度通过 SSE 实时推进" :px="96" />
      </div>
    </div>

    <!-- 任务详情 -->
    <el-dialog v-model="taskDialog" :title="`任务 ${taskDetail?.task_id || ''}`" width="min(640px, 94vw)">
      <template v-if="taskDetail">
        <div class="rd-meta">
          <el-tag size="small" :type="taskTagType(taskDetail.status)" effect="dark">
            {{ taskTagText(taskDetail.status) }}
          </el-tag>
          <span class="dim">{{ kindText(taskDetail.kind) }}</span>
          <span v-if="taskDetail.article_id" class="dim">
            文章 #{{ taskDetail.article_id }}
            <template v-if="taskDetail.article_title">· {{ taskDetail.article_title }}</template>
          </span>
          <span v-if="platList(taskDetail).length" class="dim">
            {{ platList(taskDetail).map(platName).join('、') }}
          </span>
          <span v-if="taskDetail.attempts" class="dim">尝试 {{ taskDetail.attempts }} 次</span>
        </div>

        <div v-if="taskDetail.result?.human_task" class="rd-sec rd-human">
          <b>人工任务</b>
          <div>{{ taskDetail.result.human_task.message }}</div>
          <div v-if="taskDetail.result.human_task.edit_url" class="dim">
            <a :href="taskDetail.result.human_task.edit_url" target="_blank" rel="noopener noreferrer" class="p-link">
              {{ taskDetail.result.human_task.edit_url }}
            </a>
          </div>
        </div>

        <div v-if="Array.isArray(taskDetail.result) && taskDetail.result.length" class="rd-sec">
          <b>平台结果</b>
          <div v-for="(r, i) in taskDetail.result" :key="i" class="rd-line">
            <el-tag size="small" :type="r.ok ? 'success' : 'danger'" effect="plain">{{ r.platform }}</el-tag>
            <span v-if="r.warning" class="dim">需人工收尾</span>
            <span v-if="r.skipped" class="dim">已跳过（内容未变）</span>
            <span v-if="r.error" class="run-err">{{ r.error }}</span>
            <a v-if="r.post_url" class="p-link" :href="r.post_url" target="_blank" rel="noopener noreferrer">文章链接</a>
          </div>
        </div>

        <div v-if="taskDetail.error" class="rd-sec"><b>错误</b><div class="run-err">{{ taskDetail.error }}</div></div>
        <div v-if="taskDetail.message" class="rd-sec"><b>说明</b><div class="dim">{{ taskDetail.message }}</div></div>
      </template>
      <template #footer>
        <el-button v-if="taskDetail?.article_id" @click="openArticle">打开文章</el-button>
        <el-button v-if="taskDetail?.status === 'waiting_human'" type="primary"
                   @click="doResume(taskDetail, true)">已处理，继续</el-button>
        <el-button @click="taskDialog = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { ElMessageBox } from 'element-plus'
import { useHubStore } from '@/stores/hub'
import { api } from '@/api'
import EmptyState from '@/components/EmptyState.vue'
import { fmtDateTime } from '@/utils/format'

const hub = useHubStore()

const loading = ref(false)
const fKind = ref('')
const fStatus = ref('')
const taskDialog = ref(false)
const taskDetail = ref(null)

const KINDS = [
  { v: 'publish', t: '发布' }, { v: 'update', t: '更新' }, { v: 'sync', t: '同步' },
  { v: 'login', t: '登录' }, { v: 'refresh', t: '抓取' }, { v: 'assist', t: '人工接管' },
]
const STATUSES = [
  { v: 'running', t: '执行中' }, { v: 'pending', t: '排队中' },
  { v: 'waiting_human', t: '等人工' }, { v: 'ok', t: '完成' }, { v: 'failed', t: '失败' },
]

const list = computed(() =>
  hub.tasks.filter(t => (!fKind.value || t.kind === fKind.value)
                     && (!fStatus.value || t.status === fStatus.value)))
const runningCount = computed(() =>
  hub.tasks.filter(t => t.status === 'running' || t.status === 'pending').length)

onMounted(load)

async function load() {
  loading.value = true
  try { await hub.loadTasks() } finally { loading.value = false }
}

async function doResume(task, approved) {
  if (!approved) {
    try {
      await ElMessageBox.confirm(
        `放弃「${kindText(task.kind)} #${task.task_id}」这个任务？`,
        '确认放弃', { type: 'warning', confirmButtonText: '放弃', cancelButtonText: '再想想' })
    } catch { return }
  }
  await hub.resumeTask(task.task_id, approved)
  taskDialog.value = false
}

async function showTask(row) {
  taskDetail.value = null
  try { taskDetail.value = await api.task(row.task_id) } catch { return }
  taskDialog.value = true
}

function openArticle() {
  const id = taskDetail.value?.article_id
  taskDialog.value = false
  hub.switchView('write')
  if (id) hub.open(id)
}

// platforms 列表接口已解析为数组；这里再兜一层，字符串也不会让 join 抛错
const platList = (t) => {
  const p = t?.platforms
  if (Array.isArray(p)) return p
  if (typeof p === 'string' && p.trim()) {
    try { const v = JSON.parse(p); return Array.isArray(v) ? v : [] } catch { return [] }
  }
  return []
}
const platName = (id) => hub.platforms.find(p => p.id === id)?.name || id
const kindText = (k) => ({ publish: '发布', update: '更新', sync: '同步', refresh: '抓取', login: '登录', assist: '人工接管' }[k] || k || '—')
const taskTagType = (s) => s === 'ok' ? 'success' : s === 'failed' ? 'danger'
  : s === 'waiting_human' ? 'warning' : s === 'running' ? 'primary' : 'info'
const taskTagText = (s) => s === 'ok' ? '完成' : s === 'failed' ? '失败'
  : s === 'waiting_human' ? '等人工' : s === 'running' ? '执行中' : '排队中'
const fmtTime = (ts) => fmtDateTime(ts)
</script>

<style scoped lang="scss">
.tasks-wrap { flex: 1; min-height: 0; display: flex; padding: 14px 16px; gap: 12px; }
.grow { flex: 1; min-height: 0; }
.body { padding: 4px 14px 14px; }
.pill.mini { height: 26px; font-size: 11px; padding: 0 10px; }
.dim { color: var(--tx-3); }
.mono { font-family: var(--font-mono); font-size: 11px; }
.run-err { color: var(--bad); font-size: var(--fs-xs); }
.trunc-hint { font-size: var(--fs-xs); color: var(--tx-3); }
.p-link { color: var(--accent); text-decoration: none; &:hover { text-decoration: underline; } }
.filt { width: 110px; }

.task-list { display: flex; flex-direction: column; gap: 10px; padding: 4px 2px 8px; }

.task-card {
  border: 1px solid var(--line);
  // 状态色只做左侧细条，不铺满整卡（避免整页花）
  border-left: 3px solid var(--line-strong);
  border-radius: var(--r-md);
  background: var(--surface);
  padding: 12px 14px;
  cursor: pointer;
  box-shadow: var(--shadow);
  transition: border-color .14s, transform .14s, box-shadow .14s;
  animation: fadeUp .26s var(--ease-out) both;

  &:hover { border-color: var(--accent-line); transform: translateY(-1px); box-shadow: var(--shadow-lg); }
  &:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }

  &.st-ok { border-left-color: var(--ok); }
  &.st-failed { border-left-color: var(--bad); }
  &.st-waiting_human { border-left-color: var(--warn); }
  &.st-running, &.st-pending { border-left-color: var(--accent); }
}

.tc-head {
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  .tc-id { color: var(--tx-3); }
  .tc-kind { font-size: var(--fs-sm); font-weight: 600; color: var(--tx-1); }
  .tc-time { font-size: var(--fs-xs); color: var(--tx-3); font-variant-numeric: tabular-nums; }
}
.tc-target { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin-top: 7px; }
.tc-art { font-size: var(--fs-xs); color: var(--tx-2); }
.tc-msg {
  margin-top: 6px; font-size: var(--fs-xs); color: var(--tx-3); line-height: 1.6;
  &.err { color: var(--bad); }
}
.tc-ops { display: flex; gap: 6px; margin-top: 8px; }

.rd-meta { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 12px; }
.rd-sec { border-top: 1px solid var(--line); padding: 10px 0; font-size: var(--fs-xs);
  color: var(--tx-2); b { color: var(--tx-1); display: block; margin-bottom: 6px; } }
.rd-human { border-left: 3px solid var(--warn); padding-left: 10px; }
.rd-line { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; padding: 4px 0; }

@media (max-width: 767px) {
  .tasks-wrap { padding: 8px; }
  .pane-head { flex-wrap: wrap; row-gap: 8px; }
  .filt { width: 96px; }
  .trunc-hint { order: 9; width: 100%; }
}
</style>
