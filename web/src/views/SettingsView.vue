<template>
  <div class="settings-wrap">
    <div class="pane set-pane">
      <div class="pane-head">
        <span class="title">设置</span>
        <span class="spacer" />
        <span class="dim small set-hint">配置一次，长期有效：AI 模型 / 平台账号 / 定时 / 通知</span>
      </div>
      <div class="pane-body">
        <el-tabs v-model="tab" class="set-tabs">
          <el-tab-pane label="AI 模型" name="ai" lazy>
            <AIPanel />
          </el-tab-pane>
          <el-tab-pane label="平台账号" name="accounts" lazy>
            <AccountsPanel />
          </el-tab-pane>
          <el-tab-pane label="定时发布" name="schedules" lazy>
            <SchedulesPanel />
          </el-tab-pane>
          <el-tab-pane label="通知 / Webhook" name="webhooks" lazy>
            <WebhooksPanel />
          </el-tab-pane>
          <el-tab-pane label="标签治理" name="tags" lazy>
            <TagsPanel />
          </el-tab-pane>
        </el-tabs>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import AIPanel from '@/components/settings/AIPanel.vue'
import AccountsPanel from '@/components/settings/AccountsPanel.vue'
import SchedulesPanel from '@/components/settings/SchedulesPanel.vue'
import WebhooksPanel from '@/components/settings/WebhooksPanel.vue'
import TagsPanel from '@/components/settings/TagsPanel.vue'

const tab = ref('ai')
</script>

<style scoped lang="scss">
.settings-wrap {
  flex: 1; min-height: 0; display: flex; padding: 14px 16px;
}
.set-pane { flex: 1; }
:deep(.el-tabs__header) { padding: 0 18px; margin-bottom: 4px; }
:deep(.el-tabs__content) { padding: 8px 18px 18px; overflow-y: auto; }
.dim { color: var(--tx-3); }
.small { font-size: var(--fs-xs); }
.pane-head { flex-wrap: nowrap; }
.pane-head .title { white-space: nowrap; flex-shrink: 0; }

@media (max-width: 767px) {
  .settings-wrap { padding: 8px; }
  // 窄屏没有横向空间放这句解释，藏掉才能让标题一行显示（否则「设置」被挤成竖排）
  .set-hint { display: none; }
  :deep(.el-tabs__header) { padding: 0 10px; }
  :deep(.el-tabs__content) { padding: 8px 10px 14px; }
}
</style>
