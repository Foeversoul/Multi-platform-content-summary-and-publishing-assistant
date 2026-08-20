<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { discardFailedEvent, fetchFailed, retryFailedEvent } from '@/api/failed'
import BaseTable from '@/components/BaseTable.vue'
import type { FailedEvent } from '@/types/api'
import { formatDateTime } from '@/utils/format'

const items = ref<FailedEvent[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const loading = ref(false)
const actingId = ref<string | null>(null)
/** U4：payload 展开抽屉 */
const payloadVisible = ref(false)
const currentPayload = ref<Record<string, unknown>>({})

function onViewPayload(row: FailedEvent) {
  currentPayload.value = row.payload ?? {}
  payloadVisible.value = true
}

async function loadList() {
  loading.value = true
  try {
    const data = await fetchFailed({ page: page.value, page_size: pageSize.value })
    items.value = data.items
    total.value = data.total
  } catch {
    // 网络/后端异常已由 request 拦截器统一提示，这里保持列表空态
  } finally {
    loading.value = false
  }
}

onMounted(loadList)

async function onRetry(eventId: string) {
  actingId.value = eventId
  try {
    await retryFailedEvent(eventId)
    ElMessage.success('已重新入队')
    await loadList()
  } finally {
    actingId.value = null
  }
}

async function onDiscard(eventId: string) {
  try {
    await ElMessageBox.confirm('确认放弃该死信事件？此操作不可恢复。', '放弃确认', { type: 'warning' })
  } catch {
    return
  }
  actingId.value = eventId
  try {
    await discardFailedEvent(eventId)
    ElMessage.success('已放弃')
    await loadList()
  } finally {
    actingId.value = null
  }
}
</script>

<template>
  <div class="page-card">
    <div class="page-head">
      <h3 class="page-title">死信管理</h3>
      <span class="page-subtitle">共 {{ total }} 条失败事件 · 支持重跑或放弃</span>
    </div>
    <BaseTable
      :columns="[
        { prop: 'event_id', label: '事件 ID' },
        { prop: 'event_type', label: '事件类型', width: 180 },
        { prop: 'error', label: '失败原因' },
        { prop: 'created_at', label: '发生时间', width: 180 },
      ]"
      :data="items as unknown as Record<string, unknown>[]"
      :loading="loading"
      :action-width="230"
      empty-text="暂无死信事件"
      @action="loadList"
    >
      <template #event_id="{ row }">
        <el-tag size="small" type="danger">{{ row.event_id }}</el-tag>
      </template>
      <template #error="{ row }">
        <el-tooltip :content="row.error || '—'" placement="top">
          <span class="err-text">{{ row.error || '—' }}</span>
        </el-tooltip>
      </template>
      <template #created_at="{ row }">{{ formatDateTime(row.created_at) }}</template>
      <template #actions="{ row }">
        <el-button size="small" type="primary" link @click="onViewPayload(row as unknown as FailedEvent)">载荷</el-button>
        <el-button size="small" type="primary" plain :loading="actingId === row.event_id" @click="onRetry(row.event_id)">重跑</el-button>
        <el-button size="small" type="danger" plain :loading="actingId === row.event_id" @click="onDiscard(row.event_id)">放弃</el-button>
      </template>
    </BaseTable>

    <el-drawer v-model="payloadVisible" title="事件载荷 (Payload)" size="60%">
      <pre class="payload-view">{{ JSON.stringify(currentPayload, null, 2) }}</pre>
    </el-drawer>

    <el-pagination
      v-model:current-page="page"
      v-model:page-size="pageSize"
      class="pagination"
      layout="total, prev, pager, next"
      :total="total"
      @change="loadList"
    />
  </div>
</template>

<style scoped>
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
.err-text {
  color: #f56c6c;
  font-size: 13px;
  display: inline-block;
  max-width: 320px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pagination {
  margin-top: 16px;
  justify-content: flex-end;
}
.payload-view {
  margin: 0;
  padding: 16px;
  background: #fafafa;
  border-radius: 6px;
  font-size: 13px;
  line-height: 1.6;
  overflow: auto;
  max-height: 70vh;
}
</style>
