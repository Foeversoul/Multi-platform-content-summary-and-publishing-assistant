/** IF-01~04 审核相关接口 + 内容 AI 处理 / 删除 */
import type { CopyResult, ReviewDetail, ReviewListItem, SummaryResult } from '@/types/api'
import request, { request as req } from '@/utils/request'

export interface ReviewListParams {
  page?: number
  page_size?: number
  platform?: string
  verdict?: string
  keyword?: string
}

export async function fetchReviews(params: ReviewListParams) {
  return req<{ items: ReviewListItem[]; total: number }>(request.get('/reviews', { params }))
}

export async function fetchReviewDetail(copyId: number) {
  return req<ReviewDetail>(request.get(`/reviews/${copyId}`))
}

/** 发布（IF-03） */
export async function publishReview(copyId: number) {
  return req<{ copy_id: number; verdict: 'pass'; published_at: string | null }>(request.post(`/reviews/${copyId}/publish`))
}

/** 一键审核：通过全部待审核文案 */
export async function batchPublishReviews() {
  return req<{ published: number }>(request.post('/reviews/batch-publish'))
}

/** 驳回（IF-04，comment 必填） */
export async function rejectReview(copyId: number, comment: string) {
  return req<{ copy_id: number; verdict: 'reject'; comment: string }>(request.post(`/reviews/${copyId}/reject`, { comment }))
}

/** AI 重新生成摘要（级联重写全部平台文案） */
export async function regenerateSummary(copyId: number) {
  return req<SummaryResult>(request.post(`/reviews/${copyId}/summary/regenerate`))
}

/** 手动编辑摘要（级联重写全部平台文案） */
export async function updateSummary(copyId: number, data: { summary_text: string; key_points: string[]; short_title: string }) {
  return req<SummaryResult>(request.put(`/reviews/${copyId}/summary`, data))
}

/** 重新扩写单条平台文案 */
export async function regenerateCopy(copyId: number) {
  return req<CopyResult>(request.post(`/reviews/${copyId}/copy/regenerate`))
}

/** 按指定平台风格预览扩写（不落库） */
export async function previewCopy(copyId: number, platform: string) {
  return req<CopyResult>(request.post(`/reviews/${copyId}/copy/preview`, { platform }))
}

/** 删除单条文案（软删除，移入回收站） */
export async function deleteReview(copyId: number) {
  return req<{ copy_id: number; deleted: boolean }>(request.post(`/reviews/${copyId}/delete`))
}

/** 批量删除文案（软删除） */
export async function batchDeleteReviews(copyIds: number[]) {
  return req<{ deleted: number }>(request.post('/reviews/batch-delete', { copy_ids: copyIds }))
}
