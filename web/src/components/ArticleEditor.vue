<template>
  <div class="pane" style="height:100%">
    <div class="pane-head editor-head">
      <el-button v-if="showLibrary" size="small" :icon="Files" class="lib-btn"
                 title="打开文章库" aria-label="打开文章库" @click="$emit('open-library')" />
      <span class="title">{{ article.id ? '#' + article.id : '新文章' }}</span>
      <el-tag v-if="dirty" size="small" type="warning" effect="dark" class="dirty-tag">未保存</el-tag>
      <span class="spacer" />

      <!-- 视图切换（编辑/分屏/预览）+ AI 动作，全部收进一个「视图与 AI」下拉，避免平铺堆叠 -->
      <el-dropdown trigger="click" @command="onMore">
        <el-button size="small" :icon="Menu" class="more-btn">
          <span class="btxt">视图与 AI</span>
        </el-button>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item divided command="edit" :icon="Edit"
                               :class="{ 'is-active-cmd': view === 'edit' }">编辑</el-dropdown-item>
            <el-dropdown-item command="split" :icon="Operation"
                               :class="{ 'is-active-cmd': view === 'split' }">分屏</el-dropdown-item>
            <el-dropdown-item command="preview" :icon="View"
                               :class="{ 'is-active-cmd': view === 'preview' }">预览</el-dropdown-item>
            <el-dropdown-item divided command="rewrite" :icon="Aim">AI 改写</el-dropdown-item>
            <el-dropdown-item command="polish" :icon="Brush">AI 润色</el-dropdown-item>
            <el-dropdown-item divided command="translate" :icon="Paperclip">AI 翻译</el-dropdown-item>
            <el-dropdown-item command="prompts" :icon="Picture">配图提示</el-dropdown-item>
            <el-dropdown-item command="outline" :icon="List">大纲 / SEO</el-dropdown-item>
            <el-dropdown-item command="qa" :icon="DataAnalysis">内容质检</el-dropdown-item>
            <el-dropdown-item command="clone" :icon="DocumentCopy">克隆文章</el-dropdown-item>
            <el-dropdown-item divided command="versions" :icon="Clock">版本历史</el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>

      <!-- 发布会先自动保存（不需要"先保存再发布"），loading 由 store 驱动 -->
      <el-button size="small" :icon="Promotion" :loading="hub.publishing" @click="$emit('publish')">
        <span class="btxt">发布</span>
      </el-button>

      <el-button v-if="hub.updatable.length" size="small" :icon="Refresh"
                 :loading="hub.publishing" @click="$emit('sync-update')">
        <span class="btxt">同步更新</span>
      </el-button>

      <el-button size="small" type="primary" :icon="Check" :loading="saving" @click="$emit('save')">
        <span class="btxt">保存</span>
      </el-button>
    </div>

    <div class="pane-body editor-body">
      <el-input
        v-model="article.title"
        size="large"
        placeholder="文章标题"
        class="title-input"
        @input="touch"
      />

      <el-input
        v-model="article.summary"
        type="textarea"
        :rows="2"
        placeholder="摘要（可选，部分平台用它当描述）"
        @input="touch"
      />

      <div class="meta-row">
        <el-input v-model="article.tags" placeholder="标签，逗号分隔" class="tags-input" :prefix-icon="PriceTag" @input="touch" />
        <el-select v-model="article.status" class="status-sel" @change="touch">
          <el-option label="草稿" value="draft" />
          <el-option label="已发布" value="published" />
          <el-option label="待审" value="review" />
          <el-option label="归档" value="archived" />
        </el-select>
        <span class="spacer" />
        <span class="meta-chip">
          {{ chars }} 字 · 约 {{ Math.ceil(chars / 350) }} 分钟读完
        </span>
      </div>

      <!-- 发布设置：封面 + AI 辅助标识。发布前在这里点满，会随正文一起存库并被发布轨读取 -->
      <div class="pub-settings">
        <div class="ps-title">
          <span>发布设置</span>
          <span class="ps-hint">发布前请把该填的都填上</span>
          <span class="ps-state" :class="{ done: settingsDone }">
            {{ settingsDone ? '✓ 已点满' : `${doneCount}/4 已设置` }}
          </span>
        </div>
        <div class="ps-grid">
          <label class="ps-field ps-cover">
            <span class="ps-label">封面</span>
            <el-input v-model="article.cover" placeholder="图片 URL 或 data:image/…;base64,…" @input="touch">
              <template #prefix><Picture class="ps-icon" /></template>
            </el-input>
            <input type="file" accept="image/*" class="ps-file" @change="onPickCover" />
            <img v-if="coverPreview" :src="coverPreview" class="ps-preview" alt="封面预览" />
          </label>
          <label class="ps-field">
            <span class="ps-label">AI 辅助标识</span>
            <el-select v-model="article.ai_model" class="ps-select" clearable placeholder="是否用了 AI 辅助" @change="touch">
              <el-option label="纯人工撰写" value="human-written" />
              <el-option label="AI 仅辅助校对" value="human-written (AI 仅辅助校对)" />
              <el-option label="AI 起草 + 人工改写" value="ai-drafted (人工改写)" />
              <el-option label="AI 生成（需核验）" value="ai-generated" />
            </el-select>
          </label>
        </div>
      </div>

      <!-- Markdown 编辑器（md-editor-v3）：编辑/分屏/预览三态对应 preview=false/true/'preview' -->
      <div class="md-wrap">
        <MdEditor
          v-model="article.content_md"
          :preview="editorPreview"
          :theme="'dark'"
          language="zh-CN"
          :toolbars="toolbars"
          :on-upload-img="onUploadImg"
          class="md-editor"
          @on-change="touch"
        />
      </div>
    </div>
  </div>

  <!-- 版本历史抽屉 -->
  <el-drawer v-model="versionDrawer" :title="`版本历史 · #${currentId}`" size="min(520px, 94vw)" direction="rtl">
    <div v-if="versionsLoading" class="dim padding">加载中…</div>
    <div v-else-if="versionsList.length" class="ver-list">
      <div v-for="(v, i) in versionsList" :key="v.id" class="ver-card">
        <div class="vc-head">
          <el-tag size="small" :type="versionTagType(v.change_kind)" effect="plain">
            {{ versionKindLabel(v.change_kind) }}
          </el-tag>
          <span class="dim small">{{ fmtVerTime(v.created_at) }}</span>
          <span class="vc-note">{{ v.change_note || '—' }}</span>
        </div>
        <div class="vc-preview">{{ (v.title || '').slice(0, 50) || '（无标题）' }}</div>
        <div class="vc-ops">
          <el-button size="small" text @click="rollback(v)" :disabled="i === 0">回滚到此版本</el-button>
        </div>
      </div>
    </div>
    <div v-else class="dim padding">还没有版本历史（每次编辑都会自动存一份旧版快照）</div>
  </el-drawer>

  <!-- 内容质检 -->
  <el-dialog v-model="qaDialog" title="内容质检报告" width="min(560px, 94vw)">
    <div v-if="qaLoading" class="dim padding">质检中…</div>
    <div v-else-if="qaResult">
      <div class="qa-overview">
        <div class="qa-total" :class="qaTotalClass">
          <span class="qa-total-num">{{ qaResult.total }}</span>
          <span class="qa-total-label">{{ qaResult.label }}</span>
        </div>
        <div class="qa-sub-scores">
          <div class="qa-sub">可读性<b>{{ qaResult.readability.score }}</b></div>
          <div class="qa-sub">SEO<b>{{ qaResult.seo.score }}</b></div>
          <div class="qa-sub">重复<b>{{ qaResult.duplicates.length }}篇</b></div>
        </div>
      </div>
      <div v-if="qaResult.tips.length" class="qa-tips">
        <div class="qa-section-title">改进建议</div>
        <ul>
          <li v-for="(t, i) in qaResult.tips" :key="i">{{ t }}</li>
        </ul>
      </div>
      <div v-if="qaResult.duplicates.length" class="qa-dup">
        <div class="qa-section-title">库内相似文章</div>
        <div v-for="d in qaResult.duplicates" :key="d.id" class="qa-dup-item">
          #{{ d.id }} {{ d.title }} · 相似度 {{ (d.similarity * 100).toFixed(0) }}%
        </div>
      </div>
    </div>
  </el-dialog>

  <!-- AI 工具面板 -->
  <el-dialog v-model="aiToolDialog" :title="aiToolTitle" width="min(600px, 94vw)">
    <div v-if="aiToolLoading" class="dim padding">AI 生成中…</div>
    <div v-else-if="aiToolData">
      <!-- 翻译结果 -->
      <div v-if="aiToolKind === 'translate'">
        <el-alert type="success" :closable="false"
                  title="翻译完成（可复制后手动粘贴为新文章正文）" show-icon />
        <pre class="ai-result-box">{{ aiToolData.translated_md }}</pre>
      </div>
      <!-- 配图提示 -->
      <div v-else-if="aiToolKind === 'prompts'">
        <el-alert type="info" :closable="false" title="复制到 Midjourney / 通义万相 / ComfyUI" />
        <div v-for="(p, i) in (aiToolData.prompts || [])" :key="i" class="prompt-item">
          <pre>{{ p }}</pre>
          <el-button size="small" text @click="copyText(p)">复制</el-button>
        </div>
      </div>
      <!-- 大纲 -->
      <div v-else-if="aiToolKind === 'outline'">
        <ol class="outline-list">
          <li v-for="(o, i) in (aiToolData.outline || [])" :key="i"
              :style="{ 'margin-left': (o.level - 1) * 16 + 'px' }">
            {{ o.title }}
          </li>
        </ol>
      </div>
      <!-- SEO -->
      <div v-else-if="aiToolKind === 'seo'">
        <el-descriptions :column="1" border size="small">
          <el-descriptions-item label="SEO 标题">{{ aiToolData.seo_title }}</el-descriptions-item>
          <el-descriptions-item label="SEO 描述">{{ aiToolData.seo_description }}</el-descriptions-item>
          <el-descriptions-item label="URL Slug"><code>{{ aiToolData.slug }}</code></el-descriptions-item>
          <el-descriptions-item label="关键词">{{ aiToolData.keywords }}</el-descriptions-item>
        </el-descriptions>
      </div>
    </div>
  </el-dialog>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { MdEditor } from 'md-editor-v3'
