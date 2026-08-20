/** 运行状态监控（PRD 4.3 useStatusStore，30s 轮询） */
import { defineStore } from 'pinia'
import { ref } from 'vue'

import * as statusApi from '@/api/status'
import type { SystemStatus } from '@/types/api'

const POLL_INTERVAL_MS = 30_000

export const useStatusStore = defineStore('status', () => {
  const data = ref<SystemStatus | null>(null)
  const loading = ref(false)
  const lastUpdatedAt = ref<string>('')
  /** 轮询句柄：仅此 store 管理，页面卸载时清理 */
  let timer: number | null = null

  async function refresh() {
    loading.value = true
    try {
      data.value = await statusApi.fetchStatus()
      lastUpdatedAt.value = new Date().toLocaleTimeString('zh-CN')
    } finally {
      loading.value = false
    }
  }

  function startPolling() {
    stopPolling()
    void refresh()
    timer = window.setInterval(refresh, POLL_INTERVAL_MS)
  }

  function stopPolling() {
    if (timer !== null) {
      clearInterval(timer)
      timer = null
    }
  }

  return { data, loading, lastUpdatedAt, refresh, startPolling, stopPolling }
})
