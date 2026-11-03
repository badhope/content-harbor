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
          <el-tab-pane label="外观" name="appearance" lazy>
            <div class="appearance-panel">
              <div class="ap-row">
                <div>
                  <div class="ap-title">界面主题</div>
                  <div class="dim small">暗色跟随本地偏好保存；Markdown 编辑器的独立主题后续接入。</div>
                </div>
                <el-radio-group :model-value="theme" @update:model-value="onTheme">
                  <el-radio-button value="light">亮色</el-radio-button>
                  <el-radio-button value="dark">暗色</el-radio-button>
                </el-radio-group>
              </div>
            </div>
          </el-tab-pane>
          <el-tab-pane label="访问令牌" name="token" lazy>
            <div class="token-panel">
              <p class="dim small">
                仅当后端启用了 HARBOR_API_TOKEN 时需要。令牌保存在本机浏览器，不写入构建产物。
              </p>
              <el-input v-model="tokenInput" type="password" show-password
                        placeholder="粘贴后端 X-API-Token" style="max-width: 420px" />
              <div class="token-ops">
                <el-button type="primary" size="small" @click="saveToken">保存</el-button>
                <el-button size="small" @click="clearToken">清除</el-button>
              </div>
            </div>
          </el-tab-pane>
        </el-tabs>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import AIPanel from '@/components/settings/AIPanel.vue'
import AccountsPanel from '@/components/settings/AccountsPanel.vue'
import SchedulesPanel from '@/components/settings/SchedulesPanel.vue'
import WebhooksPanel from '@/components/settings/WebhooksPanel.vue'
import TagsPanel from '@/components/settings/TagsPanel.vue'
import { getApiToken, setApiToken } from '@/api'
import { useHubStore } from '@/stores/hub'
import { getTheme, setTheme } from '@/composables/useTheme'

const hub = useHubStore()
const tab = ref('ai')
const tokenInput = ref(getApiToken())
const theme = ref(getTheme())

function onTheme(v) {
  setTheme(v)
  theme.value = v
}

async function saveToken() {
  setApiToken(tokenInput.value.trim())
  hub.dispose()
  try {
    await hub.boot()
    ElMessage.success('令牌已保存并重新连接')
  } catch {
    ElMessage.error('连接失败，请检查令牌与后端服务')
  }
}

function clearToken() {
  setApiToken('')
  tokenInput.value = ''
  ElMessage.success('令牌已清除')
}
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
.token-panel { display: flex; flex-direction: column; gap: 10px; padding-top: 8px; }
.token-ops { display: flex; gap: 8px; }
.appearance-panel { padding-top: 8px; display: flex; flex-direction: column; gap: 16px; max-width: 640px; }
.ap-row { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.ap-title { font-weight: 600; color: var(--tx-1); margin-bottom: 2px; }

@media (max-width: 767px) {
  .settings-wrap { padding: 8px; }
  // 窄屏没有横向空间放这句解释，藏掉才能让标题一行显示（否则「设置」被挤成竖排）
  .set-hint { display: none; }
  :deep(.el-tabs__header) { padding: 0 10px; }
  :deep(.el-tabs__content) { padding: 8px 10px 14px; }
}
</style>