import 'md-editor-v3/lib/style.css'
import { api } from '@/api'
import { Check, PriceTag, Aim, Brush, Menu, Edit, Operation, View, Promotion, Refresh, Files,
         Paperclip, Picture, List, DocumentCopy, Clock, DataAnalysis } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { useBreakpoint } from '@/composables/useBreakpoint'
import { useHubStore } from '@/stores/hub'

const props = defineProps({
  article: { type: Object, default: null },
  isNew: Boolean,
  dirty: Boolean,
  saving: Boolean,
  showLibrary: Boolean
})
const hub = useHubStore()
const emit = defineEmits(['save', 'touch', 'ai-rewrite', 'ai-polish', 'publish', 'sync-update', 'open-library'])

const { isMobile } = useBreakpoint()
// 手机默认只看编辑，分屏两栏根本没法用；桌面默认分屏
const view = ref(isMobile.value ? 'edit' : 'split')
// 用响应式断点监听：从桌面拖窄到手机时，若还停在分屏就切回编辑
watch(isMobile, m => { if (m && view.value === 'split') view.value = 'edit' })

// 编辑=纯编辑 / 分屏=左编右览 / 预览=纯预览（对应 md-editor-v3 的 preview prop）
const editorPreview = computed(() =>
  view.value === 'edit' ? false : view.value === 'preview' ? 'preview' : true)

