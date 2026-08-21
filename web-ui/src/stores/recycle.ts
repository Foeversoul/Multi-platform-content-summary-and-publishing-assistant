/** 回收站状态（软删除内容的恢复与永久删除） */
import { defineStore } from 'pinia'
import { ref } from 'vue'

import * as recycleApi from '@/api/recycle'
import type { RecycleItem } from '@/types/api'

export const useRecycleStore = defineStore('recycle', () => {
  const page = ref(1)
  const pageSize = ref(20)
  const items = ref<RecycleItem[]>([])
  const total = ref(0)
  const loading = ref(false)

  async function loadList() {
    loading.value = true
    try {
      const data = await recycleApi.fetchRecycleItems({ page: page.value, page_size: pageSize.value })
      items.value = data.items
      total.value = data.total
    } catch {
      // 网络/后端异常已由 request 拦截器统一提示，这里保持空态
    } finally {
      loading.value = false
    }
  }

  /** 恢复文案，回到待审列表 */
  async function restore(copyId: number) {
    await recycleApi.restoreCopy(copyId)
    items.value = items.value.filter((it) => it.copy_id !== copyId)
    total.value = Math.max(0, total.value - 1)
  }

  /** 永久删除（不可恢复） */
  async function purge(copyId: number) {
    await recycleApi.purgeCopy(copyId)
    items.value = items.value.filter((it) => it.copy_id !== copyId)
    total.value = Math.max(0, total.value - 1)
  }

  /** 批量恢复，返回实际恢复条数 */
  async function batchRestore(copyIds: number[]) {
    const { restored } = await recycleApi.batchRestoreCopy(copyIds)
    const idSet = new Set(copyIds)
    items.value = items.value.filter((it) => !idSet.has(it.copy_id))
    total.value = Math.max(0, total.value - restored)
    return restored
  }

  /** 批量永久删除（不可恢复），返回实际删除条数 */
  async function batchPurge(copyIds: number[]) {
    const { purged } = await recycleApi.batchPurgeCopy(copyIds)
    const idSet = new Set(copyIds)
    items.value = items.value.filter((it) => !idSet.has(it.copy_id))
    total.value = Math.max(0, total.value - purged)
    return purged
  }

  return { page, pageSize, items, total, loading, loadList, restore, purge, batchRestore, batchPurge }
})
