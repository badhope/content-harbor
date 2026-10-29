import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

// 静态 a11y / 设计门禁：不依赖浏览器渲染，直接把可见性硬约束写进 CI。
// 数值来自 WCAG 2.2 AA（正常文本 4.5:1、非文本 3:1），改动 token 时测试会先红。

const root = resolve(__dirname, '..')
const read = p => readFileSync(resolve(root, p), 'utf8')

function luminance(hex) {
  const h = hex.replace('#', '')
  const [r, g, b] = [0, 2, 4].map(i => parseInt(h.slice(i, i + 2), 16) / 255)
  const f = c => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4)
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)
}

function ratio(fg, bg) {
  const a = luminance(fg), b = luminance(bg)
  const [hi, lo] = a > b ? [a, b] : [b, a]
  return (hi + 0.05) / (lo + 0.05)
}

const scss = read('src/styles/main.scss')
const token = name => {
  const m = scss.match(new RegExp(`--${name}:\\s*(#[0-9A-Fa-f]{6})`))
  if (!m) throw new Error(`--${name} 未定义`)
  return m[1]
}

describe('设计 token 对比度（WCAG 2.2 AA 1.4.3 / 1.4.11）', () => {
  it.each([
    ['tx-1', 4.5],
    ['tx-2', 4.5],
    ['tx-3', 4.5],
    ['tx-4', 4.5]
  ])('%s 在白色与浅灰底上均达到 4.5:1', (name, min) => {
    const fg = token(name)
    for (const bg of ['#FFFFFF', '#F5F7FA']) {
      expect(ratio(fg, bg), `${name} on ${bg}`).toBeGreaterThanOrEqual(min)
    }
  })

  it.each(['accent', 'ok', 'warn', 'bad'])('%s 作为文字色达到 4.5:1，作为底色白字达到 4.5:1', name => {
    const c = token(name)
    expect(ratio(c, '#FFFFFF')).toBeGreaterThanOrEqual(4.5)
    expect(ratio('#FFFFFF', c)).toBeGreaterThanOrEqual(4.5)
  })
})

describe('弹窗与抽屉宽度自适应窄屏（WCAG 1.4.10 Reflow）', () => {
  const files = [
    'src/components/ArticleEditor.vue',
    'src/components/AIWriteDialog.vue',
    'src/components/settings/AccountsPanel.vue',
    'src/components/settings/TagsPanel.vue',
    'src/components/settings/SchedulesPanel.vue',
    'src/components/settings/WebhooksPanel.vue',
    'src/views/TasksView.vue'
  ]

  it.each(files)('%s 的固定 px 尺寸都用 min(px, 94vw) 收敛', file => {
    const src = read(file)
    // label-width 是表单标签列宽，不占 el-dialog / el-drawer 的尺寸位
    const fixed = [...src.matchAll(/\s(?:width|size)="(\d+)px"/g)].map(m => m[0])
    expect(fixed, `${file} 仍存在固定像素尺寸: ${fixed.join(', ')}`).toEqual([])
  })
})

describe('交互可达性（WCAG 4.1.2 / 2.1.1）', () => {
  it('文章列表项是 role=button + tabindex + Enter/Space', () => {
    const src = read('src/components/ArticleList.vue')
    expect(src).toContain('role="button"')
    expect(src).toContain('tabindex="0"')
    expect(src).toContain('@keydown.enter')
    expect(src).toContain('@keydown.space')
  })

  it('平台卡片是 checkbox 语义 + 键盘可切换', () => {
    const src = read('src/components/PublishRail.vue')
    expect(src).toContain('role="checkbox"')
    expect(src).toContain(':aria-checked')
    // 能力矩阵会给不支持发布的平台加 canPublish() 守卫，两种写法都算可达
    expect(src).toMatch(/@keydown\.space\.prevent="(canPublish\(p\) && )?toggle/)
  })

  it('图标按钮带 aria-label', () => {
    const src = read('src/components/ArticleEditor.vue')
    expect(src).toMatch(/aria-label="打开文章库"/)
  })
})

describe('Element Plus 主题接管必须落在 :root', () => {
  it('样式表用 :root 而不是 html 定义 --el-color-*', () => {
    // Element Plus 默认变量定义在 :root 上；html 是类型选择器，优先级更低，
    // 写了也会被默认色板覆盖（实测标签仍是 #f56c6c 默认红）。
    const idx = scss.indexOf('--el-color-primary')
    expect(idx).toBeGreaterThan(-1)
    const before = scss.slice(0, idx)
    const lastRoot = before.lastIndexOf(':root {')
    const lastHtml = before.lastIndexOf('\nhtml {')
    expect(lastRoot).toBeGreaterThan(-1)
    expect(lastRoot).toBeGreaterThan(lastHtml)
  })

  it('primary / success / warning / danger 的四档浅色齐全（否则 EP 组件取不到配色）', () => {
    for (const name of ['primary', 'success', 'warning', 'danger', 'error', 'info']) {
      for (const n of ['3', '5', '7', '8', '9']) {
        expect(scss, `--el-color-${name}-light-${n}`)
          .toContain(`--el-color-${name}-light-${n}:`)
      }
      expect(scss, `--el-color-${name}-dark-2`).toContain(`--el-color-${name}-dark-2:`)
    }
  })

  it('EP 语义色与设计 token 一致（避免两套色板打架）', () => {
    // 按行解析而不是正则：模板字面量里的 \s 会被吃掉，写成正则容易假通过/假失败
    const vars = {}
    for (const line of scss.split('\n')) {
      const m = line.match(/^\s*--([a-z0-9-]+):\s*(#[0-9A-Fa-f]{6})/)
      if (m && !(m[1] in vars)) vars[m[1]] = m[2].toLowerCase()
    }
    const pairs = [['el-color-success', 'ok'], ['el-color-warning', 'warn'],
                   ['el-color-danger', 'bad'], ['el-color-primary', 'accent']]
    for (const [ep, token] of pairs) {
      expect(vars[ep], `缺少 --${ep}`).toBeTruthy()
      expect(vars[token], `缺少 --${token}`).toBeTruthy()
      expect(vars[ep], `${ep} vs ${token}`).toBe(vars[token])
    }
  })
})