// 常用工具栏（砍掉导入导出/目录等低频项，保持轻量）
const toolbars = ref(['bold', 'italic', 'strikethrough', 'heading',
  'quote', 'ul', 'ol', 'code', 'link', 'image', 'table',
  'revoke', 'next', 'preview', 'expand'])

const chars = computed(() => (props.article?.content_md || '').length)

// ---- 发布设置：发布前「点满」检查 ----
// 四项：封面 / 标签 / 摘要 / AI 辅助标识。缺一项就不算点满，发布轨会提示。
const settingFilled = computed(() => ({
  cover: !!(props.article?.cover || '').trim(),
  tags: !!(props.article?.tags || '').trim(),
  summary: !!(props.article?.summary || '').trim(),
  ai: !!(props.article?.ai_model || '').trim(),
}))
const doneCount = computed(() => Object.values(settingFilled.value).filter(Boolean).length)
const settingsDone = computed(() => doneCount.value === 4)
const coverPreview = computed(() => {
  const c = props.article?.cover || ''
  return /^(https?:\/\/|data:image\/)/.test(c) ? c : ''
})

function onPickCover(e) {
  const f = e.target.files?.[0]
  if (!f) return
  const reader = new FileReader()
  reader.onload = () => {
    props.article.cover = String(reader.result || '')
    emit('touch')
  }
  reader.readAsDataURL(f)
}
const qaTotalClass = computed(() => {
  const t = qaResult.value?.total ?? 0
  return 'qa-' + (t >= 70 ? 'good' : t >= 50 ? 'ok' : 'bad')
})
const touch = () => emit('touch')

