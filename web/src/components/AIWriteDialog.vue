<template>
  <el-dialog v-model="visible" title="AI 落笔" width="min(560px, 94vw)" :close-on-click-modal="false" class="ai-write-dialog">
    <el-alert v-if="!ready" type="warning" :closable="false" show-icon
              title="AI 未配置——到 设置 → AI 模型 填任一 Provider 的密钥后即可写稿"
              description="在 config.json 或环境变量里填 AI_API_KEY / AI_BASE_URL / AI_MODEL 后重启后端即可。" />

    <el-form label-width="88px" style="margin-top:14px">
      <el-form-item label="主题" required>
        <el-input v-model="form.topic" placeholder="例如：用 Python 手写一个任务队列" />
      </el-form-item>
      <el-form-item label="模型">
        <el-select v-model="form.model" placeholder="自动路由（写作用旗舰，标签用经济型）" style="width:100%" clearable>
          <el-option-group v-for="prov in providerGroups" :key="prov.key" :label="prov.name + ' · ' + prov.price">
            <el-option v-for="m in prov.models" :key="prov.key + ':' + m"
                       :value="prov.key + ':' + m" :label="m" />
          </el-option-group>
        </el-select>
        <div class="dim small" style="margin-top:4px">
          留空自动路由：写作用旗舰、润色用均衡、标签/摘要用经济型
        </div>
      </el-form-item>
      <el-form-item label="文风">
        <el-select v-model="form.style" placeholder="默认：技术干货" style="width:100%">
          <el-option label="技术干货" value="技术干货，有代码示例" />
          <el-option label="通俗科普" value="通俗易懂，面向初学者" />
          <el-option label="观点评论" value="有观点、有论据的评论" />
          <el-option label="教程步骤" value="分步骤教程，可直接照做" />
        </el-select>
      </el-form-item>
      <el-form-item label="字数">
        <el-slider v-model="form.words" :min="600" :max="6000" :step="200" show-input />
      </el-form-item>
      <el-form-item label="标签提示">
        <el-input v-model="form.tags_hint" placeholder="可选，逗号分隔，例如：Python,后端" />
      </el-form-item>
      <el-form-item label="直接发布">
        <el-select v-model="form.publish_to" multiple placeholder="不选则只入库" style="width:100%">
          <el-option v-for="p in platforms" :key="p.id" :label="p.name" :value="p.id" />
        </el-select>
      </el-form-item>
    </el-form>

    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button type="primary" :loading="loading" :disabled="!form.topic.trim()" @click="submit">
        开始落笔
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
:deep(.el-dialog__title) {
  font-family: var(--font-serif);
  font-weight: 600;
  letter-spacing: .02em;
}
</style>

<script setup>
import { ref, reactive, watch, computed, onMounted } from 'vue'
import { api } from '@/api'
import { ElMessage } from 'element-plus'
import { useHubStore } from '@/stores/hub'

const props = defineProps({
  modelValue: Boolean,
  platforms: { type: Array, default: () => [] },
  ready: Boolean
})
const emit = defineEmits(['update:modelValue', 'done'])
const hub = useHubStore()

const visible = ref(false)
const loading = ref(false)
const form = reactive({ topic: '', style: '', words: 2000, tags_hint: '', publish_to: [], model: '' })

// 把后端 provider 列表转成前端分组格式
const providerGroups = computed(() => {
  return (hub.aiProviders || []).map(p => ({
    key: p.key, name: p.name, models: p.models || [],
    price: p.price_level === 'premium' ? '旗舰' : p.price_level === 'standard' ? '均衡' : '经济',
    cooling: p.cooling
  }))
})

onMounted(() => {
  if (!hub.aiProviders.length) hub.loadAiProviders()
})

// v-model 双向：外面改 modelValue，里面改 visible
watch(() => props.modelValue, v => {
  visible.value = v
  if (v && !hub.aiProviders.length) hub.loadAiProviders()
})
watch(visible, v => emit('update:modelValue', v))

async function submit() {
  loading.value = true
  try {
    const r = await api.aiWrite({
      topic: form.topic.trim(),
      style: form.style,
      words: form.words,
      tags_hint: form.tags_hint,
      publish_to: form.publish_to.length ? form.publish_to : null,
      model: form.model || ''
    })
    ElMessage.success(`已生成《${r.title}》${r.chars} 字 ${r.ai_model ? '(' + r.ai_model + ')' : ''}`)
    emit('done', r.id)
    visible.value = false
  } finally { loading.value = false }
}
</script>
