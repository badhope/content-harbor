<template>
  <div class="acct-panel">
    <div class="hint-bar">
      <span class="dim small">
        有头浏览器扫码一次，登录态长期复用；纯协议平台（博客园/掘金 API）在 config.json 填令牌即可。
        发布前每个平台的登录态会在写作台发布轨里实时显示。
      </span>
      <span class="spacer" />
      <el-button size="small" :icon="Refresh" :loading="hub.loadingList" @click="reload">刷新全部</el-button>
    </div>

    <div class="acct-grid">
      <div v-for="p in hub.platforms" :key="p.id" class="acct-card">
        <div class="ac-top">
          <b class="ac-name">{{ p.name }}</b>
          <el-tag size="small" :type="online(p.id) ? 'success' : 'info'" effect="dark">
            {{ online(p.id) ? '在线' : '离线' }}
          </el-tag>
        </div>
        <div class="ac-sub">{{ p.needs_browser ? '需要浏览器登录（扫码一次长期有效）' : '免登 API，凭据在 config.json' }}</div>

        <div class="ac-ops">
          <el-button v-if="p.needs_browser" size="small" text type="primary"
                     :loading="hub.loginTasks[p.id]?.status === 'running'"
                     @click="hub.startLogin(p.id)">
            {{ hub.loginTasks[p.id]?.status === 'running' ? '等待扫码…' : (online(p.id) ? '重新登录' : '去登录') }}
          </el-button>
          <el-button size="small" text :loading="busy === 'check:' + p.id" @click="doCheck(p)">检测</el-button>
          <el-button size="small" text :loading="busy === 'refresh:' + p.id" @click="doRefresh(p)">抓取入库</el-button>
          <el-button size="small" text @click="doDiagnose(p)">诊断</el-button>
          <el-button v-if="online(p.id)" size="small" text type="danger" @click="doLogout(p)">登出</el-button>
        </div>
      </div>
    </div>

    <el-dialog v-model="diagOpen" :title="`接入诊断 · ${diagData?.platform || ''}`" width="min(560px, 94vw)">
      <el-descriptions v-if="diagData" :column="1" border size="small">
        <el-descriptions-item v-for="(v, k) in diagData" :key="k" :label="k">{{ v }}</el-descriptions-item>
      </el-descriptions>
      <template #footer><el-button @click="diagOpen = false">关闭</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useHubStore } from '@/stores/hub'

const hub = useHubStore()
const busy = ref('')
const diagOpen = ref(false)
const diagData = ref(null)

onMounted(async () => {
  if (!hub.platforms.length) await hub.loadPlatforms()
  await hub.loadStatus()
})

async function reload() {
  await Promise.all([hub.loadPlatforms(), hub.loadStatus()])
}

const online = (pf) => hub.accounts.some(a => a.platform === pf && a.status === 'logined')

async function doCheck(p) {
  busy.value = 'check:' + p.id
  try {
    const r = await hub.checkPlatform(p.id)
    if (r === true) ElMessage.success(`${p.name} 登录态有效`)
    else if (r === false) ElMessage.warning(`${p.name} 未登录或已失效，点「去登录」`)
    else ElMessage.info(`${p.name} 检测未返回结果（看任务中心）`)
  } finally { busy.value = '' }
}

async function doRefresh(p) {
  busy.value = 'refresh:' + p.id
  try { await hub.refreshPlatform(p.id) } finally { busy.value = '' }
}

async function doDiagnose(p) {
  try {
    diagData.value = await hub.diagnosePlatform(p.id)
    diagOpen.value = true
  } catch { /* 拦截器已提示 */ }
}

async function doLogout(p) {
  try {
    await ElMessageBox.confirm(
      `登出 ${p.name}？会清除本地登录态（profile + 快照），下次要重新扫码。`,
      '确认登出', { type: 'warning', confirmButtonText: '登出', cancelButtonText: '取消' })
    await hub.logoutPlatform(p.id)
    ElMessage.success(`${p.name} 已登出`)
  } catch { /* 取消 */ }
}
</script>

<style scoped lang="scss">
.acct-panel { display: flex; flex-direction: column; gap: 14px; padding-top: 8px; }
.hint-bar { display: flex; align-items: center; gap: 10px; }
.acct-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 12px; }
.acct-card {
  border: 1px solid var(--line); border-radius: 12px; padding: 14px 16px;
  background: var(--surface);
  .ac-top { display: flex; align-items: center; justify-content: space-between; }
  .ac-name { color: var(--tx-1); font-size: var(--fs-md); }
  .ac-sub { font-size: var(--fs-xs); color: var(--tx-3); margin: 8px 0 10px; line-height: 1.6; }
  .ac-ops { display: flex; gap: 4px; flex-wrap: wrap; }
}
.dim { color: var(--tx-4); }
.small { font-size: var(--fs-xs); }
</style>
