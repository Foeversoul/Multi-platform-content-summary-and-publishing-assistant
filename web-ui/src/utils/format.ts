/** 通用格式化工具 */

/** 日期 → "YYYY-MM-DD HH:mm:ss"，空值返回占位符 */
export function formatDateTime(value: string | null | undefined, empty = '—'): string {
  if (!value) return empty
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return empty
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

/** 数字 → 千分位 */
export function formatNumber(value: number | undefined | null): string {
  return (value ?? 0).toLocaleString('zh-CN')
}

/** 爬取任务状态 → Element Plus 标签类型 */
export function jobStatusTag(status: string): 'success' | 'warning' | 'danger' | 'info' | 'primary' {
  switch (status) {
    case 'succeeded':
      return 'success'
    case 'partial':
      return 'warning'
    case 'failed':
      return 'danger'
    case 'pending':
      return 'info'
    default:
      return 'primary'
  }
}

/** 爬取条目状态 → Element Plus 标签类型 */
export function itemStatusTag(status: string): 'success' | 'warning' | 'danger' | 'info' | 'primary' {
  switch (status) {
    case 'succeeded':
      return 'success'
    case 'failed':
      return 'danger'
    case 'validated':
      return 'primary'
    default:
      return 'info'
  }
}

/** 爬取任务状态 → 中文标签 */
export function jobStatusLabel(status: string): string {
  const map: Record<string, string> = {
    pending: '待处理',
    validating: '校验中',
    crawling: '爬取中',
    succeeded: '成功',
    failed: '失败',
    partial: '部分成功',
  }
  return map[status] ?? status
}

/** 爬取条目状态 → 中文标签 */
export function itemStatusLabel(status: string): string {
  const map: Record<string, string> = {
    pending: '待处理',
    validated: '已校验',
    crawling: '爬取中',
    succeeded: '成功',
    failed: '失败',
  }
  return map[status] ?? status
}

/** 平台标识 → 中文名（兼容 yaml key 与旧命名两套） */
export function platformLabel(platform: string): string {
  const map: Record<string, string> = {
    weibo: '微博',
    'wechat-moments': '朋友圈',
    moments: '朋友圈',
    xiaohongshu: '小红书',
    xhs: '小红书',
  }
  return map[platform] ?? platform
}

/** 审核裁决 → 标签类型（空值按待审核处理） */
export function verdictTag(verdict: string | null | undefined): 'success' | 'danger' | 'info' {
  if (verdict === 'pass') return 'success'
  if (verdict === 'reject') return 'danger'
  return 'info'
}
