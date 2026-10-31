<template>
  <div class="acct-panel">
    <div class="hint-bar">
      <span class="dim small">
        浏览器平台扫码一次长期复用；API 平台在 config.json 填凭据
        （devto / github / wechat / telegraph 的字段见仓库 PLATFORM_CONNECTION.md），配完点「检测」验证。
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
        <div class="ac-sub">{{ acctHint(p) }}</div>

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

// 每个平台的接入方式一句话说清（与 adapters 的真实凭据模型一致）
const ACCT_HINTS = {
  zhihu: '浏览器登录（扫码一次长期有效）',
  juejin: '浏览器登录；或 config.json 填 token 走 API',
  csdn: '浏览器登录（账密或微信扫码）',
  bilibili: '浏览器扫码登录（仅草稿，发布需到编辑页二次确认）',
  toutiao: '浏览器扫码登录（标题限 30 字）',
  oschina: '浏览器账密登录',
  segmentfault: '浏览器账密登录',
  cnblogs: 'config.json 填 MetaWeblog 令牌；或「bootstrap-cnblogs」一条命令自动抠',
  metaweblog: 'config.json 填 endpoint + 用户名 + 应用密码（WordPress/Typecho）',
  '51cto': 'config.json 填 endpoint + 用户名 + 登录密码',
  devto: 'config.json 填 api_key（dev.to/settings/extensions 生成）',
  github: 'config.json 填 token + owner + repo（PAT 勾 repo 权限）',
  wechat: 'config.json 填 appid + secret（公众号后台基本配置；IP 需加白名单）',
  telegraph: '免注册：首次发布自动创建账号，token 回写 config.json',
  xiaohongshu: '浏览器登录（App 扫码一次长期有效）',
}
const acctHint = (p) => ACCT_HINTS[p.id]
  || (p.needs_browser ? '需要浏览器登录（扫码一次长期有效）' : '免登 API，凭据在 config.json')

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
