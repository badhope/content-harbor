<template>
  <div class="pane" style="height:100%">
    <div class="pane-head">
      <span class="title">文章库</span>
      <span class="spacer" />
      <span class="count-chip" :title="`已加载 ${articles.length} / 共 ${total} 篇`">
        {{ total > articles.length ? `${articles.length}/${total}` : articles.length }}
      </span>
    </div>

    <div class="filter-bar">
      <el-input v-model="keyword" size="small" clearable placeholder="搜索标题 / 摘要"
                :prefix-icon="Search" class="filter-in" aria-label="搜索文章" />
      <div class="status-chips" role="group" aria-label="按状态筛选">
        <button v-for="s in STATUS_FILTERS" :key="s.v" type="button"
                class="chip" :class="{ on: status === s.v }"
                :aria-pressed="status === s.v ? 'true' : 'false'"
                @click="status = s.v">{{ s.t }}</button>
      </div>
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
        <!-- 列表项是按钮语义的容器：role/tabindex + Enter/Space 与鼠标点击等价 -->
        <div
          v-for="(a, i) in shown" :key="a.id"
          class="art-item"
          :class="{ on: a.id === currentId }"
          :style="{ '--i': i }"
          role="button"
          tabindex="0"
          :aria-current="a.id === currentId ? 'true' : undefined"
          @click="$emit('open', a.id)"
          @keydown.enter.prevent="$emit('open', a.id)"
          @keydown.space.prevent="$emit('open', a.id)"
        >
          <div class="t">{{ a.title }}</div>
          <div class="m">
            <el-tag size="small" :type="tagType(a.status)" effect="dark">{{ statusText(a.status) }}</el-tag>
            <el-tag v-if="a.source === 'ai'" size="small" type="primary" effect="plain">AI</el-tag>
            <el-tag v-else-if="a.source === 'import'" size="small" type="info" effect="plain">导入</el-tag>
            <span class="time">{{ fmt(a.updated_at) }}</span>
          </div>
        </div>

        <EmptyState v-if="!articles.length" title="还没有文章" desc="点右上角「新建」写第一篇，或让 AI 帮你起稿">
          <el-button size="small" type="primary" :icon="Plus" @click="$emit('new-article')">新建文章</el-button>
        </EmptyState>
        <div v-else-if="!shown.length" class="no-match">
          当前筛选下没有文章
          <el-button size="small" text type="primary" @click="reset">清空筛选</el-button>
        </div>

        <el-button v-if="hasMore && !filtering" class="more-btn" size="small" :loading="loadingMore"
                   @click="$emit('load-more')">
          加载更多（还有 {{ total - articles.length }} 篇）
        </el-button>
        <div v-else-if="hasMore" class="more-hint">
          筛选只作用于已加载的 {{ articles.length }} 篇
          <el-button size="small" text type="primary" @click="$emit('load-more')">加载更多</el-button>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { Plus, Search } from '@element-plus/icons-vue'
import EmptyState from './EmptyState.vue'
import { fmtShort } from '@/utils/format'

const props = defineProps({
  articles: { type: Array, default: () => [] },
  currentId: [Number, null],
  loading: Boolean,
  total: { type: Number, default: 0 },
  hasMore: Boolean,
  loadingMore: Boolean
})
defineEmits(['open', 'new-article', 'load-more'])

const keyword = ref('')
const status = ref('')
const STATUS_FILTERS = [
  { v: '', t: '全部' },
  { v: 'draft', t: '草稿' },
  { v: 'published', t: '已发布' },
  { v: 'review', t: '待审' },
]
const filtering = computed(() => !!keyword.value.trim() || !!status.value)

// 本地过滤只作用于已加载的页；分页由父组件按 offset 继续拉，两者互不干扰
const shown = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  return props.articles.filter(a => {
    if (status.value && a.status !== status.value) return false
    if (!kw) return true
    return (a.title || '').toLowerCase().includes(kw) ||
           (a.summary || '').toLowerCase().includes(kw)
  })
})

function reset() {
  keyword.value = ''
  status.value = ''
}

const statusText = s => ({ draft: '草稿', published: '已发布', review: '待审', archived: '归档' }[s] || s)
const tagType = s => ({ draft: 'warning', published: 'success', review: 'primary', archived: 'info' }[s] || 'info')

const fmt = (ts) => fmtShort(ts)
</script>

<style scoped>
.filter-bar {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 0 12px 10px;
  border-bottom: 1px solid var(--line-soft);
}
.filter-in { width: 100%; }
.status-chips { display: flex; gap: 6px; flex-wrap: wrap; }
.chip {
  border: 1px solid var(--line);
  background: var(--surface);
  color: var(--tx-2);
  border-radius: 999px;
  padding: 2px 10px;
  font-size: var(--fs-xs);
  font-family: inherit;
  line-height: 1.7;
  cursor: pointer;
  transition: background .14s, border-color .14s, color .14s;
}
.chip:hover { border-color: var(--accent-line); background: var(--accent-soft); }
.chip.on {
  background: var(--accent-soft);
  border-color: var(--accent);
  color: var(--accent);
  font-weight: 600;
}
.more-btn {
  width: calc(100% - 24px);
  margin: 8px 12px 12px;
}
.more-hint, .no-match {
  display: flex; align-items: center; justify-content: center; gap: 4px;
  margin: 4px 12px 12px; font-size: var(--fs-xs); color: var(--tx-3);
}
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
