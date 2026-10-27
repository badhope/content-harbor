<template>
  <div class="pane" style="height:100%">
    <div class="pane-head">
      <span class="title">文章库</span>
      <span class="spacer" />
      <span class="count-chip">{{ articles.length }}</span>
    </div>

    <div class="pane-body">
      <!-- 加载：骨架屏（比转圈安静，也更快给人"结构感"） -->
      <template v-if="loading">
        <div v-for="i in 6" :key="'sk' + i" class="skel-item">
          <div class="skel t" :style="{ width: 45 + (i * 13) % 30 + '%' }" />
          <div class="skel m" :style="{ width: 26 + (i * 9) % 18 + '%' }" />
        </div>
      </template>

      <template v-else>
        <div
          v-for="(a, i) in articles" :key="a.id"
          class="art-item"
          :class="{ on: a.id === currentId }"
          :style="{ '--i': i }"
          @click="$emit('open', a.id)"
        >
          <div class="t">{{ a.title }}</div>
          <div class="m">
            <el-tag size="small" :type="tagType(a.status)" effect="dark">{{ statusText(a.status) }}</el-tag>
            <el-tag v-if="a.source === 'ai'" size="small" type="primary" effect="plain">AI</el-tag>
            <el-tag v-else-if="a.source === 'import'" size="small" type="info" effect="plain">导入</el-tag>
            <span>{{ fmt(a.updated_at) }}</span>
          </div>
        </div>
        <EmptyState v-if="!articles.length" title="还没有文章" desc="点右上角「新建」写第一篇，或让 AI 帮你起稿">
          <el-button size="small" type="primary" :icon="Plus" @click="$emit('new-article')">新建文章</el-button>
        </EmptyState>
      </template>
    </div>
  </div>
</template>

<script setup>
import { Plus } from '@element-plus/icons-vue'
import EmptyState from './EmptyState.vue'
import { fmtShort } from '@/utils/format'

defineProps({
  articles: { type: Array, default: () => [] },
  currentId: [Number, null],
  loading: Boolean
})
defineEmits(['open', 'new-article'])

const statusText = s => ({ draft: '草稿', published: '已发布', review: '待审', archived: '归档' }[s] || s)
const tagType = s => ({ draft: 'warning', published: 'success', review: 'primary', archived: 'info' }[s] || 'info')

const fmt = (ts) => fmtShort(ts)
</script>

<style scoped>
.count-chip {
  min-width: 20px;
  height: 18px;
  padding: 0 6px;
  border-radius: var(--r-sm);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: var(--fs-xs);
  font-weight: 500;
  font-variant-numeric: tabular-nums;
  color: var(--tx-3);
  background: var(--surface-2);
}
</style>
