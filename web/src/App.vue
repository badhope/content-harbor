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
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import { useHubStore } from '@/stores/hub'
import SideNav from '@/components/SideNav.vue'
import WriteView from '@/views/WriteView.vue'
import PublishCenterView from '@/views/PublishCenterView.vue'
import TasksView from '@/views/TasksView.vue'
import SettingsView from '@/views/SettingsView.vue'
import AIWriteDialog from '@/components/AIWriteDialog.vue'

const hub = useHubStore()

const aiDialog = ref(false)

const waitCount = computed(() => hub.pendingHuman.length + hub.waitingTasks.length)
const publishedCount = computed(() => hub.articles.filter(a => a.status === 'published').length)

onMounted(() => hub.boot())
onBeforeUnmount(() => hub.dispose())

function newArticle() {
  hub.switchView('write')
  hub.newArticle()
}

async function onAiDone(id) {
  hub.switchView('write')
  if (id) await hub.open(id)
}
</script>
