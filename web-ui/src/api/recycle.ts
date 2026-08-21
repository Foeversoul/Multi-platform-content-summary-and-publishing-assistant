/** 回收站（软删除内容的恢复与永久删除） */
import type { RecycleItem } from '@/types/api'
import request, { request as req } from '@/utils/request'

export interface RecycleListParams {
  page?: number
  page_size?: number
}

export async function fetchRecycleItems(params: RecycleListParams) {
  return req<{ items: RecycleItem[]; total: number }>(request.get('/recycle', { params }))
}

/** 恢复文案 */
export async function restoreCopy(copyId: number) {
  return req<{ copy_id: number; restored: boolean }>(request.post(`/recycle/${copyId}/restore`))
}

/** 永久删除（不可恢复） */
export async function purgeCopy(copyId: number) {
  return req<{ copy_id: number; purged: boolean }>(request.delete(`/recycle/${copyId}`))
}

/** 批量恢复文案 */
export async function batchRestoreCopy(copyIds: number[]) {
  return req<{ restored: number }>(request.post('/recycle/batch-restore', { copy_ids: copyIds }))
}

/** 批量永久删除（不可恢复） */
export async function batchPurgeCopy(copyIds: number[]) {
  return req<{ purged: number }>(request.post('/recycle/batch-purge', { copy_ids: copyIds }))
}
