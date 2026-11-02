import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const src = readFileSync(resolve(process.cwd(), 'src/api.js'), 'utf8')

describe('api 重试策略（审计 B3：POST 重试会重复发布）', () => {
  it('重试条件必须限定 GET 方法', () => {
    expect(src).toMatch(/method\s*===\s*'get'/)
  })

  it('不再使用会让所有请求重试的裸 RETRYABLE.includes 判断', () => {
    expect(src).not.toMatch(/if\s*\(\s*RETRYABLE\.includes\(status\)\s*&&/)
  })

  it('POST 路径上不应出现自动重试调用', () => {
    const retryBlock = src.slice(src.indexOf('interceptors.response'), src.indexOf('export default'))
    expect(retryBlock).toMatch(/method === 'get'/)
  })
})
