<template>
  <div class="tags-panel">
    <div class="hint-bar">
      <span class="dim small">词云按使用频次；支持重命名 / 合并 / 别名映射，改完已发布文章不重发，只影响后续使用。</span>
      <span class="spacer" />
      <el-button size="small" @click="hub.syncTags()">重新统计</el-button>
      <el-button size="small" :icon="Refresh" @click="hub.loadTags()">刷新</el-button>
    </div>

    <div v-if="hub.tags.length" class="tag-cloud">
      <button v-for="t in hub.tags" :key="t.tag" type="button" class="tag-chip"
            :style="{ fontSize: Math.max(11, Math.min(17, 10 + (t.count || 1) * 0.5)) + 'px' }"
            :aria-label="`标签 ${t.tag}：${t.count} 篇，打开操作`"
            @click="openOps(t)">
        {{ t.tag }} <em class="tag-cnt">{{ t.count }}</em>
      </button>
    </div>
    <EmptyState v-else title="还没有标签数据" desc="先保存几篇文章，再点「重新统计」" :px="60" />

    <el-dialog v-model="opsOpen" :title="`标签操作 · ${current?.tag || ''}`" width="min(440px, 94vw)">
      <el-form label-width="80px" v-if="current">
        <el-form-item label="重命名">
          <div class="op-row">
            <el-input v-model="newName" :placeholder="current.tag" />
            <el-button :disabled="!newName.trim() || newName.trim() === current.tag"
                       @click="doRename">改名</el-button>
          </div>
        </el-form-item>
        <el-form-item label="合并到">
          <div class="op-row">
            <el-select v-model="mergeTo" filterable placeholder="选目标标签" style="flex:1">
              <el-option v-for="t in hub.tags" :key="t.tag" :label="t.tag"
                         :value="t.tag" :disabled="t.tag === current.tag" />
            </el-select>
            <el-button type="warning" :disabled="!mergeTo" @click="doMerge">合并</el-button>
          </div>
          <div class="dim small" style="margin-top:4px">「{{ current.tag }}」的全部文章会改用目标标签</div>
        </el-form-item>
      </el-form>
      <template #footer><el-button @click="opsOpen = false">关闭</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { useHubStore } from '@/stores/hub'
import EmptyState from '@/components/EmptyState.vue'

const hub = useHubStore()

const opsOpen = ref(false)
const current = ref(null)
const newName = ref('')
const mergeTo = ref('')

onMounted(() => hub.loadTags())

function openOps(t) {
  current.value = t
  newName.value = ''
  mergeTo.value = ''
  opsOpen.value = true
}

async function doRename() {
  await hub.renameTag(current.value.tag, newName.value.trim())
  await hub.loadTags()
  opsOpen.value = false
}

async function doMerge() {
  await hub.mergeTag(current.value.tag, mergeTo.value)
  await hub.loadTags()
  opsOpen.value = false
}
</script>

<style scoped lang="scss">
.tags-panel { display: flex; flex-direction: column; gap: 14px; padding-top: 8px; }
.hint-bar { display: flex; align-items: center; gap: 10px; }
.tag-cloud { display: flex; flex-wrap: wrap; gap: 8px; }
.tag-chip {
  display: inline-flex; align-items: center; gap: 5px;
  font-family: inherit; line-height: 1.5;
  padding: 3px 10px; border-radius: 999px; background: var(--surface-2);
  color: var(--tx-2); border: 1px solid var(--line); cursor: pointer;
  transition: border-color .14s, background .14s;
  &:hover { border-color: var(--accent-line); background: var(--accent-soft); }
  .tag-cnt { font-style: normal; color: var(--tx-4); font-size: 11px; }
}
.op-row { display: flex; gap: 8px; width: 100%; }
.dim { color: var(--tx-4); }
.small { font-size: var(--fs-xs); }
</style>
