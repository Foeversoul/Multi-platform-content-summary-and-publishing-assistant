/** IF-06~08 死信管理接口 */
import type { FailedEvent } from '@/types/api'
import request, { request as req } from '@/utils/request'

export interface FailedListParams {
  page?: number
  page_size?: number
}

export async function fetchFailed(params: FailedListParams) {
  return req<{ items: FailedEvent[]; total: number }>(request.get('/failed', { params }))
}

/** 死信重跑（IF-07） */
export async function retryFailedEvent(eventId: string) {
  return req<{ event_id: string; status: 'retried' }>(request.post(`/failed/${eventId}/retry`))
}

/** 死信放弃（IF-08） */
export async function discardFailedEvent(eventId: string) {
  return req<{ event_id: string; status: 'discarded' }>(request.post(`/failed/${eventId}/discard`))
}
