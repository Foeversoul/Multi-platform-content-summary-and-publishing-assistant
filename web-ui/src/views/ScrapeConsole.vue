<script setup lang="ts">
import { onBeforeUnmount, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'

import ErrorTag from '@/components/ErrorTag.vue'
import JobProgress from '@/components/JobProgress.vue'
import ManualUpload from '@/components/ManualUpload.vue'
import UrlUploader from '@/components/UrlUploader.vue'
import { useScrapeStore } from '@/stores/scrape'
import { formatDateTime, itemStatusLabel, itemStatusTag, jobStatusLabel, jobStatusTag } from '@/utils/format'

const router = useRouter()
const store = useScrapeStore()

async function onSubmit(urls: string[]) {
  try {
    const created = await store.submit(urls)
    const dedupText = store.lastDedupCount > 0 ? `，去重 ${store.lastDedupCount} 条（BR-20-02）` : ''
    ElMessage.success(`任务 #${created.job_id} 已创建，共 ${created.url_count} 条 URL${dedupText}`)
  } catch {
    // 统一错误提示
  }
}

async function onRetry(itemId: number) {
  if (!store.job) return
  try {
    const created = await store.retryFailed(store.job.job_id, itemId)
    ElMessage.success(`已创建新任务 #${created.new_job_id}，正在重新爬取`)
    await router.push('/scrape')
  } catch {
    // 统一错误提示
  }
}

onMounted(() => {
  // 页面加载时如果有在途任务则恢复轮询
  if (store.job && !['succeeded', 'failed', 'partial'].includes(store.job.status)) {
    store.startPolling(store.job.job_id)
  }
  void store.loadJobs(1, 10)
})
onBeforeUnmount(() => store.stopPolling())

/** 查看历史任务明细（U1） */
function onSelectJob(jobId: number) {
  store.selectJob(jobId)
}
</script>

<template>
  <div class="scrape-page">
    <div class="page-card">
      <div class="page-head">
        <h3 class="page-title">内容导入</h3>
        <span class="page-subtitle">自动进入 摘要 → 多平台文案 → 待审 流程</span>
      </div>
      <el-tabs class="import-tabs">
        <el-tab-pane label="URL 上传爬取">
          <UrlUploader @submit="onSubmit" />
          <el-alert
            class="rule-tip"
            type="info"
            :closable="false"
            title="规则：仅支持 http/https；单批最多 1000 条；同批重复 URL 自动去重；成功条目自动进入 摘要 → 平台文案 → 待审 流程。"
          />
        </el-tab-pane>
        <el-tab-pane label="手动内容上传">
          <ManualUpload />
        </el-tab-pane>
      </el-tabs>
    </div>

    <div v-if="store.job" class="page-card result-card">
      <h3>任务 #{{ store.job.job_id }} 进度</h3>
      <JobProgress
        :status="store.job.status"
        :url-count="store.job.url_count"
        :succeeded-count="store.job.succeeded_count"
        :failed-count="store.job.failed_count"
      />
      <div class="job-meta">
        创建于 {{ formatDateTime(store.job.created_at) }}，完成于 {{ formatDateTime(store.job.finished_at, '进行中…') }}
      </div>

      <el-table v-loading="store.itemsTotal === 0 && store.job.status === 'pending'" :data="store.items" stripe class="item-table">
        <el-table-column prop="url" label="URL" min-width="260" show-overflow-tooltip />
        <el-table-column prop="status" label="状态" width="110">
          <template #default="{ row }">
            <el-tag size="small" :type="itemStatusTag(row.status)">{{ itemStatusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="error_code" label="错误" width="150">
          <template #default="{ row }">
            <ErrorTag :code="row.error_code" />
          </template>
        </el-table-column>
        <el-table-column prop="error_message" label="失败原因" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">
            <span v-if="row.error_message" class="err-msg">{{ row.error_message }}</span>
            <span v-else>—</span>
          </template>
        </el-table-column>
        <el-table-column prop="finished_at" label="完成时间" width="180">
          <template #default="{ row }">{{ formatDateTime(row.finished_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="110" fixed="right">
          <template #default="{ row }">
            <el-button v-if="row.status === 'failed'" size="small" type="primary" plain @click="onRetry(row.item_id)">
              重新提交
            </el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="暂无任务明细，等待执行…" />
        </template>
      </el-table>
    </div>

    <div class="page-card result-card">
      <h3>历史任务（最近 {{ store.jobsTotal }} 个）</h3>
      <el-table v-loading="store.jobsLoading" :data="store.jobs" stripe>
        <el-table-column prop="job_id" label="任务 ID" width="90" />
        <el-table-column prop="status" label="状态" width="110">
          <template #default="{ row }">
            <el-tag size="small" :type="jobStatusTag(row.status)">{{ jobStatusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="url_count" label="URL 数" width="90" />
        <el-table-column prop="succeeded_count" label="成功" width="80" />
        <el-table-column prop="failed_count" label="失败" width="80" />
        <el-table-column prop="created_at" label="创建时间" width="180">
          <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="110">
          <template #default="{ row }">
            <el-button size="small" type="primary" plain @click="onSelectJob(row.job_id)">查看明细</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="暂无历史任务" />
        </template>
      </el-table>
    </div>
  </div>
</template>

<style scoped>
.rule-tip {
  margin-top: 16px;
}
.page-head {
  display: flex;
  align-items: baseline;
  gap: 12px;
  margin-bottom: 16px;
}
.page-head .page-title {
  margin: 0;
}
.page-subtitle {
  color: var(--text-3);
  font-size: 13px;
}
.result-card {
  margin-top: 20px;
}
.job-meta {
  color: #909399;
  font-size: 13px;
  margin: 8px 0 12px;
}
.item-table {
  margin-top: 4px;
}
.err-msg {
  color: #f56c6c;
  font-size: 13px;
}
</style>
