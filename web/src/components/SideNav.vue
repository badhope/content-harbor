<template>
  <aside class="side-nav">
    <div class="sn-brand">
      <BrandLogo :px="30" :with-name="false" />
      <div class="sn-names">
        <div class="sn-name">内容港 <em>Harbor</em></div>
        <div class="sn-sub">内容进港 · 全网分发</div>
      </div>
    </div>

    <el-button class="sn-new" type="primary" :icon="Plus" @click="$emit('new')">写新文章</el-button>

    <nav class="sn-menu">
      <button v-for="it in items" :key="it.key" class="sn-item"
              :class="{ on: view === it.key }" @click="$emit('switch', it.key)">
        <el-icon class="sn-ico"><component :is="it.icon" /></el-icon>
        <span class="sn-label">{{ it.label }}</span>
        <span v-if="it.badge" class="sn-badge">{{ it.badge > 99 ? '99+' : it.badge }}</span>
      </button>
    </nav>

    <div class="sn-foot">
      <div class="pill sn-pill" :title="aiReady ? 'AI 已就绪' : 'AI 未配置：到设置 → AI 模型 填密钥'">
        <span class="dot" :class="aiReady ? 'ok' : 'off'" />
        <span>{{ aiReady ? 'AI 就绪' : 'AI 未配置' }}</span>
      </div>
      <div class="sn-pubs" v-if="publishedCount">{{ publishedCount }} 篇已发布</div>
    </div>
  </aside>
</template>

<script setup>
import { computed } from 'vue'
import { Plus, EditPen, Promotion, List, Setting } from '@element-plus/icons-vue'
import BrandLogo from './BrandLogo.vue'

const props = defineProps({
  view: { type: String, default: 'write' },
  aiReady: Boolean,
  waitCount: { type: Number, default: 0 },
  publishedCount: { type: Number, default: 0 },
})
defineEmits(['switch', 'new'])

const items = computed(() => [
  { key: 'write', icon: EditPen, label: '写作台' },
  { key: 'publish', icon: Promotion, label: '发布中心' },
  { key: 'tasks', icon: List, label: '任务中心', badge: props.waitCount },
  { key: 'settings', icon: Setting, label: '设置' },
])
</script>

<style scoped lang="scss">
.side-nav {
  width: 208px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 16px 12px;
  background: var(--surface);
  border-right: 1px solid var(--line);
}

.sn-brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 4px;
  .sn-name { font-size: 15px; font-weight: 700; color: var(--tx-1); letter-spacing: .01em; }
  .sn-name em { font-style: normal; font-size: 11px; font-weight: 500; color: var(--tx-3); margin-left: 2px; }
  .sn-sub { font-size: 10px; color: var(--tx-4); margin-top: 1px; }
}

.sn-new { width: 100%; }

.sn-menu { display: flex; flex-direction: column; gap: 4px; flex: 1; }

.sn-item {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 9px 12px;
  border: none;
  border-radius: 10px;
  background: transparent;
  color: var(--tx-2);
  font-size: 13.5px;
  font-weight: 500;
  font-family: inherit;
  cursor: pointer;
  text-align: left;
  transition: background .14s, color .14s;

  .sn-ico { font-size: 16px; }
  .sn-label { flex: 1; }
  .sn-badge {
    min-width: 18px; height: 18px; padding: 0 5px;
    display: inline-flex; align-items: center; justify-content: center;
    border-radius: 9px; background: var(--warn); color: #fff;
    font-size: 10px; font-weight: 600;
  }

  &:hover { background: var(--surface-2); color: var(--tx-1); }
  &.on { background: var(--accent-soft); color: var(--accent); font-weight: 600; }
}

.sn-foot {
  border-top: 1px solid var(--line-soft);
  padding-top: 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  .sn-pill { height: 26px; font-size: 11px; padding: 0 10px; }
  .sn-pubs { font-size: 11px; color: var(--tx-4); padding: 0 4px; }
}

// 窄屏：侧栏收成图标条
@media (max-width: 1023px) {
  .side-nav { width: 64px; padding: 14px 8px; }
  .sn-names, .sn-label, .sn-sub, .sn-pubs { display: none; }
  .sn-brand { justify-content: center; }
  .sn-new { :deep(span) { display: none; } padding: 8px 0; }
  .sn-item { justify-content: center; padding: 10px 0; }
  .sn-foot { align-items: center; .sn-pill span:last-child { display: none; } }
}
</style>