// 编辑器内粘贴/拖拽的图片：先入本地媒体库（内容寻址去重），再回写稳定 URL。
// 直接塞 base64 会让正文膨胀几十倍，也会让平台侧富文本编辑器拒收。
async function onUploadImg(files, callback) {
  const urls = []
  for (const file of files) {
    try {
      const r = await api.mediaUpload(file)
      urls.push(r.url || `/api/v1/hub/media/${r.sha256}.png`)
    } catch {
      ElMessage.error(`图片上传失败：${file.name}`)
    }
  }
  callback(urls)
}

// 版本历史抽屉状态
const versionDrawer = ref(false)
const versionsLoading = ref(false)
const versionsList = ref([])

// AI 工具面板状态
const aiToolDialog = ref(false)
const aiToolTitle = ref('')
const aiToolLoading = ref(false)
const aiToolData = ref(null)
const aiToolKind = ref('')

// 打开版本历史
async function openVersions() {
  if (!props.article?.id) return
  versionDrawer.value = true
  versionsLoading.value = true
  try {
    versionsList.value = await hub.loadVersions(props.article.id)
  } catch { versionsList.value = [] }
  versionsLoading.value = false
}

// 回滚版本
async function rollback(v) {
  if (!props.article?.id) return
  await hub.rollbackVersion(props.article.id, v.id)
  versionsList.value = await hub.loadVersions(props.article.id)
}

// 内容质检
const qaDialog = ref(false)
const qaLoading = ref(false)
const qaResult = ref(null)
async function openQa() {
  if (!props.article?.id) return
  qaDialog.value = true
  qaLoading.value = true
  qaResult.value = null
  try {
    qaResult.value = await hub.qaArticle(props.article.id)
  } catch { qaResult.value = null }
  qaLoading.value = false
}

