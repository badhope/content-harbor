<template>
  <div class="wh-panel">
    <div class="hint-bar">
      <span class="dim small">
        任务完成 / 失败 / 等人工等事件推送到你的地址（HMAC 签名）；连续失败会自动禁用（防僵尸订阅）。
      </span>
      <span class="spacer" />
      <el-button size="small" type="primary" :icon="Plus" @click="openCreate">新建 Webhook</el-button>
      <el-button size="small" :icon="Refresh" @click="reload">刷新</el-button>
    </div>

    <el-table v-if="hub.webhooks.length" :data="hub.webhooks" size="default">
      <el-table-column label="地址" min-width="300">
        <template #default="{ row }"><span class="mono">{{ row.url }}</span></template>
      </el-table-column>
      <el-table-column label="事件" min-width="200">
        <template #default="{ row }">
          <el-tag v-for="e in parseEvents(row.events)" :key="e" size="small" effect="plain" class="ev-tag">{{ e }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="96">
        <template #default="{ row }">
          <el-tag size="small" :type="row.enabled ? 'success' : 'danger'" effect="dark">
            {{ row.enabled ? '启用' : '已禁用' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="200">
        <template #default="{ row }">
          <el-button size="small" text @click="hub.testWebhook(row.id)">测试</el-button>
          <el-button size="small" text @click="toggle(row)">{{ row.enabled ? '禁用' : '启用' }}</el-button>
          <el-button size="small" text type="danger" @click="del(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>
    <EmptyState v-else title="还没有 Webhook"
                desc="可以接到飞书机器人 / Slack / 自建服务；任务失败、等人工时第一时间知道"
                :px="60" />

    <div class="sec">
      <div class="sec-title">
        站内通知（最近 {{ hub.notifications.length }} 条）
        <el-button size="small" text @click="hub.loadNotifications()">刷新</el-button>
      </div>
      <div v-if="hub.notifications.length" class="noti-list">
        <div v-for="(n, i) in hub.notifications.slice(-30).reverse()" :key="i" class="noti-row">
          <el-tag size="small" :type="notiType(n)" effect="plain">{{ n.event || n.type || '事件' }}</el-tag>
          <span class="dim small">{{ notiText(n) }}</span>
          <span class="spacer" />
          <span class="dim small">{{ fmtTime(n.timestamp) }}</span>
        </div>
      </div>
      <div v-else class="dim small">暂无通知</div>
    </div>

    <el-dialog v-model="createOpen" title="新建 Webhook" width="520px">
      <el-form label-width="82px">
        <el-form-item label="地址" required>
          <el-input v-model="form.url" placeholder="https://your-server/hook" />
        </el-form-item>
        <el-form-item label="签名密钥">
          <el-input v-model="form.secret" placeholder="可选；填写后请求带 HMAC 签名头" />
        </el-form-item>
        <el-form-item label="订阅事件">
          <el-select v-model="form.events" multiple placeholder="不选 = 全部事件" style="width:100%">
            <el-option v-for="e in EVENT_OPTIONS" :key="e.v" :label="e.t" :value="e.v" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createOpen = false">取消</el-button>
        <el-button type="primary" :loading="creating" :disabled="!form.url.trim()" @click="submit">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { Plus, Refresh } from '@element-plus/icons-vue'
import { ElMessageBox } from 'element-plus'
import { useHubStore } from '@/stores/hub'
import EmptyState from '@/components/EmptyState.vue'
import { fmtClock } from '@/utils/format'

const hub = useHubStore()

const createOpen = ref(false)
const creating = ref(false)
const form = reactive({ url: '', secret: '', events: [] })

const EVENT_OPTIONS = [
  { v: 'task.ok', t: '任务完成' },
  { v: 'task.failed', t: '任务失败' },
  { v: 'task.waiting_human', t: '任务等人工' },
  { v: 'publish.ok', t: '发布成功' },
  { v: 'publish.failed', t: '发布失败' },
]

onMounted(reload)

async function reload() {
  await Promise.all([hub.loadWebhooks(), hub.loadNotifications()])
}

function openCreate() {
  Object.assign(form, { url: '', secret: '', events: [] })
  createOpen.value = true
}

async function submit() {
  creating.value = true
  try {
    await hub.createWebhook({ url: form.url.trim(), secret: form.secret,
                              events: form.events.length ? form.events : null })
    createOpen.value = false
  } finally { creating.value = false }
}

async function toggle(row) {
  await hub.updateWebhook(row.id, { enabled: !row.enabled })
}

async function del(row) {
  try {
    await ElMessageBox.confirm(`删除 Webhook「${row.url}」？`, '确认删除',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' })
    await hub.deleteWebhook(row.id)
  } catch { /* 取消 */ }
}

const parseEvents = (e) => {
  if (!e) return ['*']
  try { const v = JSON.parse(e); return Array.isArray(v) ? (v.length ? v : ['*']) : [String(v)] } catch { return [String(e)] }
}
const notiType = (n) => String(n.event || n.type || '').includes('fail') ? 'danger'
  : String(n.event || n.type || '').includes('human') ? 'warning' : 'success'
const notiText = (n) => n.message || n.data?.message || `${n.kind || ''} ${n.article_id ? '#' + n.article_id : ''}`.trim() || '—'
const fmtTime = (ts) => fmtClock(ts)
</script>

<style scoped lang="scss">
.wh-panel { display: flex; flex-direction: column; gap: 14px; padding-top: 8px; }
.hint-bar { display: flex; align-items: center; gap: 10px; }
.ev-tag { margin-right: 4px; }
.mono { font-family: var(--font-mono); font-size: 11px; color: var(--tx-2); }
.sec { border-top: 1px solid var(--line-soft); padding-top: 12px; }
.sec-title {
  font-size: var(--fs-sm); font-weight: 600; color: var(--tx-2); margin-bottom: 10px;
  display: flex; align-items: center; gap: 8px;
}
.noti-list { display: flex; flex-direction: column; gap: 6px; max-height: 260px; overflow-y: auto; }
.noti-row { display: flex; align-items: center; gap: 8px; .spacer { flex: 1; } }
.dim { color: var(--tx-4); }
.small { font-size: var(--fs-xs); }
</style>
