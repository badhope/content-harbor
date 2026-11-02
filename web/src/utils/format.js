/** 统一时间/文本格式化。后端时间戳一律是 Unix 秒（浮点）。 */

const pad = n => String(n).padStart(2, '0')

/** 月-日 时:分（列表/表格默认） */
export function fmtDateTime(ts, fallback = '—') {
  if (!ts) return fallback
  const d = new Date(ts * 1000)
  return `${d.getMonth() + 1}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/** 时:分:秒（Webhook 派发流水） */
export function fmtClock(ts, fallback = '') {
  if (!ts) return fallback
  const d = new Date(ts * 1000)
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

/** 月/日 时:分（文章列表紧凑形态） */
export function fmtShort(ts, fallback = '') {
  if (!ts) return fallback
  const d = new Date(ts * 1000)
  return `${d.getMonth() + 1}/${d.getDate()} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/** 秒 → 人类可读时长 */
export function fmtDuration(sec) {
  if (!sec || sec < 0) return '0s'
  if (sec < 60) return `${Math.round(sec)}s`
  if (sec < 3600) return `${Math.floor(sec / 60)}m${Math.round(sec % 60)}s`
  return `${Math.floor(sec / 3600)}h${Math.floor((sec % 3600) / 60)}m`
}
