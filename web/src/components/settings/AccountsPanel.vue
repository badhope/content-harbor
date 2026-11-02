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
          <el-button v-if="p.id === 'cnblogs'" size="small" text type="primary"
                     @click="bsOpen = true">自动抠令牌</el-button>
          <el-button size="small" text :loading="busy === 'check:' + p.id" @click="doCheck(p)">检测</el-button>
          <el-button size="small" text :loading="busy === 'refresh:' + p.id" @click="doRefresh(p)">抓取入库</el-button>
          <el-button size="small" text @click="doDiagnose(p)">诊断</el-button>
          <el-button v-if="online(p.id)" size="small" text type="danger" @click="doLogout(p)">登出</el-button>
        </div>
      </div>
    </div>

    <!-- cnblogs 自动抠令牌：浏览器登录一次，三件套自动写 config.json -->
    <el-dialog v-model="bsOpen" title="博客园 · 自动抠令牌" width="min(460px, 94vw)">
      <p class="dim small" style="margin: 0 0 12px">
        输入博客园账号密码，弹出浏览器登录一次，MetaWeblog 三件套（endpoint/username/token）
        自动写进 config.json，之后发布走纯协议免扫码。
      </p>
      <el-form label-width="72px">
        <el-form-item label="用户名">
          <el-input v-model="bsForm.username" placeholder="博客园登录名" autocomplete="off" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input v-model="bsForm.password" type="password" show-password
                    placeholder="博客园登录密码" autocomplete="new-password" />
        </el-form-item>
      </el-form>
      <p v-if="bsRunning" class="dim small">正在弹出浏览器登录（最长 10 分钟），可关此窗口，完成后会自动刷新状态。</p>
      <p v-if="bsError" class="small" style="color: var(--el-color-danger)">{{ bsError }}</p>
      <template #footer>
        <el-button @click="bsOpen = false">关闭</el-button>
        <el-button type="primary" :loading="bsRunning" :disabled="!bsForm.username || !bsForm.password"
                   @click="startBootstrap">开始</el-button>
      </template>
    </el-dialog>

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
import { api } from '@/api'

const hub = useHubStore()
const busy = ref('')
const diagOpen = ref(false)
const diagData = ref(null)

// cnblogs 自动抠令牌：内存 job 轮询（凭据只经 POST 传给后端线程，不落库）
const bsOpen = ref(false)
const bsRunning = ref(false)
const bsError = ref('')
const bsForm = ref({ username: '', password: '' })

async function startBootstrap() {
  bsRunning.value = true
  bsError.value = ''
  try {
    const { job_id } = await api.bootstrapCnblogs(bsForm.value.username, bsForm.value.password)
    const deadline = Date.now() + 660000
    while (Date.now() < deadline) {
      await new Promise(r => setTimeout(r, 3000))
      const j = await api.bootstrapCnblogsJob(job_id)
      if (j.status === 'succeeded') {
        ElMessage.success('博客园三件套已自动写进 config.json')
        bsForm.value = { username: '', password: '' }
        bsOpen.value = false
        await reload()
        return
      }
      if (j.status === 'failed') {
        bsError.value = j.error || '登录未成功，检查账号密码或验证码'
        return
      }
    }
    bsError.value = '等待超时（11 分钟），后台可能仍在跑，稍后点「检测」确认'
  } catch (e) {
    bsError.value = e?.response?.data?.detail || String(e)
  } finally {
    bsRunning.value = false
  }
}

// 每个平台的接入方式一句话说清（与 adapters 的真实凭据模型一致）
const ACCT_HINTS = {
  zhihu: '浏览器登录（扫码一次长期有效）',
  juejin: '浏览器登录；或 config.json 填 token 走 API',
  csdn: '浏览器登录（账密或微信扫码）',
  bilibili: '浏览器扫码登录（仅草稿，发布需到编辑页二次确认）',
  toutiao: '浏览器扫码登录（标题限 30 字）',
  oschina: '浏览器账密登录',
  segmentfault: '浏览器账密登录',
  cnblogs: '浏览器登录后自动抠 MetaWeblog 三件套（点「自动抠令牌」），无需手贴 config.json',
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

// 测试缝：el-dialog 在 jsdom 里不 teleport 落地，轮询逻辑只能从实例驱动
defineExpose({ startBootstrap, bsForm, bsOpen, bsError })
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
