<template>
  <div class="pane rail">
    <div class="pane-head">
      <span class="title">发布</span>
      <span class="spacer" />
      <el-tag v-if="currentId" size="small" effect="plain" type="info">#{{ currentId }}</el-tag>
    </div>

    <div class="pane-body rail-body">
      <template v-if="currentId">
        <!-- ① 选平台（登录态可见，未登录就地登录） -->
        <div class="rail-sec">
          <div class="rail-lbl">
            1 · 发到哪些平台
            <span class="spacer" />
            <el-button size="small" text @click="selectAll">全选</el-button>
            <el-button size="small" text @click="selected = []">清空</el-button>
          </div>
          <div class="plat-grid rail-plats">
            <div v-for="p in platforms" :key="p.id" class="plat-card rail-plat"
                 :class="{ on: selected.includes(p.id), disabled: !canPublish(p) }"
                 role="checkbox"
                 :aria-checked="selected.includes(p.id) ? 'true' : 'false'"
                 :aria-disabled="!canPublish(p) ? 'true' : 'false'"
                 :aria-label="`${p.name}：${canPublish(p) ? (selected.includes(p.id) ? '已选' : '未选') : '不支持发布'}`"
                 :tabindex="canPublish(p) ? 0 : -1"
                 @click="canPublish(p) && toggle(p.id)"
                 @keydown.enter.prevent="canPublish(p) && toggle(p.id)"
                 @keydown.space.prevent="canPublish(p) && toggle(p.id)">
              <div class="plat-top">
                <span class="plat-badge">{{ mark(p) }}</span>
                <div class="nn">
                  <div class="n">{{ p.name }}</div>
                  <div class="s">
                    <span class="log-dot" :class="isOnline(p.id) ? 'ok' : 'off'" />
                    {{ isOnline(p.id) ? '已登录' : (p.needs_browser ? '未登录' : '协议凭据') }}
                  </div>
                  <div class="s caps" v-if="p.capabilities && !p.capabilities.publish">
                    该平台不支持发布
                  </div>
                </div>
                <el-button v-if="p.needs_browser && !isOnline(p.id)" size="small" text type="primary"
                           class="login-btn" @click.stop="hub.startLogin(p.id)">登录</el-button>
              </div>
              <!-- 需要选分类的平台（掘金/CSDN），记住上次选择 -->
              <div v-if="catOptions[p.id] && selected.includes(p.id)" class="cat-row" @click.stop>
                <el-select v-model="platformSettings[p.id].category" size="small" placeholder="选分类"
                           style="width:100%" @change="rememberCategory">
                  <el-option v-for="c in catOptions[p.id]" :key="c" :label="c" :value="c" />
                </el-select>
              </div>
              <!-- 知乎：粘贴问题链接 = 写回答；留空 = 发专栏文章 -->
              <div v-if="p.id === 'zhihu' && selected.includes(p.id)" class="cat-row" @click.stop>
                <el-input v-model="platformSettings[p.id].question_url" size="small" clearable
                          placeholder="回答问题？粘贴知乎问题链接（留空=发专栏）" />
              </div>
            </div>
          </div>
        </div>

        <!-- ② 发布方式：真实上线是一等公民 -->
        <div class="rail-sec">
          <div class="rail-lbl">2 · 怎么发</div>
          <el-radio-group v-model="mode" class="mode-group">
            <el-radio-button value="live">立即发布（真实上线）</el-radio-button>
            <el-radio-button value="draft">仅存草稿</el-radio-button>
          </el-radio-group>
          <div class="rail-hint" v-if="mode === 'draft'">只进平台草稿箱，不公开；回头在平台上自己发。</div>
          <div class="rail-hint ok" v-else>直接发布到平台、公开可见；多平台间隔 8–20s 自动防风控。</div>
        </div>

        <!-- ②b 发布模式：全自动 / 中断改配置 / 全手动（2026-10-14） -->
        <div class="rail-sec">
          <div class="rail-lbl">3 · 谁来操作</div>
          <el-radio-group v-model="publishMode" class="mode-group">
            <el-radio-button value="auto">全自动</el-radio-button>
            <el-radio-button value="assist">中断改配置</el-radio-button>
            <el-radio-button value="manual">全手动</el-radio-button>
          </el-radio-group>
          <div class="rail-hint" v-if="publishMode === 'auto'">
            程序把标题/正文/标签/选项一次填好并直接发布，全程无需你介入。
          </div>
          <div class="rail-hint" v-else-if="publishMode === 'assist'">
            打开平台页并**预填**好内容，停在发布前——你改选项、上传封面/附件、确认无误再自己点发布。
          </div>
          <div class="rail-hint" v-else>
            只打开平台新建页，一个字都不自动填，你全程自己选自己填自己发。
          </div>

          <!-- 平台自带「AI 辅助」声明开关（替代正文声明，2026-10-14） -->
          <el-checkbox v-model="aiAssist" class="assist-ck">
            勾选平台的「AI 辅助创作」声明（在平台上显示，不写进正文）
          </el-checkbox>
        </div>

        <!-- ③ AI 稿合规门禁：人工核验 / 直发开关 -->
        <div v-if="isAiArticle && mode === 'live' && publishMode !== 'auto'" class="ai-gate direct">
          <b>AI 稿 · 中断/手动模式</b>
          <span class="gate-sub">你会在平台上亲自确认后再发布，合规由这次人工核验承接。</span>
        </div>
        <div v-else-if="isAiArticle && mode === 'live'" class="ai-gate"
             :class="{ direct: settings.directPublish }">
          <template v-if="settings.directPublish">
            <b>AI 稿已开启直发</b>
            <span class="gate-sub">安全扫描 + 双模型二审后自动上线（不写正文声明）。</span>
          </template>
          <template v-else>
            <b>AI 生成内容 · 需人工核验</b>
            <span class="gate-sub">默认先发草稿或人工确认后上线；也可选上面的「中断改配置」亲自在平台上操作。</span>
            <el-checkbox v-model="humanConfirmed" class="gate-ck">
              我已核验内容无误，同意正式发布
            </el-checkbox>
            <el-button size="small" text type="primary" @click="hub.switchView('settings')">
              或到设置开启「AI 稿直发」
            </el-button>
          </template>
        </div>

        <!-- ④ 发布按钮 -->
        <div class="rail-sec">
          <el-button class="go-btn" size="large"
                     :type="mode === 'live' ? 'primary' : 'warning'"
                     :loading="running"
                     :disabled="!selected.length || (mode === 'live' && isAiArticle && !settings.directPublish && !humanConfirmed)"
                     @click="runPublish">
            <template v-if="running">发布中 {{ doneCount }}/{{ selected.length }}…</template>
            <template v-else-if="publishMode === 'assist'">打开并预填 {{ selected.length }} 个平台</template>
            <template v-else-if="publishMode === 'manual'">打开 {{ selected.length }} 个平台页（手动）</template>
            <template v-else-if="mode === 'live'">全自动发布到 {{ selected.length }} 个平台</template>
            <template v-else>存为草稿（{{ selected.length }} 个平台）</template>
          </el-button>
        </div>

        <!-- 发布结果（每平台一行，链接/失败原因/待人工） -->
        <div v-if="results.length" class="rail-sec">
          <div class="rail-lbl">本次结果</div>
          <div v-for="r in results" :key="r.platform" class="res-row">
            <el-tag size="small" :type="r.ok ? 'success' : 'danger'" effect="dark">
              {{ platName(r.platform) }}
            </el-tag>
            <a v-if="r.post_url" class="p-link" :href="r.post_url" target="_blank" rel="noopener">打开文章</a>
            <a v-else-if="r.edit_url" class="p-link" :href="r.edit_url" target="_blank" rel="noopener">打开草稿</a>
            <span v-else-if="r.ok" class="dim">已发布</span>
            <span v-else class="run-err">{{ (r.error || r.warning || '失败').slice(0, 60) }}</span>
          </div>
        </div>

        <!-- 已发布实例：状态 + 单平台更新 + 全平台同步 -->
        <div v-if="currentPubs.length" class="rail-sec">
          <div class="rail-lbl">
            已发布实例
            <span class="spacer" />
            <el-button size="small" text type="primary" :loading="hub.publishing"
                       @click="hub.syncPending()">全平台同步</el-button>
          </div>
          <div v-for="p in currentPubs" :key="p.platform" class="pub-row">
            <span class="pub-plat">{{ platName(p.platform) }}</span>
            <el-tag size="small" :type="pubTagType(p.status)" effect="plain">{{ pubText(p.status) }}</el-tag>
            <a v-if="p.post_url" class="p-link" :href="p.post_url" target="_blank" rel="noopener">链接</a>
            <span class="spacer" />
            <el-button v-if="p.post_id" size="small" text :loading="hub.publishing"
                       @click="hub.updateRemote([p.platform])">更新这篇</el-button>
          </div>
          <div v-if="updatable.length" class="rail-hint warn">
            改过的正文还没同步到 {{ updatable.length }} 个平台
          </div>
        </div>
      </template>

      <EmptyState v-else title="先写一篇" desc="保存后这里会出现平台选择、真实发布与进度" :px="40" />
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { useHubStore } from '@/stores/hub'
import { useSettingsStore } from '@/stores/settings'
import EmptyState from './EmptyState.vue'

