// 明暗主题：localStorage 持久化 + html.dark class。
// Element Plus 暗色变量与项目暗色变量都挂在 html.dark 上（见 main.scss）。
import { ref } from 'vue'

const KEY = 'harbor-theme'

// 全局唯一的主题状态：任何需要跟随明暗的组件（Markdown 编辑器、图表等）
// 都订阅这里，而不是各自读一次 localStorage——否则切换主题后它们不会更新。
export const themeState = ref('light')

// 订阅者集合：不用 DOM 事件，避免依赖宿主环境是否支持 CustomEvent
const listeners = new Set()

export function getTheme() {
  try { return localStorage.getItem(KEY) === 'dark' ? 'dark' : 'light' } catch { return 'light' }
}

/** 订阅主题变化，返回取消订阅函数。 */
export function onThemeChange(fn) {
  listeners.add(fn)
  return () => listeners.delete(fn)
}

// 模块加载即应用：main.js 早期调用，避免首屏闪白
export function applyTheme(t) {
  const theme = t === 'dark' ? 'dark' : 'light'
  document.documentElement.classList.toggle('dark', theme === 'dark')
  themeState.value = theme
  for (const fn of listeners) {
    try { fn(theme) } catch { /* 单个订阅者出错不影响其他人 */ }
  }
}

export function setTheme(t) {
  const theme = t === 'dark' ? 'dark' : 'light'
  try { localStorage.setItem(KEY, theme) } catch { /* 隐私模式忽略 */ }
  applyTheme(theme)
}

export function toggleTheme() {
  setTheme(getTheme() === 'dark' ? 'light' : 'dark')
}
