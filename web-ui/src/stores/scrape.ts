/** 爬取任务状态（PRD 4.3 useScrapeStore，提交后每 2s 轮询） */
import { defineStore } from 'pinia'
import { ref } from 'vue'

import * as scrapeApi from '@/api/scrape'
import type { ScrapeJobItemDetail, ScrapeJobProgress } from '@/types/api'

const POLL_INTERVAL_MS = 2_000
const TERMINAL = new Set(['succeeded', 'failed', 'partial'])

export const useScrapeStore = defineStore('scrape', () => {
  const job = ref<ScrapeJobProgress | null>(null)
  const items = ref<ScrapeJobItemDetail[]>([])
  const itemsTotal = ref(0)
  const submitting = ref(false)
  const lastDedupCount = ref(0)
  /** 历史任务列表（U1） */
  const jobs = ref<ScrapeJobProgress[]>([])
  const jobsTotal = ref(0)
  const jobsLoading = ref(false)
  /** 轮询句柄：页面卸载时停止（PRD 4.3） */
  let timer: number | null = null

  async function loadJobs(page = 1, pageSize = 10) {
    jobsLoading.value = true
    try {
      const data = await scrapeApi.fetchScrapeJobs({ page, page_size: pageSize })
      jobs.value = data.items
      jobsTotal.value = data.total
    } catch {
      // 网络/后端异常已由 request 拦截器统一提示，这里保持列表空态
    } finally {
      jobsLoading.value = false
    }
  }

  /** 切换查看指定任务并恢复轮询 */
  function selectJob(jobId: number) {
    startPolling(jobId)
  }

  async function submit(urls: string[]) {
    submitting.value = true
    try {
      const created = await scrapeApi.createScrapeJob(urls)
      lastDedupCount.value = created.dedup_count
      job.value = {
        job_id: created.job_id,
        status: created.status,
        url_count: created.url_count,
        succeeded_count: 0,
        failed_count: 0,
        created_at: new Date().toISOString(),
        finished_at: null,
      }
      startPolling(created.job_id)
      return created
    } finally {
      submitting.value = false
    }
  }

  async function refresh(jobId: number) {
    const next = await scrapeApi.fetchScrapeJob(jobId)
    job.value = next
    const pageData = await scrapeApi.fetchScrapeJobItems(jobId, { page: 1, page_size: 50 })
    items.value = pageData.items
    itemsTotal.value = pageData.total
    if (TERMINAL.has(next.status)) {
      stopPolling()
    }
  }

  function startPolling(jobId: number) {
    stopPolling()
    void refresh(jobId)
    timer = window.setInterval(() => void refresh(jobId), POLL_INTERVAL_MS)
  }

  function stopPolling() {
    if (timer !== null) {
      clearInterval(timer)
      timer = null
    }
  }

  /** 失败条目重新提交：保留原始 URL，生成新任务并跳转轮询 */
  async function retryFailed(jobId: number, itemId: number) {
    const created = await scrapeApi.retryScrapeItem(jobId, itemId)
    startPolling(created.new_job_id)
    return created
  }

  return {
    job,
    items,
    itemsTotal,
    submitting,
    lastDedupCount,
    jobs,
    jobsTotal,
    jobsLoading,
    submit,
    refresh,
    startPolling,
    stopPolling,
    retryFailed,
    loadJobs,
    selectJob,
  }
})
