<template>
  <div class="write-wrap" :class="layoutClass">
    <!-- 左：文章库（≥1024 常驻） -->
    <ArticleList
      v-if="listDocked"
      :articles="hub.articles"
      :current-id="hub.currentId"
      :loading="hub.loadingList"
      @open="onOpen"
      @new-article="hub.newArticle()"
    />

    <!-- 中：编辑器 -->
    <ArticleEditor
      v-if="hub.current"
      :article="hub.current"
      :is-new="!hub.currentId"
      :dirty="hub.dirty"
      :saving="hub.saving"
      :show-library="!listDocked"
      @open-library="listDrawer = true"
      @save="hub.save()"
      @touch="hub.dirty = true"
      @publish="onPublish"
      @sync-update="hub.updateRemote(hub.updatable)"
      @ai-rewrite="onRewrite"
      @ai-polish="onPolish"
    />
    <div v-else class="pane pane-center">
      <EmptyState
        :px="58"
        title="墨迹未干"
        desc="挑一篇接着写，或新起一篇。写完在发布轨里选平台，一发出网。"
      >
        <el-button type="primary" :icon="Plus" @click="hub.newArticle()">起一篇新文</el-button>
        <el-button :icon="MagicStick" @click="$emit('ai-write')">让 AI 落笔</el-button>
        <el-button v-if="!listDocked && hub.articles.length" :icon="Files"
                   @click="listDrawer = true">打开文章库</el-button>
      </EmptyState>
    </div>

    <!-- 右：发布轨（≥1280 常驻） -->
    <PublishRail v-if="sideDocked" />
  </div>

  <!-- 窄屏：列表抽屉 -->
  <el-drawer v-if="!listDocked" v-model="listDrawer" direction="ltr" size="86%" :with-header="false">
    <ArticleList
      :articles="hub.articles"
      :current-id="hub.currentId"
      :loading="hub.loadingList"
      @open="id => { onOpen(id); listDrawer = false }"
      @new-article="hub.newArticle()"
    />
  </el-drawer>

  <!-- 窄屏：发布轨抽屉（点编辑器「发布」打开） -->
  <el-drawer v-if="!sideDocked" v-model="railDrawer" direction="rtl"
             size="min(440px, 94vw)" :with-header="false">
    <PublishRail />
  </el-drawer>
</template>

<script setup>
import { ref, computed } from 'vue'
import { ElMessageBox } from 'element-plus'
import { Plus, MagicStick, Files } from '@element-plus/icons-vue'
import { useBreakpoint } from '@/composables/useBreakpoint'
import { useHubStore } from '@/stores/hub'
import { api } from '@/api'
import ArticleList from '@/components/ArticleList.vue'
import ArticleEditor from '@/components/ArticleEditor.vue'
import PublishRail from '@/components/PublishRail.vue'
import EmptyState from '@/components/EmptyState.vue'

defineEmits(['ai-write'])

const hub = useHubStore()
const { listDocked, sideDocked } = useBreakpoint()

const listDrawer = ref(false)
const railDrawer = ref(false)

const layoutClass = computed(() =>
  sideDocked.value ? 'three' : listDocked.value ? 'two' : 'one')

async function onOpen(id) {
  await hub.open(id)
  listDrawer.value = false
}

// 「发布」：宽屏滚动高亮右轨（常驻无需动作）；窄屏打开发布抽屉
function onPublish() {
  if (!sideDocked.value) railDrawer.value = true
}

async function onRewrite() {
  if (!hub.currentId) return
  try {
    const { value } = await ElMessageBox.prompt(
      '例如：多给两个代码示例 / 语气更口语 / 压缩到 800 字',
      'AI 改写', { inputPlaceholder: '改写要求', inputType: 'textarea' }
    )
    await hub.save()
    await api.aiRewrite(hub.currentId, value)
    await hub.open(hub.currentId)
  } catch { /* 用户取消 */ }
}

async function onPolish() {
  if (!hub.currentId) return
  await hub.save()
  await api.aiPolish(hub.currentId)
  await hub.open(hub.currentId)
}
</script>

<style scoped lang="scss">
.write-wrap {
  flex: 1;
  min-height: 0;
  display: grid;
  gap: 12px;
  padding: 14px 16px;
  overflow: hidden;

  &.three { grid-template-columns: 272px minmax(0, 1fr) 336px; }
  &.two { grid-template-columns: 272px minmax(0, 1fr); }
  &.one { grid-template-columns: minmax(0, 1fr); }

  > * { animation: fadeUp .4s var(--ease-out) both; min-height: 0; }
  > *:nth-child(2) { animation-delay: .05s; }
  > *:nth-child(3) { animation-delay: .1s; }
}
.pane-center {
  align-items: center;
  justify-content: center;
}

@media (max-width: 1023px) {
  .write-wrap { padding: 10px; gap: 10px; }
}
</style>
