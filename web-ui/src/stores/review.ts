/** 待审列表/操作状态（PRD 4.3 useReviewStore） */
import { defineStore } from 'pinia'
import { ref } from 'vue'

import * as reviewsApi from '@/api/reviews'
import type { ReviewListItem } from '@/types/api'

export const useReviewStore = defineStore('review', () => {
  /** 列表分页/筛选/搜索参数 */
  const page = ref(1)
  const pageSize = ref(20)
  const platform = ref('')
  const verdict = ref('')
  const keyword = ref('')
  const items = ref<ReviewListItem[]>([])
  const total = ref(0)
  const loading = ref(false)
  /** 操作中标记，防重复提交（AC-17-01） */
  const actingCopyId = ref<number | null>(null)

  async function loadList() {
    loading.value = true
    try {
      const data = await reviewsApi.fetchReviews({
        page: page.value,
        page_size: pageSize.value,
        platform: platform.value || undefined,
        verdict: verdict.value || undefined,
        keyword: keyword.value || undefined,
      })
      items.value = data.items
      total.value = data.total
    } finally {
      loading.value = false
    }
  }

  async function publish(copyId: number) {
    actingCopyId.value = copyId
    try {
      await reviewsApi.publishReview(copyId)
      // 基于响应更新本地状态，避免整页刷新（PRD 4.3）
      items.value = items.value.filter((it) => it.copy_id !== copyId)
      total.value = Math.max(0, total.value - 1)
    } finally {
      actingCopyId.value = null
    }
  }

  /** 一键审核：通过全部待审核文案并刷新列表，返回通过条数 */
  async function publishAll() {
    const { published } = await reviewsApi.batchPublishReviews()
    await loadList()
    return published
  }

  async function reject(copyId: number, comment: string) {
    actingCopyId.value = copyId
    try {
      await reviewsApi.rejectReview(copyId, comment)
      items.value = items.value.filter((it) => it.copy_id !== copyId)
      total.value = Math.max(0, total.value - 1)
    } finally {
      actingCopyId.value = null
    }
  }

  /** 删除单条（软删除，移入回收站） */
  async function remove(copyId: number) {
    await reviewsApi.deleteReview(copyId)
    items.value = items.value.filter((it) => it.copy_id !== copyId)
    total.value = Math.max(0, total.value - 1)
  }

  /** 批量删除（软删除），返回实际删除条数 */
  async function batchRemove(copyIds: number[]) {
    const { deleted } = await reviewsApi.batchDeleteReviews(copyIds)
    const ids = new Set(copyIds)
    items.value = items.value.filter((it) => !ids.has(it.copy_id))
    total.value = Math.max(0, total.value - deleted)
    return deleted
  }

  return { page, pageSize, platform, verdict, keyword, items, total, loading, actingCopyId, loadList, publish, publishAll, reject, remove, batchRemove }
})
