<template>
  <div class="ai-panel">
    <!-- 状态 + 门禁开关 -->
    <div class="status-bar">
      <span class="pill">
        <span class="dot" :class="settings.ready ? 'ok' : 'off'" />
        {{ settings.ready ? 'AI 已就绪' : 'AI 未配置' }}
      </span>
      <span class="dim small" v-if="legacySet">
        检测到旧版单 Provider 配置（AI_API_KEY 环境变量），仍可用
      </span>
      <span class="spacer" />
      <el-button size="small" :icon="Refresh" :loading="settings.loading" @click="settings.loadAI()">重新读取</el-button>
    </div>

    <!-- Provider 列表 -->
    <el-collapse v-model="opened" class="prov-collapse">
      <el-collapse-item v-for="p in settings.providers" :key="p.key" :name="p.key">
        <template #title>
          <div class="pv-title">
            <b>{{ p.name }}</b>
            <el-tag size="small" :type="p.available ? 'success' : 'info'" effect="plain">
              {{ p.available ? '可用' : '未生效' }}
            </el-tag>
            <el-tag size="small" effect="plain" type="warning" v-if="p.key_source === 'env'">密钥来自环境变量</el-tag>
            <span class="dim small">{{ keyState(p) }}</span>
            <span class="price-tag">{{ priceText(p.price_level) }}</span>
          </div>
        </template>

        <div class="pv-body" v-if="drafts[p.key]">
          <el-form label-width="96px" size="default">
            <el-form-item label="API 密钥">
              <div class="key-row">
                <el-input v-model="drafts[p.key].api_key" type="password" show-password
                          :placeholder="p.key_set ? `已配置（${p.key_masked}），留空不改` : '粘贴 API Key'"
                          :disabled="p.key_source === 'env'" />
                <el-button v-if="p.key_set && p.key_source === 'config'" size="small" text type="danger"
                           @click="clearKey(p)">清除</el-button>
              </div>
              <div class="dim small" v-if="p.key_source === 'env'">
                密钥由环境变量提供，改动请去系统环境变量（此处只读）
              </div>
            </el-form-item>

            <el-form-item label="接口地址">
              <el-input v-model="drafts[p.key].base_url" :placeholder="p.base_url || 'https://.../v1'" />
            </el-form-item>

            <el-form-item label="默认模型">
              <el-select v-model="drafts[p.key].default_model" filterable allow-create
                         placeholder="选或手填模型名" style="width:100%">
                <el-option v-for="m in p.models" :key="m" :label="m" :value="m" />
              </el-select>
            </el-form-item>

            <el-form-item label="模型清单">
              <el-input v-model="drafts[p.key].modelsText" type="textarea" :rows="2"
                        placeholder="逗号分隔，例如：deepseek-chat, deepseek-reasoner" />
            </el-form-item>

            <el-form-item label="价格级别">
              <el-radio-group v-model="drafts[p.key].price_level">
                <el-radio-button value="economy">经济</el-radio-button>
                <el-radio-button value="standard">均衡</el-radio-button>
                <el-radio-button value="premium">旗舰</el-radio-button>
              </el-radio-group>
              <span class="dim small" style="margin-left:10px">决定任务自动路由：写稿走旗舰、摘要/标签走经济</span>
            </el-form-item>

            <el-form-item>
              <el-button type="primary" :loading="savingKey === p.key" @click="saveProvider(p)">保存</el-button>
              <el-button :loading="settings.testing === p.key" @click="testProvider(p)">测试连接</el-button>
              <span v-if="testResult[p.key]" class="test-res" :class="{ ok: testResult[p.key].ok }">
                {{ testResult[p.key].ok
                    ? `连通 ✓ ${testResult[p.key].latency_ms}ms · ${testResult[p.key].model}`
                    : `失败：${testResult[p.key].error}` }}
              </span>
            </el-form-item>
          </el-form>
        </div>
      </el-collapse-item>
    </el-collapse>

    <!-- 合规门禁 -->
    <div class="sec">
      <div class="sec-title">合规门禁（AIGC）</div>
      <el-form label-width="150px" size="default">
        <el-form-item label="AI 稿直发">
          <el-switch v-model="directPublish" :loading="savingDirect"
                     @change="onToggleDirect" />
          <span class="dim small" style="margin-left:10px">
            关闭（默认）：AI 稿正式上线需在前端勾选「我已核验」；开启：安全扫描 + 双模型二审 + AIGC 标识后直接上线
          </span>
        </el-form-item>
        <el-form-item label="二审模型（可选）">
          <div class="key-row">
            <el-input v-model="reviewModel" placeholder="例如 glm-4-air；留空走本地 heuristic 审查" />
            <el-button :loading="savingReview" @click="saveReview">保存</el-button>
          </div>
          <div class="dim small">配置后：发布前用第二模型审一遍（事实/口径/合规），失败自动降级本地审查</div>
        </el-form-item>
        <el-form-item label="门禁状态">
          <el-tag size="small" :type="settings.review.gate_enabled ? 'success' : 'danger'" effect="plain">
            {{ settings.review.gate_enabled ? '已启用' : '已关闭' }}
          </el-tag>
          <span class="dim small" style="margin-left:10px">
            高危词 {{ settings.review.dangerous_terms || 0 }} 条 · 最近拒绝 {{ (settings.aiGate?.recent_denied || []).length }} 次
          </span>
        </el-form-item>
      </el-form>
    </div>

    <!-- 任务路由说明 -->
    <div class="sec">
      <div class="sec-title">任务自动路由（按价格级别选 Provider）</div>
      <div class="route-grid">
        <span v-for="(level, task) in settings.routing" :key="task" class="route-chip">
          {{ taskText(task) }} → {{ priceText(level) }}
        </span>
      </div>
    </div>

    <!-- 写作风格档案 -->
    <div class="sec">
      <div class="sec-title">写作风格档案</div>
      <div class="dim small">
        文件：<code class="mono">{{ settings.aiConfig?.style?.file || '—' }}</code>
        （{{ settings.aiConfig?.style?.loaded ? '已加载，会注入每次 AI 写稿' : '未找到/为空，AI 使用内置文风' }}）
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, watch, onMounted } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { useSettingsStore } from '@/stores/settings'

