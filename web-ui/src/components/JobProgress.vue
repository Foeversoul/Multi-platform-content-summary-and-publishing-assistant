<script setup lang="ts">
import { computed } from 'vue'

import { jobStatusLabel, jobStatusTag } from '@/utils/format'

const props = defineProps<{
  status: string
  urlCount: number
  succeededCount: number
  failedCount: number
}>()

const done = computed(() => props.succeededCount + props.failedCount)
const percent = computed(() => (props.urlCount > 0 ? Math.round((done.value / props.urlCount) * 100) : 0))
const finished = computed(() => ['succeeded', 'failed', 'partial'].includes(props.status))
</script>

<template>
  <div class="job-progress">
    <div class="job-progress-head">
      <el-tag :type="jobStatusTag(status)" size="small">{{ jobStatusLabel(status) }}</el-tag>
      <span class="job-progress-count">
        已完成 {{ succeededCount }} 成功 / {{ failedCount }} 失败，共 {{ urlCount }} 条
      </span>
    </div>
    <el-progress :percentage="percent" :status="finished ? (status === 'succeeded' ? 'success' : 'warning') : undefined" />
  </div>
</template>

<style scoped>
.job-progress-head {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 8px;
}
.job-progress-count {
  color: #606266;
  font-size: 13px;
}
</style>