const hub = useHubStore()
const settings = useSettingsStore()

const currentId = computed(() => hub.currentId)
const platforms = computed(() => hub.platforms)

// 能力矩阵来自后端（/platforms.capabilities），不是前端猜的
const canPublish = (p) => p?.capabilities ? p.capabilities.publish !== false : true
const canUpdate = (p) => !!p?.capabilities?.update
const currentPubs = computed(() => hub.publications)
const updatable = computed(() => hub.updatable.filter(id => canUpdate(platforms.value.find(p => p.id === id))))
const isAiArticle = computed(() => hub.current?.source === 'ai')

// ---- 平台选择 + 分类记忆（沿用旧向导的本地记忆） ----
const PLAT_MARKS = {
  zhihu: '知', bilibili: 'B', cnblogs: '博', csdn: 'C', metaweblog: '站',
  juejin: '掘', oschina: '开', segmentfault: '思', toutiao: '头', wuyi_cto: 'CTO'
}
const CAT_OPTIONS = {
  juejin: ['后端', '前端', 'android', 'ios', '人工智能', '开发工具', '代码人生', '阅读'],
  csdn: ['后端与架构设计', '前端开发', '移动开发', '人工智能', '网络安全', '云计算', '大数据', '物联网', '数据库', '运维', '程序人生', '其他'],
}
const CAT_MEM_KEY = 'harbour.publish.categories'