// 打开 AI 工具面板
async function openAiTool(kind) {
  if (!props.article?.id) return
  aiToolKind.value = kind
  aiToolDialog.value = true
  aiToolLoading.value = true
  aiToolData.value = null
  const titles = { translate: 'AI 翻译', prompts: '配图提示生成', outline: '文章大纲', seo: 'SEO 元数据' }
  aiToolTitle.value = titles[kind] || 'AI 工具'
  try {
    if (kind === 'translate') {
      aiToolData.value = await hub.aiTranslate(props.article.id, 'en')
    } else if (kind === 'prompts') {
      aiToolData.value = await hub.aiImagePrompts(props.article.id, 3)
    } else if (kind === 'outline') {
      aiToolData.value = await hub.aiOutline(props.article.id)
    } else if (kind === 'seo') {
      aiToolData.value = await hub.aiSeo(props.article.id)
    }
  } catch { aiToolData.value = null }
  aiToolLoading.value = false
}

function copyText(t) {
  navigator.clipboard.writeText(t).catch(() => {})
}

// 版本历史标签
const versionKindLabel = (k) =>
  ({ edit: '编辑', ai_rewrite: 'AI 改写', ai_polish: 'AI 润色',
     rollback: '回滚', import: '导入', 'pre-rollback': '回滚前快照' }[k] || k)
const versionTagType = (k) =>
  k === 'edit' ? 'info' : k === 'ai_rewrite' ? 'primary' : k === 'rollback' ? 'warning' : 'info'
