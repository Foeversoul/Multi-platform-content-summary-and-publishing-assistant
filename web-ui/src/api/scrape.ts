/** IF-09~13 URL 上传爬取接口 */
import type { ScrapeJobCreated, ScrapeJobItemDetail, ScrapeJobProgress, ScrapeRetryCreated } from '@/types/api'
import request, { request as req } from '@/utils/request'

/** 爬取任务历史列表（U1） */
export async function fetchScrapeJobs(params: { page?: number; page_size?: number }) {
  return req<{ items: ScrapeJobProgress[]; total: number }>(request.get('/scrape/jobs', { params }))
}

/** 创建爬取任务（IF-09） */
export async function createScrapeJob(urls: string[]) {
  return req<ScrapeJobCreated>(request.post('/scrape/jobs', { urls }))
}

/** 任务进度与汇总（IF-10，轮询） */
export async function fetchScrapeJob(jobId: number) {
  return req<ScrapeJobProgress>(request.get(`/scrape/jobs/${jobId}`))
}

/** 任务条目明细（IF-11） */
export async function fetchScrapeJobItems(jobId: number, params: { page?: number; page_size?: number; status?: string }) {
  return req<{ items: ScrapeJobItemDetail[]; total: number }>(request.get(`/scrape/jobs/${jobId}/items`, { params }))
}

/** 单条目结果（IF-12） */
export async function fetchScrapeItem(itemId: number) {
  return req<ScrapeJobItemDetail>(request.get(`/scrape/items/${itemId}`))
}

/** 失败条目重新提交（IF-13） */
export async function retryScrapeItem(jobId: number, itemId: number) {
  return req<ScrapeRetryCreated>(request.post(`/scrape/jobs/${jobId}/items/${itemId}/retry`))
}