const selected = ref([])
const platformSettings = reactive({})
const catOptions = CAT_OPTIONS
const mark = (p) => PLAT_MARKS[p.id] || p.name?.[0] || '?'

function loadCatMem() {
  try { return JSON.parse(localStorage.getItem(CAT_MEM_KEY) || '{}') } catch { return {} }
}
function rememberCategory() {
  try {
    const mem = {}
    for (const id of Object.keys(platformSettings)) {
      const cat = platformSettings[id]?.category
      if (cat) mem[id] = cat
    }
    localStorage.setItem(CAT_MEM_KEY, JSON.stringify(mem))
  } catch { /* 隐私模式静默 */ }
}
function ensureCfg(id) {
  if (!platformSettings[id]) {
    platformSettings[id] = { category: loadCatMem()[id] || '', tags: '', question_url: '' }
  }
}
function toggle(id) {
  const i = selected.value.indexOf(id)
  if (i >= 0) { selected.value.splice(i, 1); return }
  selected.value.push(id)
  ensureCfg(id)
}
function selectAll() {
  selected.value = platforms.value.filter(canPublish).map(p => p.id)
  selected.value.forEach(ensureCfg)
}

// ---- 登录态 ----
function isOnline(pf) {
  const a = hub.accounts.find(x => x.platform === pf)
  return !!a && a.status === 'logined'
}

// ---- 发布方式 / 模式 / 门禁确认 ----
const mode = ref('live')            // live | draft —— 真实上线是默认
const publishMode = ref('auto')     // auto | assist | manual（2026-10-14）
const aiAssist = ref(true)          // 勾选平台自带「AI 辅助」声明（默认开，不写正文）
const humanConfirmed = ref(false)
const running = ref(false)
const results = ref([])
const doneCount = ref(0)

// 换文章：重置结果（保留平台勾选，连续发稿更顺）
watch(currentId, () => { results.value = []; humanConfirmed.value = false })

async function runPublish() {
  if (!selected.value.length) { ElMessage.warning('先选要发到哪些平台'); return }
  // 全自动 + AI 稿 + 未开直发 → 必须勾选核验；中断/手动模式由用户亲自确认
  if (mode.value === 'live' && publishMode.value === 'auto' && isAiArticle.value
      && !settings.directPublish && !humanConfirmed.value) {
    ElMessage.warning('全自动发布 AI 稿需先勾选「我已核验」，或改用「中断改配置」亲自操作')
    return
  }
  running.value = true
  doneCount.value = 0
  results.value = []
  try {
    // 把「谁操作」与「AI 辅助声明」逐平台注入 settings
    const ps = JSON.parse(JSON.stringify(platformSettings))
    for (const id of selected.value) {
      ps[id] = ps[id] || {}
      ps[id].mode = publishMode.value
      ps[id].ai_assist = aiAssist.value
    }
    const done = await hub.publish(selected.value.slice(), mode.value === 'draft',
                                   ps, humanConfirmed.value, publishMode.value)
    if (!done) return
    // 任务终态：failed（门禁/平台拒）/ waiting_human（转人工）/ pending_user（中断等你）/ ok
    if (done.status === 'failed') {
      ElMessage.error(`发布未通过：${(done.error || done.message || '').slice(0, 120)}`)
      await hub.loadTasks()
      return
    }
    if (done.status === 'waiting_human') {
      ElMessage.warning('发布转入人工处理，去任务中心/发布中心继续')
      await hub.loadPendingHuman()
      return
    }
    const res = done.result || {}
    results.value = Array.isArray(res) ? res : (res.results || [])
    doneCount.value = results.value.length
    // 中断模式：浏览器已打开等你自己操作
    if (results.value.some(r => r.pending_user)) {
      ElMessage.success('平台页已打开（已预填），改好配置/传完附件后自己点发布')
      await Promise.all([hub.loadPendingHuman(), hub.loadAllPubs()])
      return
    }
    const okN = results.value.filter(r => r.ok).length
    if (mode.value === 'live') {
      ElMessage.success(`发布完成：成功 ${okN}/${results.value.length}（真实上线）`)
    } else {
      ElMessage.success(`草稿已存：${okN}/${results.value.length}`)
    }
    await hub.loadPendingHuman()
  } catch (e) {
    // 门禁 422 / 网络错误：api 拦截器已弹 detail，这里刷新任务列表即可
    await hub.loadTasks()
  } finally {
    running.value = false
  }
}