const fmtVerTime = (ts) => {
  if (!ts) return '—'
  const d = new Date(ts * 1000)
  const pad = n => String(n).padStart(2, '0')
  return `${d.getMonth() + 1}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

// 「视图与 AI」下拉：命令分发
function onMore(cmd) {
  switch (cmd) {
    case 'edit': view.value = 'edit'; break
    case 'split': view.value = 'split'; break
    case 'preview': view.value = 'preview'; break
    case 'rewrite': emit('ai-rewrite'); break
    case 'polish': emit('ai-polish'); break
    case 'translate': openAiTool('translate'); break
    case 'prompts': openAiTool('prompts'); break
    case 'outline': openAiTool('outline'); break
    case 'seo': openAiTool('seo'); break
    case 'qa': openQa(); break
    case 'clone': hub.cloneArticle(props.article.id); break
    case 'versions': openVersions(); break
  }
}
</script>

<style scoped lang="scss">
.is-active-cmd {
  background: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
  font-weight: 600;
}

.dirty-tag {
  animation: popIn .2s var(--ease-out);
}
.title-input :deep(.el-input__inner) {
  font-size: var(--fs-xl);
  font-weight: 600;
  letter-spacing: -.01em;
}

.editor-body {
  padding: 14px 16px 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.meta-row {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
}
.tags-input { max-width: 300px; }
.status-sel { width: 116px; }
.meta-row .spacer { flex: 1; }

// 发布设置：封面 / AI 辅助标识（发布前点满）
.pub-settings {
  margin: 2px 0 10px;
  padding: 8px 10px 10px;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: var(--bg-soft);
}
.ps-title {
  display: flex; align-items: baseline; gap: 8px;
  font-size: 12px; font-weight: 600; color: var(--text);
  margin-bottom: 7px;
}
.ps-hint { color: var(--text-dim, var(--muted)); font-weight: 400; font-size: 11px; }
.ps-state { margin-left: auto; font-weight: 500; font-size: 11px; color: var(--warning, #d8963a); }
.ps-state.done { color: var(--success, #4caf7d); }
.ps-grid { display: flex; gap: 10px; align-items: flex-start; flex-wrap: wrap; }
.ps-field { display: flex; flex-direction: column; gap: 4px; flex: 1 1 240px; min-width: 200px; }
.ps-label { font-size: 11px; color: var(--muted); }
.ps-select { width: 100%; }
.ps-icon { width: 14px; height: 14px; }
.ps-cover { position: relative; }
.ps-file {
  font-size: 11px; color: var(--muted);
  &::file-selector-button {
    margin-right: 6px; padding: 2px 8px; border: 1px solid var(--line-soft);
    border-radius: 5px; background: var(--panel); color: var(--text); cursor: pointer;
  }
}
.ps-preview {
  width: 96px; height: 64px; object-fit: cover;
  border-radius: 5px; border: 1px solid var(--line-soft); margin-top: 2px;
}

// Markdown 编辑器容器撑满剩余高度
.md-wrap { flex: 1; min-height: 0; }

// 让 md-editor-v3 融入"夜墨金箔"暗色体系
.md-editor {
  height: 100%;

  :deep() {
    // 编辑器本体
    --md-bk-color: var(--surface);
    --md-bk-color-light: var(--surface-2);
    --md-bk-color-dark: var(--surface-3);
    --md-border-color: var(--line);
    --md-color: var(--tx-2);
    --md-hover-bg-color: var(--surface-3);
    --md-active-bg-color: var(--surface-3);
    --md-accent-color: var(--accent);
    --md-accent-color-light: var(--accent-soft);
    --md-accent-color-dark: var(--accent-hi);
    --md-code-bk-color: var(--surface-3);
    --md-content-bk-color: var(--surface);
    --md-meta-color: var(--tx-4);
    --md-scrollbar-bg-color: transparent;
    --md-scrollbar-thumb-bg-color: var(--line-strong);
    border: 1px solid var(--line);
    border-radius: 10px;
    overflow: hidden;
  }
}

// 版本历史
.ver-list { display: flex; flex-direction: column; gap: 10px; }
.ver-card {
  border: 1px solid var(--line); border-radius: 10px; padding: 10px 14px;
  .vc-head { display: flex; align-items: center; gap: 10px; }
  .vc-note { color: var(--tx-3); font-size: 11px; flex: 1; overflow: hidden;
    text-overflow: ellipsis; white-space: nowrap; }
  .vc-preview { font-size: 12px; color: var(--tx-2); margin: 6px 0; }
}

// AI 工具
.ai-result-box {
  background: var(--surface-3); border-radius: 8px; padding: 10px;
  font-size: 12px; color: var(--tx-2); max-height: 360px; overflow: auto;
  white-space: pre-wrap; word-break: break-all; margin-top: 10px;
}
.prompt-item {
  border: 1px solid var(--line); border-radius: 8px; padding: 8px 10px; margin-top: 8px;
  pre { font-size: 12px; color: var(--tx-2); margin: 0 0 6px; word-break: break-all; white-space: pre-wrap; }
}
.outline-list { padding-left: 18px; color: var(--tx-2); font-size: 13px;
  li { line-height: 1.8; }
}

// 内容质检
.qa-overview { display: flex; align-items: center; gap: 18px; margin-bottom: 14px; }
.qa-total {
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  width: 88px; height: 88px; border-radius: 50%;
  background: var(--surface-3); border: 2px solid var(--line);
  .qa-total-num { font-size: 26px; font-weight: 700; }
  .qa-total-label { font-size: 11px; color: var(--tx-3); }
  &.qa-good { border-color: #10b981; .qa-total-num { color: #10b981; } }
  &.qa-ok { border-color: #f59e0b; .qa-total-num { color: #f59e0b; } }
  &.qa-bad { border-color: #ef4444; .qa-total-num { color: #ef4444; } }
}
.qa-sub-scores { display: flex; flex-direction: column; gap: 4px; flex: 1; }
.qa-sub {
  display: flex; justify-content: space-between; font-size: 12px; color: var(--tx-3);
  padding: 4px 10px; background: var(--surface-3); border-radius: 6px;
  b { color: var(--tx-1); font-size: 13px; }
}
.qa-section-title { font-size: 12px; color: var(--tx-1); font-weight: 600; margin: 12px 0 6px; }
.qa-tips ul { padding-left: 18px; font-size: 12px; color: var(--tx-3);
  li { line-height: 1.7; }
}
.qa-dup-item { font-size: 11px; color: var(--tx-3); padding: 3px 0; }

.dim { color: var(--tx-4); }
.padding { padding: 20px; }
</style>
