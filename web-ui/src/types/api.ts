/**
 * 与后端对齐的 TS 类型与枚举（PRD IF-01~13 / 6.2 通用约定）。
 * 后端响应统一包：{code, message, data}，业务失败 code != 0。
 */

/** 统一响应包（Axios 拦截器已解包，仅保留 data） */
export interface ApiResponse<T> {
  code: number
  message: string
  data: T
}

/** 爬取错误码（PRD FR-21 错误分类表） */
export const SCRAPE_ERROR_CODES = [
  'INVALID_URL_FORMAT',
  'UNSUPPORTED_PROTOCOL',
  'DNS_FAILED',
  'CONNECTION_REFUSED',
  'TIMEOUT',
  'SSL_ERROR',
  'HTTP_403',
  'HTTP_404',
  'HTTP_429',
  'HTTP_5XX',
  'HTTP_OTHER',
  'ROBOTS_BLOCKED',
  'EMPTY_CONTENT',
  'RENDER_UNSUPPORTED',
  'DUPLICATE',
] as const
export type ScrapeErrorCode = (typeof SCRAPE_ERROR_CODES)[number]

/** 审核裁决 */
export type Verdict = 'pending' | 'pass' | 'reject'

/** 爬取任务状态机（BR-22-01） */
export type ScrapeJobStatus = 'pending' | 'validating' | 'crawling' | 'succeeded' | 'failed' | 'partial'

/** 爬取条目状态机（BR-22-02） */
export type ScrapeItemStatus = 'pending' | 'validated' | 'crawling' | 'succeeded' | 'failed'

/** 死信事件状态 */
export type EventStatus = 'queued' | 'processed' | 'dead' | 'discarded'

/** IF-01 待审列表条目 */
export interface ReviewListItem {
  article_id: number
  article_title: string
  /** 默认进入详情的副本（优先待审的平台文案） */
  copy_id: number
  review_id: number
  platform_count: number
  /** 该文章的各平台副本，供列表展示与详情切换 */
  platforms: { copy_id: number; review_id: number; platform: string; verdict: Verdict; scores: Record<string, number> }[]
  verdict: Verdict
  scores: Record<string, number>
  created_at: string | null
}

/** IF-02 详情 */
export interface ReviewDetail {
  review: {
    id: number
    verdict: Verdict
    scores: Record<string, number>
    comment: string | null
    created_at: string | null
  }
  copy: { id: number; platform: string; text: string; status: string }
  summary: { id: number; summary_text: string; key_points: string[]; short_title: string }
  article: { id: number; url: string; title: string; publish_time: string | null; text: string }
  publish: { status: string; published_at: string | null } | null
  /** 同摘要的其他平台文案（U2 多平台聚合） */
  siblings: { copy_id: number; platform: string; text: string; verdict: string }[]
}

/** 摘要生成/编辑结果 */
export interface SummaryResult {
  summary_id: number
  summary_text: string
  key_points: string[]
  short_title: string
  source: 'llm' | 'extractive' | 'manual'
}

/** 文案扩写/预览结果 */
export interface CopyResult {
  copy_id: number
  platform: string
  text: string
  source: 'llm' | 'fallback'
}

/** 回收站条目 */
export interface RecycleItem {
  copy_id: number
  platform: string
  article_title: string
  verdict: Verdict
  text: string
  deleted_at: string | null
}

/** 手动内容上传结果 */
export interface ManualContentResult {
  article_id: number
  summary_id: number
  copy_ids: number[]
}

/** IF-05 运行状态 */
export interface SystemStatus {
  stream_lengths: Record<string, number>
  event_counts: Record<string, number>
  event_types: Record<string, number>
  article_counts: Record<string, number>
  copy_counts: Record<string, number>
  copy_by_platform: Record<string, number>
  review_verdicts: Record<string, number>
  publish_count: number
  summary_count: number
  failed_counts: { events: number; articles: number }
  pending_reviews: number
  scrape_jobs: Record<string, number>
}

/** IF-06 死信条目 */
export interface FailedEvent {
  event_id: string
  event_type: string
  error: string
  payload: Record<string, unknown>
  created_at: string | null
}

/** IF-09 创建爬取任务 */
export interface ScrapeJobCreated {
  job_id: number
  status: 'pending'
  url_count: number
  dedup_count: number
}

/** IF-10 任务进度 */
export interface ScrapeJobProgress {
  job_id: number
  status: ScrapeJobStatus
  url_count: number
  succeeded_count: number
  failed_count: number
  created_at: string | null
  finished_at: string | null
}

/** IF-11 任务条目 */
export interface ScrapeJobItemDetail {
  item_id: number
  url: string
  status: ScrapeItemStatus
  error_code: ScrapeErrorCode | null
  error_message: string | null
  article_id: number | null
  created_at: string | null
  finished_at: string | null
}

/** IF-13 失败条目重提 */
export interface ScrapeRetryCreated {
  new_job_id: number
  status: 'created'
}