const settings = useSettingsStore()

const opened = ref([])
const drafts = reactive({})
const testResult = reactive({})
const savingKey = ref('')
const savingDirect = ref(false)
const savingReview = ref(false)
const reviewModel = ref('')
const directPublish = ref(false)

const legacySet = computed(() => !!settings.aiConfig?.legacy?.api_key_set)

// 配置到达/刷新时重建草稿（避免输入被覆盖：只在初次或显式刷新时重建）
watch(() => settings.aiConfig, (cfg) => {
  if (!cfg) return
  reviewModel.value = cfg.review?.model || ''
  directPublish.value = !!cfg.review?.ai_direct_publish
  for (const p of cfg.providers || []) {
    if (drafts[p.key]) continue   // 已有草稿不覆盖
    drafts[p.key] = {
      api_key: '',
      base_url: p.base_url || '',
      default_model: p.default_model || '',
      modelsText: (p.models || []).join(', '),
      price_level: p.price_level || 'standard',
    }
  }
}, { immediate: true })

onMounted(() => { if (!settings.aiConfig) settings.loadAI() })

const keyState = (p) => p.key_set ? `密钥已配置（${p.key_masked}）` : '密钥未配置'
const priceText = (lv) => ({ economy: '经济', standard: '均衡', premium: '旗舰' }[lv] || lv)
const taskText = (t) => ({
  write: '写稿', rewrite: '改写', polish: '润色', summarize: '摘要', tags: '标签',
  translate: '翻译', image_prompts: '配图', outline: '大纲', seo: 'SEO',
}[t] || t)

