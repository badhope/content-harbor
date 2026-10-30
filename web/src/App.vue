<template>
  <el-config-provider :locale="zhCn">
  <div class="app">
    <!-- 全局动作进度条：加载/保存/发布任一进行中就亮 -->
    <div class="top-progress" :class="{ show: hub.loadingList || hub.saving || hub.publishing }" />

    <div class="shell">
      <SideNav
        :view="hub.view"
        :ai-ready="hub.aiReady"
        :wait-count="waitCount"
        :published-count="publishedCount"
        @switch="hub.switchView($event)"
        @new="newArticle"
      />

      <main class="shell-main">
        <!-- 视图切换统一用 v-if：v-show 节点紧邻 v-if 链会被编译成同一分支而失效；
             编辑态由 hub store 持有（current/dirty/saving），切回不丢 -->
        <WriteView v-if="hub.view === 'write'" @ai-write="aiDialog = true" />
        <PublishCenterView v-if="hub.view === 'publish'" />
        <TasksView v-else-if="hub.view === 'tasks'" />
        <SettingsView v-else-if="hub.view === 'settings'" />
      </main>
    </div>

    <AIWriteDialog v-model="aiDialog" :platforms="hub.platforms"
                   :ready="hub.aiReady" @done="onAiDone" />
  </div>
  </el-config-provider>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount, defineAsyncComponent } from 'vue'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import { useHubStore } from '@/stores/hub'
import SideNav from '@/components/SideNav.vue'

// 视图按需加载：WriteView 拉 md-editor-v3（~900KB），PublishCenterView 拉
// echarts（~490KB）。整包静态 import 会让首屏把两者都下载完才能渲染，
// 而用户一次只看一个视图。改异步组件后首屏只付当前视图的成本。
const WriteView = defineAsyncComponent(() => import('@/views/WriteView.vue'))
const PublishCenterView = defineAsyncComponent(() => import('@/views/PublishCenterView.vue'))
const TasksView = defineAsyncComponent(() => import('@/views/TasksView.vue'))
const SettingsView = defineAsyncComponent(() => import('@/views/SettingsView.vue'))
const AIWriteDialog = defineAsyncComponent(() => import('@/components/AIWriteDialog.vue'))

const hub = useHubStore()

const aiDialog = ref(false)

const waitCount = computed(() => hub.pendingHuman.length + hub.waitingTasks.length)
// 列表已分页，计数一律取服务端统计，避免只统计到已加载的那一页。
// 用 published_articles（文章数）而不是 published（平台实例数）——侧栏写的是「篇」。
const publishedCount = computed(() => hub.stats?.published_articles || 0)

onMounted(() => hub.boot())
onBeforeUnmount(() => hub.dispose())

function newArticle() {
  hub.switchView('write')
  hub.newArticle()
}

async function onAiDone(id) {
  hub.switchView('write')
  if (id) {
    await hub.open(id)
    // AI 写完 → 自动滚到发布轨，让用户立刻选平台发布（少一步找位置）
    await new Promise(r => setTimeout(r, 300))
    const rail = document.querySelector('.rail')
    if (rail) rail.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }
}
</script>
