import { describe, it, expect, beforeEach } from 'vitest'
import { getTheme, setTheme, applyTheme, toggleTheme, onThemeChange, themeState } from '@/composables/useTheme'

describe('主题：亮色为默认', () => {
  beforeEach(() => {
    localStorage.clear()
    document.documentElement.className = ''
  })

  it('未设置过时默认亮色（需求要求默认亮色调）', () => {
    expect(getTheme()).toBe('light')
    applyTheme(getTheme())
    expect(document.documentElement.classList.contains('dark')).toBe(false)
  })

  it('切到暗色会加 html.dark 并持久化', () => {
    setTheme('dark')
    expect(document.documentElement.classList.contains('dark')).toBe(true)
    expect(localStorage.getItem('harbor-theme')).toBe('dark')
    expect(getTheme()).toBe('dark')
  })

  it('切换后回到亮色会移除 dark class', () => {
    setTheme('dark')
    toggleTheme()
    expect(document.documentElement.classList.contains('dark')).toBe(false)
    expect(getTheme()).toBe('light')
  })

  it('订阅者能收到主题变化，取消订阅后不再收到', () => {
    const seen = []
    const off = onThemeChange((t) => seen.push(t))
    setTheme('dark')
    expect(seen).toEqual(['dark'])
    expect(themeState.value).toBe('dark')
    off()
    setTheme('light')
    expect(seen).toEqual(['dark'])
  })

  it('某个订阅者抛错不影响其他人', () => {
    const seen = []
    const off1 = onThemeChange(() => { throw new Error('boom') })
    const off2 = onThemeChange((t) => seen.push(t))
    expect(() => setTheme('dark')).not.toThrow()
    expect(seen).toEqual(['dark'])
    off1(); off2()
  })
})