// ---- 格式化 ----
const platName = (id) => platforms.value.find(p => p.id === id)?.name || id
const pubTagType = (s) => s === 'ok' ? 'success'
  : (s === 'pending' || s === 'pending_user') ? 'warning'
  : s === 'failed' ? 'danger' : 'info'
const pubText = (s) => ({
  ok: '已发布', pending: '待同步', failed: '失败',
  pending_human: '待人工', pending_user: '等你确认'
}[s] || s)
</script>

<style scoped lang="scss">
.rail { height: 100%; }
.rail-body { padding: 4px 14px 16px; display: flex; flex-direction: column; gap: 4px; }

.rail-sec { padding: 10px 0; & + .rail-sec { border-top: 1px solid var(--line-soft); } }
.rail-lbl {
  display: flex; align-items: center; gap: 6px;
  font-size: var(--fs-sm); font-weight: 600; color: var(--tx-2);
  letter-spacing: .03em; margin-bottom: 10px;
  .spacer { flex: 1; }
}

.rail-plats { grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); }
.rail-plat {
  padding: 8px 10px;
  .n { font-size: var(--fs-sm); }
  .s { display: flex; align-items: center; gap: 5px; }
  .s.caps { color: var(--tx-4); }
  .cat-row { margin-top: 6px; }
  // 能力矩阵判定不支持发布的平台：置灰且不可点，而不是让用户点了才失败
  &.disabled {
    opacity: .5;
    cursor: not-allowed;
    filter: grayscale(1);
  }
}
.log-dot {
  width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0;
  &.ok { background: var(--ok); }
  &.off { background: var(--tx-4); }
}

.mode-group { width: 100%; display: flex;
  :deep(.el-radio-button) { flex: 1; }
  :deep(.el-radio-button__inner) { width: 100%; font-size: 12.5px; padding: 9px 4px; }
}
.rail-hint {
  margin-top: 8px; font-size: var(--fs-xs); color: var(--tx-3);
  &.ok { color: var(--tx-2); }
  &.warn { color: var(--warn); }
}

.ai-gate {
  margin: 6px 0; padding: 10px 12px;
  border: 1px solid rgba(232, 150, 74, .35);
  border-left: 3px solid var(--warn);
  border-radius: var(--r-sm);
  background: rgba(232, 150, 74, .06);
  display: flex; flex-direction: column; gap: 6px;
  b { font-size: var(--fs-sm); color: var(--tx-1); }
  .gate-sub { font-size: var(--fs-xs); color: var(--tx-3); line-height: 1.6; }
  .gate-ck { height: auto; white-space: normal; :deep(.el-checkbox__label) { font-size: 12px; line-height: 1.5; white-space: normal; } }
  &.direct {
    border-color: var(--accent-line); border-left-color: var(--accent);
    background: var(--accent-soft);
  }
}

.go-btn { width: 100%; font-weight: 600; }

.assist-ck {
  margin-top: 10px;
  height: auto;
  white-space: normal;
  :deep(.el-checkbox__label) { font-size: 12px; line-height: 1.5; white-space: normal; }
}

.res-row, .pub-row {
  display: flex; align-items: center; gap: 8px; padding: 5px 0;
  font-size: var(--fs-xs);
  .spacer { flex: 1; }
}
.pub-plat { color: var(--tx-2); font-weight: 500; min-width: 56px; }
.p-link { color: var(--accent); text-decoration: none; &:hover { text-decoration: underline; } }
.run-err { color: var(--bad); }
.dim { color: var(--tx-4); }
</style>
