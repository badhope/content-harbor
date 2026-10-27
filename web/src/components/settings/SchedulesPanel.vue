<template>
  <div class="sched-panel">
    <div class="hint-bar">
      <span class="dim small">到点自动投递到统一任务引擎；一次性 / 每天 / 每周 / cron 表达式均支持。</span>
      <span class="spacer" />
      <el-button size="small" type="primary" :icon="Plus" @click="openCreate">新建定时任务</el-button>
      <el-button size="small" :icon="Refresh" @click="hub.loadSchedules()">刷新</el-button>
    </div>

    <div v-if="hub.schedules.length" class="sched-list">
      <div v-for="s in hub.schedules" :key="s.id" class="sched-card" :class="{ disabled: !s.enabled }">
        <div class="sc-head">
          <el-tag size="small" :type="s.enabled ? 'success' : 'info'" effect="plain">
            {{ label(s.schedule_type, s.schedule_expr) }}
          </el-tag>
          <span class="sc-title">{{ s.title || ('任务 #' + s.id) }}</span>
          <el-tag v-if="s.draft_only" size="small" type="info" effect="plain">草稿</el-tag>
          <span class="dim small">#{{ s.id }}</span>
        </div>
        <div class="sc-meta dim small">
          文章 #{{ s.article_id }} · 平台 {{ parsePlats(s.platforms).map(platName).join('、') || '—' }}
        </div>
        <div class="sc-meta dim small">
          下次执行：{{ s.next_run ? fmtTime(s.next_run) : '—' }} · 已跑 {{ s.run_count || 0 }} 次
        </div>
        <div class="sc-ops">
          <el-button size="small" text :type="s.enabled ? 'warning' : 'success'"
                     @click="s.enabled ? hub.pauseSchedule(s.id) : hub.resumeSchedule(s.id)">
            {{ s.enabled ? '暂停' : '启用' }}
          </el-button>
          <el-button size="small" text @click="hub.triggerSchedule(s.id)">立即执行</el-button>
          <el-button size="small" text type="danger" @click="confirmDelete(s)">删除</el-button>
        </div>
      </div>
    </div>
    <EmptyState v-else title="还没有定时任务"
                desc="例：每周一 9 点把某个专栏文章发到知乎 + 掘金"
                :px="60" />

    <el-dialog v-model="createOpen" title="新建定时任务" width="min(520px, 94vw)">
      <el-form label-width="92px">
        <el-form-item label="文章">
          <el-select v-model="form.article_id" filterable placeholder="选一篇" style="width:100%">
            <el-option v-for="a in articleOptions" :key="a.id" :label="`#${a.id} ${a.title}`" :value="a.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="平台">
          <el-select v-model="form.platforms" multiple placeholder="选平台" style="width:100%">
            <el-option v-for="p in hub.platforms" :key="p.id" :label="p.name" :value="p.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="方式">
          <el-select v-model="form.schedule_type" style="width:100%">
            <el-option label="一次性（到点发一次）" value="once" />
            <el-option label="每天" value="daily" />
            <el-option label="每周" value="weekly" />
            <el-option label="Cron 表达式" value="cron" />
          </el-select>
        </el-form-item>
        <el-form-item label="时间表达式">
          <el-input v-model="form.schedule_expr"
                    :placeholder="exprHint" />
          <div class="dim small" style="margin-top:4px">{{ exprHelp }}</div>
        </el-form-item>
        <el-form-item label="发布为">
          <el-radio-group v-model="form.draft_only">
            <el-radio-button :value="false">真实上线</el-radio-button>
            <el-radio-button :value="true">仅存草稿</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="备注名">
          <el-input v-model="form.title" placeholder="例如：周一早报发知乎+掘金" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createOpen = false">取消</el-button>
        <el-button type="primary" :loading="creating" :disabled="!form.article_id || !form.platforms.length"
                   @click="submit">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { Plus, Refresh } from '@element-plus/icons-vue'
import { ElMessageBox } from 'element-plus'
import { useHubStore } from '@/stores/hub'
import { api } from '@/api'
import EmptyState from '@/components/EmptyState.vue'
import { fmtDateTime } from '@/utils/format'

const hub = useHubStore()

const createOpen = ref(false)
// 文章库列表本身是分页的，定时任务要能选到任意一篇，这里单独翻页取全量
const articleOptions = ref([])
const creating = ref(false)
const form = reactive({ article_id: null, platforms: [], schedule_type: 'once',
                        schedule_expr: '', draft_only: false, title: '' })

const exprHint = computed(() => ({
  once: 'HH:MM（今天/明天到点发）或 unix 时间戳',
  daily: 'HH:MM，例如 09:30',
  weekly: '周几 HH:MM，例如 1 09:00（周一 9 点）',
  cron: '5 段式 cron，例如 0 9 * * 1',
}[form.schedule_type] || ''))

const exprHelp = computed(() => ({
  once: '不填 = 10 分钟后执行',
  daily: '每天到这个时间自动发',
  weekly: '0=周日 … 6=周六',
  cron: '分 时 日 月 周',
}[form.schedule_type] || ''))

async function loadArticleOptions() {
  const all = []
  for (let offset = 0; offset < 1000; offset += 200) {
    const r = await api.articlesPage({ limit: 200, offset }) || {}
    all.push(...(r.items || []))
    if (!r.has_more) break
  }
  articleOptions.value = all
}

onMounted(async () => {
  hub.loadSchedules()
  await loadArticleOptions()
  if (!hub.platforms.length) await hub.loadPlatforms()
})

function openCreate() {
  Object.assign(form, { article_id: null, platforms: [], schedule_type: 'once',
                        schedule_expr: '', draft_only: false, title: '' })
  createOpen.value = true
}

async function submit() {
  creating.value = true
  try {
    await hub.createSchedule({ ...form })
    createOpen.value = false
  } finally { creating.value = false }
}

async function confirmDelete(s) {
  try {
    await ElMessageBox.confirm(`删除定时任务「${s.title || '#' + s.id}」？`, '确认删除',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' })
    await hub.deleteSchedule(s.id)
  } catch { /* 取消 */ }
}

const label = (type, expr) => ({ once: '一次性', daily: '每天', weekly: '每周', cron: 'Cron' }[type] || type)
  + (expr ? ' · ' + expr : '')
const parsePlats = (p) => { try { return JSON.parse(p || '[]') } catch { return [] } }
const platName = (id) => hub.platforms.find(p => p.id === id)?.name || id
const fmtTime = (ts) => fmtDateTime(ts)
</script>

<style scoped lang="scss">
.sched-panel { display: flex; flex-direction: column; gap: 14px; padding-top: 8px; }
.hint-bar { display: flex; align-items: center; gap: 10px; }
.sched-list { display: flex; flex-direction: column; gap: 10px; }
.sched-card {
  border: 1px solid var(--line); border-radius: 12px; padding: 12px 14px;
  background: var(--surface);
  &.disabled { opacity: 0.55; }
  .sc-head { display: flex; align-items: center; gap: 10px; }
  .sc-title { color: var(--tx-1); font-size: var(--fs-sm); font-weight: 500; flex: 1;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .sc-meta { margin-top: 4px; }
  .sc-ops { display: flex; gap: 6px; margin-top: 8px; }
}
.dim { color: var(--tx-4); }
.small { font-size: var(--fs-xs); }
</style>
