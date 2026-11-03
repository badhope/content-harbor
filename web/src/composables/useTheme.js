// 明暗主题：localStorage 持久化 + html.dark class。
// Element Plus 暗色变量与项目暗色变量都挂在 html.dark 上（见 main.scss）。
const KEY = 'harbor-theme'

export function getTheme() {
  try { return localStorage.getItem(KEY) === 'dark' ? 'dark' : 'light' } catch { return 'light' }
}

// 模块加载即应用：main.js 早期调用，避免首屏闪白
export function applyTheme(t) {
  const theme = t === 'dark' ? 'dark' : 'light'
  document.documentElement.classList.toggle('dark', theme === 'dark')
}

export function setTheme(t) {
  const theme = t === 'dark' ? 'dark' : 'light'
  try { localStorage.setItem(KEY, theme) } catch { /* 隐私模式忽略 */ }
  applyTheme(theme)
}

export function toggleTheme() {
  setTheme(getTheme() === 'dark' ? 'light' : 'dark')
}