async function saveProvider(p) {
  const d = drafts[p.key]
  if (!d) return
  savingKey.value = p.key
  try {
    const patch = {
      providers: {
        [p.key]: {
          base_url: d.base_url || undefined,
          default_model: d.default_model || undefined,
          price_level: d.price_level,
          models: d.modelsText.split(/[,，]/).map(s => s.trim()).filter(Boolean),
        }
      }
    }
    // 密钥语义：留空=不改；有值=设置（清除走 clearKey）
    if (d.api_key && d.api_key.trim()) patch.providers[p.key].api_key = d.api_key.trim()
    await settings.saveAI(patch)
    d.api_key = ''
    ElMessage.success(`${p.name} 配置已保存`)
  } finally { savingKey.value = '' }
}

async function clearKey(p) {
  await settings.saveAI({ providers: { [p.key]: { api_key: '' } } })
  ElMessage.success(`${p.name} 密钥已清除`)
}

async function testProvider(p) {
  const d = drafts[p.key] || {}
  const r = await settings.testProvider({
    provider: p.key,
    model: d.default_model || '',
    base_url: d.base_url || '',
    api_key: d.api_key || '',   // 留空则后端用已存密钥
  })
  testResult[p.key] = r
}

async function onToggleDirect(v) {
  savingDirect.value = true
  try {
    await settings.saveAI({ ai_direct_publish: v })
    ElMessage.success(v ? '已开启 AI 稿直发' : '已关闭 AI 稿直发（回到人工核验）')
  } catch { directPublish.value = !v } finally { savingDirect.value = false }
}

async function saveReview() {
  savingReview.value = true
  try {
    await settings.saveAI({ review_model: reviewModel.value.trim() })
    ElMessage.success(reviewModel.value.trim() ? '二审模型已保存' : '已关闭 LLM 二审（走本地审查）')
  } finally { savingReview.value = false }
}
</script>

<style scoped lang="scss">
.ai-panel { display: flex; flex-direction: column; gap: 16px; padding-top: 8px; }

.status-bar { display: flex; align-items: center; gap: 12px; }

.pv-title {
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  padding-right: 8px;
  b { font-size: var(--fs-md); color: var(--tx-1); }
  .price-tag {
    margin-left: auto; font-size: 11px; color: var(--accent);
    background: var(--accent-soft); border: 1px solid var(--accent-line);
    padding: 1px 8px; border-radius: 9px;
    // 窄屏下不允许被挤成竖排（实测 390px 时「经济」两个字竖着堆）
    white-space: nowrap; flex-shrink: 0; line-height: 1.6;
  }
}

@media (max-width: 767px) {
  // 折叠头在窄屏允许换行，否则长 Provider 名（Anthropic (OpenAI-compat proxy)）会把价格标签挤没
  :deep(.el-collapse-item__header) { height: auto; min-height: 48px; line-height: 1.5; padding: 8px 6px; }
  .pv-body { max-width: none; }
}
:deep(.el-collapse-item__header) { padding-left: 6px; }
.pv-body { padding: 6px 8px 2px; max-width: 720px; }

.key-row { display: flex; gap: 8px; width: 100%; align-items: flex-start; }

.test-res {
  margin-left: 10px; font-size: var(--fs-xs); color: var(--bad);
  &.ok { color: var(--ok); }
}

.sec { border-top: 1px solid var(--line-soft); padding-top: 14px; }
.sec-title { font-size: var(--fs-sm); font-weight: 600; color: var(--tx-2); margin-bottom: 12px; }

.route-grid { display: flex; flex-wrap: wrap; gap: 8px; }
.route-chip {
  font-size: var(--fs-xs); color: var(--tx-2);
  background: var(--surface-2); border: 1px solid var(--line);
  border-radius: 999px; padding: 3px 12px;
}

.dim { color: var(--tx-4); }
.small { font-size: var(--fs-xs); }
.mono { font-family: var(--font-mono); font-size: 11px; color: var(--tx-2); }
</style>
