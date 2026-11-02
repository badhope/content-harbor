import { vi, afterEach } from 'vitest'
import { config } from '@vue/test-utils'

// element-plus 的交互组件在测试里会尝试挂载 DOM 之外的东西，统一 stub 掉
const stubs = {
  'el-button': { template: '<button><slot /></button>' },
  // el-input 桩必须转发 v-model，否则被测组件的输入过滤逻辑永远收不到值
  'el-input': {
    props: ['modelValue'],
    emits: ['update:modelValue'],
    template: '<input :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />'
  },
  'el-tag': { template: '<span><slot /></span>' },
  'el-select': { template: '<div><slot /></div>' },
  'el-option': { template: '<div />' },
  'el-drawer': { template: '<div><slot /></div>' },
  'el-dialog': { template: '<div><slot /></div>' },
  'el-dropdown': { template: '<div><slot /></div>' },
  'el-dropdown-menu': { template: '<div><slot /></div>' },
  'el-dropdown-item': { template: '<div><slot /></div>' },
  'el-table': { template: '<div><slot /></div>' },
  'el-table-column': { template: '<div />' },
  'el-progress': { template: '<div />' },
  'el-empty': { template: '<div />' }
}

config.global.stubs = stubs

// jsdom 没有这些浏览器 API
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver = globalThis.ResizeObserver || ResizeObserverStub
// jsdom 没有 EventSource；App.vue 挂载会调 hub.boot() → connectSSE()
class EventSourceStub {
  constructor() { this.readyState = 0 }
  addEventListener() {}
  removeEventListener() {}
  close() { this.readyState = 2 }
}
globalThis.EventSource = globalThis.EventSource || EventSourceStub

globalThis.matchMedia = globalThis.matchMedia || (() => ({
  matches: false, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {}
}))

// element-plus 的消息组件在 store 里被直接调用，测试中静默
vi.mock('element-plus', async () => {
  const actual = await vi.importActual('element-plus')
  return {
    ...actual,
    ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
    ElMessageBox: { confirm: vi.fn().mockResolvedValue(true), alert: vi.fn() }
  }
})

afterEach(() => vi.clearAllMocks())
