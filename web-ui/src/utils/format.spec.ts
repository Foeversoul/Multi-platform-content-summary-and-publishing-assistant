/** T7：前端轻量单元测试（vitest）——通用格式化工具纯函数 */
import { describe, expect, it } from 'vitest'

import {
  formatDateTime,
  formatNumber,
  itemStatusLabel,
  itemStatusTag,
  jobStatusLabel,
  jobStatusTag,
  platformLabel,
  verdictTag,
} from './format'

describe('formatDateTime', () => {
  it('formats ISO datetime string', () => {
    expect(formatDateTime('2026-08-19T10:30:00')).toBe('2026-08-19 10:30:00')
  })

  it('returns placeholder for empty input', () => {
    expect(formatDateTime(null)).toBe('—')
    expect(formatDateTime(undefined)).toBe('—')
    expect(formatDateTime('', 'N/A')).toBe('N/A')
  })

  it('returns placeholder for invalid input', () => {
    expect(formatDateTime('not-a-date')).toBe('—')
  })
})

describe('formatNumber', () => {
  it('adds thousands separators', () => {
    expect(formatNumber(1234567)).toBe('1,234,567')
  })

  it('falls back to 0', () => {
    expect(formatNumber(undefined)).toBe('0')
    expect(formatNumber(null)).toBe('0')
  })
})

describe('jobStatusTag', () => {
  it('maps scrape job statuses to tag types', () => {
    expect(jobStatusTag('succeeded')).toBe('success')
    expect(jobStatusTag('partial')).toBe('warning')
    expect(jobStatusTag('failed')).toBe('danger')
    expect(jobStatusTag('pending')).toBe('info')
    expect(jobStatusTag('crawling')).toBe('primary')
  })
})

describe('verdictTag', () => {
  it('maps verdicts to tag types', () => {
    expect(verdictTag('pass')).toBe('success')
    expect(verdictTag('reject')).toBe('danger')
    expect(verdictTag('pending')).toBe('info')
  })
})

describe('status labels', () => {
  it('maps job statuses to Chinese labels', () => {
    expect(jobStatusLabel('pending')).toBe('待处理')
    expect(jobStatusLabel('crawling')).toBe('爬取中')
    expect(jobStatusLabel('succeeded')).toBe('成功')
    expect(jobStatusLabel('partial')).toBe('部分成功')
    expect(jobStatusLabel('failed')).toBe('失败')
    expect(jobStatusLabel('unknown-status')).toBe('unknown-status')
  })

  it('maps item statuses to Chinese labels and tag types', () => {
    expect(itemStatusLabel('validated')).toBe('已校验')
    expect(itemStatusLabel('crawling')).toBe('爬取中')
    expect(itemStatusLabel('succeeded')).toBe('成功')
    expect(itemStatusTag('succeeded')).toBe('success')
    expect(itemStatusTag('failed')).toBe('danger')
    expect(itemStatusTag('validated')).toBe('primary')
    expect(itemStatusTag('pending')).toBe('info')
  })
})

describe('platformLabel', () => {
  it('maps platform keys to Chinese names', () => {
    expect(platformLabel('weibo')).toBe('微博')
    expect(platformLabel('wechat-moments')).toBe('朋友圈')
    expect(platformLabel('xiaohongshu')).toBe('小红书')
    expect(platformLabel('other')).toBe('other')
  })
})
