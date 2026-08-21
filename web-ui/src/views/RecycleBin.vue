<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'

import BaseTable from '@/components/BaseTable.vue'
import VerdictTag from '@/components/VerdictTag.vue'
import { useConfirm } from '@/composables/useConfirm'
import { useRecycleStore } from '@/stores/recycle'
import { formatDateTime, platformLabel } from '@/utils/format'

const { confirm } = useConfirm()
const store = useRecycleStore()
const actingId = ref<number | null>(null)
const selection = ref<Record<string, unknown>[]>([])
const batchActing = ref(false)

onMounted(() => {
  void store.loadList()
})

/** 恢复文案到待审列表 */
async function onRestore(copyId: number) {
  actingId.value = copyId
  try {
    await store.restore(copyId)
    ElMessage.success('已恢复到待审列表')
  } catch {
    // 统一错误提示
  } finally {
    actingId.value = null
  }
}

/** 永久删除（二次确认，不可恢复） */
async function onPurge(row: Record<string, unknown>) {
  const copyId = Number(row.copy_id)
  const ok = await confirm({
    title: '永久删除',
    message: `将永久删除「${row.article_title}」的该平台文案及全部审核记录，此操作不可恢复！确认继续？`,
    confirmText: '永久删除',
    type: 'danger',
  })
  if (!ok) return
  actingId.value = copyId
  try {
    await store.purge(copyId)
    ElMessage.success('已永久删除')
  } catch {
    // 统一错误提示
  } finally {
    actingId.value = null
  }
}

/** 批量恢复选中文案到待审列表 */
async function onBatchRestore() {
  const copyIds = selection.value.map((row) => Number(row.copy_id))
  if (!copyIds.length) {
    ElMessage.warning('请先勾选要恢复的文案')
    return
  }
  batchActing.value = true
  try {
    const restored = await store.batchRestore(copyIds)
    selection.value = []
    ElMessage.success(`已恢复 ${restored} 条文案到待审列表`)
  } catch {
    // 统一错误提示
  } finally {
    batchActing.value = false
  }
}

/** 批量永久删除（二次确认，不可恢复） */
async function onBatchPurge() {
  const copyIds = selection.value.map((row) => Number(row.copy_id))
  if (!copyIds.length) {
    ElMessage.warning('请先勾选要删除的文案')
    return
  }
  const ok = await confirm({
    title: '批量永久删除',
    message: `将永久删除选中的 ${copyIds.length} 条文案及全部审核记录，此操作不可恢复！确认继续？`,
    confirmText: '永久删除',
    type: 'danger',
  })
  if (!ok) return
  batchActing.value = true
  try {
    const purged = await store.batchPurge(copyIds)
    selection.value = []
    ElMessage.success(`已永久删除 ${purged} 条文案`)
  } catch {
    // 统一错误提示
  } finally {
    batchActing.value = false
  }
}
</script>

<template>
  <div class="page-card">
    <div class="page-head">
      <h3 class="page-title">回收站</h3>
      <span class="page-subtitle">共 {{ store.total }} 条已删除内容，可恢复或永久删除</span>
    </div>

    <div v-if="selection.length" class="batch-bar">
      <span>已选 {{ selection.length }} 条</span>
      <el-button type="primary" plain :loading="batchActing" @click="onBatchRestore">批量恢复</el-button>
      <el-button type="danger" plain :loading="batchActing" @click="onBatchPurge">批量永久删除</el-button>
    </div>

    <BaseTable
      selectable
      :columns="[
        { prop: 'article_title', label: '文章标题' },
        { prop: 'platform', label: '平台', width: 120 },
        { prop: 'verdict', label: '原状态', width: 110 },
        { prop: 'deleted_at', label: '删除时间', width: 180 },
      ]"
      :data="store.items as unknown as Record<string, unknown>[]"
      :loading="store.loading"
      empty-text="回收站为空"
      :action-width="160"
      @action="store.loadList"
      @selection="selection = $event"
    >
      <template #article_title="{ row }">
        <span :title="String(row.text || '')">{{ row.article_title }}</span>
      </template>
      <template #platform="{ row }">
        <el-tag size="small" type="info" effect="light">{{ platformLabel(row.platform) }}</el-tag>
      </template>
      <template #verdict="{ row }">
        <VerdictTag :verdict="row.verdict" />
      </template>
      <template #deleted_at="{ row }">
        {{ formatDateTime(row.deleted_at) }}
      </template>
      <template #actions="{ row }">
        <el-button size="small" type="primary" plain :loading="actingId === row.copy_id" @click="onRestore(Number(row.copy_id))">
          恢复
        </el-button>
        <el-button size="small" type="danger" plain :disabled="actingId === row.copy_id" @click="onPurge(row)">
          永久删除
        </el-button>
      </template>
    </BaseTable>

    <el-pagination
      v-model:current-page="store.page"
      v-model:page-size="store.pageSize"
      class="pagination"
      layout="total, sizes, prev, pager, next"
      :total="store.total"
      :page-sizes="[10, 20, 50]"
      @change="store.loadList"
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
.batch-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
  padding: 10px 16px;
  background: linear-gradient(90deg, var(--el-color-primary-light-9) 0%, #ffffff 100%);
  border: 1px solid var(--el-color-primary-light-7);
  border-radius: var(--radius-md);
  font-size: 13px;
  color: var(--brand-600);
}
.pagination {
  margin-top: 16px;
  justify-content: flex-end;
}
</style>
