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
        <el-select v-model="fKind" size="small" placeholder="类型" clearable style="width:110px">
          <el-option v-for="k in KINDS" :key="k.v" :label="k.t" :value="k.v" />
        </el-select>
        <el-select v-model="fStatus" size="small" placeholder="状态" clearable style="width:110px">
          <el-option v-for="s in STATUSES" :key="s.v" :label="s.t" :value="s.v" />
        </el-select>
        <el-button size="small" :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
      </div>

      <div class="pane-body body">
        <el-table v-if="list.length" :data="list" size="default" max-height="100%"
                  @row-click="showTask" row-style="cursor:pointer">
          <el-table-column label="任务" width="104">
            <template #default="{ row }"><span class="dim mono">{{ row.task_id }}</span></template>
          </el-table-column>
          <el-table-column label="类型" width="84">
            <template #default="{ row }">{{ kindText(row.kind) }}</template>
          </el-table-column>
          <el-table-column label="对象" min-width="150">
            <template #default="{ row }">
              {{ row.article_id ? ('文章 #' + row.article_id) : '' }}
              {{ (row.platforms || []).join('、') }}
            </template>
          </el-table-column>
          <el-table-column label="状态" width="100">
            <template #default="{ row }">
              <el-tag size="small" :type="taskTagType(row.status)" effect="dark">
                {{ taskTagText(row.status) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="说明" min-width="180">
            <template #default="{ row }">
              <span v-if="row.message" class="dim">{{ row.message }}</span>
              <span v-else-if="row.error" class="run-err">{{ row.error.slice(0, 60) }}</span>
              <span v-else class="dim">—</span>
            </template>
          </el-table-column>
          <el-table-column label="时间" width="128">
            <template #default="{ row }">{{ fmtTime(row.updated_at) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="150">
            <template #default="{ row }">
              <template v-if="row.status === 'waiting_human'">
                <el-button size="small" type="primary" text @click.stop="doResume(row, true)">继续</el-button>
                <el-button size="small" text type="danger" @click.stop="doResume(row, false)">放弃</el-button>
              </template>
            </template>
          </el-table-column>
        </el-table>
        <EmptyState v-else title="还没有任务" desc="发布、更新、登录、同步都会产生任务，进度通过 SSE 实时推进" :px="96" />
      </div>
    </div>

    <!-- 任务详情 -->
    <el-dialog v-model="taskDialog" :title="`任务 ${taskDetail?.task_id || ''}`" width="640px">
      <template v-if="taskDetail">
        <div class="rd-meta">
          <el-tag size="small" :type="taskTagType(taskDetail.status)" effect="dark">
            {{ taskTagText(taskDetail.status) }}
          </el-tag>
          <span class="dim">{{ kindText(taskDetail.kind) }} ·
            {{ taskDetail.article_id ? ('文章 #' + taskDetail.article_id) : '' }}
            {{ (taskDetail.platforms || []).join('、') }}</span>
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
.dim { color: var(--tx-4); }
.mono { font-family: var(--font-mono); font-size: 11px; }
.run-err { color: var(--bad); font-size: var(--fs-xs); }
.p-link { color: var(--accent); text-decoration: none; &:hover { text-decoration: underline; } }
.rd-meta { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 12px; }
.rd-sec { border-top: 1px solid var(--line); padding: 10px 0; font-size: var(--fs-xs);
  color: var(--tx-2); b { color: var(--tx-1); display: block; margin-bottom: 6px; } }
.rd-human { border-left: 3px solid var(--warn); padding-left: 10px; }
.rd-line { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; padding: 4px 0; }
</style>
