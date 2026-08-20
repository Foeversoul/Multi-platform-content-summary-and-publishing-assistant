<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'

import * as reviewsApi from '@/api/reviews'
import BaseTable from '@/components/BaseTable.vue'
import VerdictTag from '@/components/VerdictTag.vue'
import { useConfirm } from '@/composables/useConfirm'
import { useReviewStore } from '@/stores/review'
import { formatDateTime, platformLabel } from '@/utils/format'

const { confirm, prompt } = useConfirm()
const router = useRouter()
const store = useReviewStore()
/** U3 批量操作：多选行 */
const selection = ref<Record<string, unknown>[]>([])
const batchActing = ref(false)
const oneClickActing = ref(false)

onMounted(() => {
  void store.loadList()
})

function onSearch() {
  store.page = 1
  void store.loadList()
}

/** 一键审核：直接通过全部待审核文案 */
async function onPublishAll() {
  if (!store.total) {
    ElMessage.warning('当前没有待审核文案')
    return
  }
  const ok = await confirm({
    title: '一键审核',
    message: `将直接通过全部 ${store.total} 条待审核文案，标记为已发布。确认继续？`,
    confirmText: '一键通过',
    type: 'warning',
  })
  if (!ok) return
  oneClickActing.value = true
  try {
    const published = await store.publishAll()
    ElMessage.success(`已一键通过 ${published} 条待审核文案`)
  } catch {
    // 统一错误提示
  } finally {
    oneClickActing.value = false
  }
}

function openDetail(copyId: number) {
  void router.push(`/reviews/${copyId}`)
}

/** U3：批量发布选中内容的全部平台副本 */
async function onBatchPublish() {
  if (!selection.value.length) {
    ElMessage.warning('请先勾选要发布的内容')
    return
  }
  const total = selection.value.length
  batchActing.value = true
  try {
    let count = 0
    for (const row of selection.value as Array<{ platforms?: { copy_id: number }[] }>) {
      for (const cp of row.platforms ?? []) {
        await reviewsApi.publishReview(Number(cp.copy_id))
        count += 1
      }
    }
    selection.value = []
    await store.loadList()
    ElMessage.success(`已发布 ${total} 篇内容（${count} 条平台文案）`)
  } finally {
    batchActing.value = false
  }
}

/** U3：批量驳回（统一理由，作用于选中内容的全部平台副本） */
async function onBatchReject() {
  if (!selection.value.length) {
    ElMessage.warning('请先勾选要驳回的内容')
    return
  }
  const total = selection.value.length
  const reason = await prompt({
    title: '批量驳回',
    message: `将驳回选中的 ${total} 篇内容的全部平台文案，请填写统一驳回理由（必填）`,
    placeholder: '统一驳回理由',
    confirmText: '确认驳回',
    type: 'danger',
  })
  if (reason === null) return
  batchActing.value = true
  try {
    for (const row of selection.value as Array<{ platforms?: { copy_id: number }[] }>) {
      for (const cp of row.platforms ?? []) {
        await reviewsApi.rejectReview(Number(cp.copy_id), reason)
      }
    }
    selection.value = []
    await store.loadList()
    ElMessage.success(`已驳回 ${total} 篇内容`)
  } finally {
    batchActing.value = false
  }
}

/** 删除单篇内容（删除其全部平台副本，移入回收站可恢复） */
async function onDelete(row: Record<string, unknown>) {
  const copyIds = ((row.platforms ?? []) as { copy_id: number }[]).map((p) => Number(p.copy_id))
  const ok = await confirm({
    title: '删除内容',
    message: `将删除「${row.article_title}」的全部 ${copyIds.length} 个平台文案并移入回收站，可随时恢复。确认删除？`,
    confirmText: '删除',
    type: 'danger',
  })
  if (!ok) return
  batchActing.value = true
  try {
    await reviewsApi.batchDeleteReviews(copyIds)
    await store.loadList()
    ElMessage.success('已移入回收站')
  } catch {
    // 统一错误提示
  } finally {
    batchActing.value = false
  }
}

/** 批量删除（删除各选中内容的全部平台副本，移入回收站） */
async function onBatchDelete() {
  if (!selection.value.length) {
    ElMessage.warning('请先勾选要删除的内容')
    return
  }
  const copyIds = (selection.value as Array<{ platforms?: { copy_id: number }[] }>).flatMap((row) =>
    (row.platforms ?? []).map((p) => Number(p.copy_id)),
  )
  const ok = await confirm({
    title: '批量删除',
    message: `将删除选中的 ${selection.value.length} 篇内容的全部平台文案并移入回收站，可随时恢复。确认删除？`,
    confirmText: '批量删除',
    type: 'danger',
  })
  if (!ok) return
  batchActing.value = true
  try {
    const deleted = await reviewsApi.batchDeleteReviews(copyIds)
    selection.value = []
    await store.loadList()
    ElMessage.success(`已删除 ${deleted} 条，可在回收站中恢复`)
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
      <h3 class="page-title">待审核文案</h3>
      <span class="page-subtitle">共 {{ store.total }} 条待处理</span>
      <el-button class="one-click-btn" type="success" plain :loading="oneClickActing" @click="onPublishAll">
        一键通过全部
      </el-button>
    </div>
    <el-form inline class="filter-bar">
      <el-form-item label="平台">
        <el-select v-model="store.platform" clearable placeholder="全部" style="width: 130px" @change="onSearch">
          <el-option label="微博" value="weibo" />
          <el-option label="朋友圈" value="moments" />
          <el-option label="小红书" value="xhs" />
        </el-select>
      </el-form-item>
      <el-form-item label="状态">
        <el-select v-model="store.verdict" clearable placeholder="全部" style="width: 130px" @change="onSearch">
          <el-option label="待审核" value="pending" />
          <el-option label="已发布" value="pass" />
          <el-option label="已驳回" value="reject" />
        </el-select>
      </el-form-item>
      <el-form-item>
        <el-input
          v-model="store.keyword"
          placeholder="搜索文章标题 / 文案内容"
          clearable
          style="width: 240px"
          @keyup.enter="onSearch"
          @clear="onSearch"
        />
      </el-form-item>
      <el-form-item>
        <el-button type="primary" @click="onSearch">搜索</el-button>
      </el-form-item>
    </el-form>

    <div v-if="selection.length" class="batch-bar">
      <span>已选 {{ selection.length }} 条</span>
      <el-button type="success" plain :loading="batchActing" @click="onBatchPublish">批量发布</el-button>
      <el-button type="danger" plain :loading="batchActing" @click="onBatchReject">批量驳回</el-button>
      <el-button type="danger" plain :loading="batchActing" @click="onBatchDelete">批量删除</el-button>
    </div>

    <BaseTable
      selectable
      :columns="[
        { prop: 'article_title', label: '文章标题' },
        { prop: 'platform', label: '平台', width: 180 },
        { prop: 'scores', label: '质量评分', width: 160 },
        { prop: 'verdict', label: '状态', width: 100 },
        { prop: 'created_at', label: '生成时间', width: 180 },
      ]"
      :data="store.items as unknown as Record<string, unknown>[]"
      :loading="store.loading"
      empty-text="暂无待审文案"
      :action-width="90"
      @action="store.loadList"
      @selection="selection = $event"
    >
      <template #article_title="{ row }">
        <el-link type="primary" @click="openDetail(row.copy_id)">{{ row.article_title }}</el-link>
      </template>
      <template #platform="{ row }">
        <div class="platform-tags">
          <el-tag v-for="p in (row.platforms ?? []).slice(0, 3)" :key="p.copy_id" size="small" effect="light">
            {{ platformLabel(p.platform) }}
          </el-tag>
          <span v-if="(row.platforms?.length ?? 0) > 3" class="platform-more">+{{ row.platforms.length - 3 }}</span>
        </div>
      </template>
      <template #scores="{ row }">
        <span>{{ row.scores?.overall_score ?? row.scores?.style_score ?? '—' }}</span>
      </template>
      <template #verdict="{ row }">
        <VerdictTag :verdict="row.verdict" />
      </template>
      <template #created_at="{ row }">
        {{ formatDateTime(row.created_at) }}
      </template>
      <template #actions="{ row }">
        <el-button size="small" type="danger" plain :loading="batchActing" @click="onDelete(row)">删除</el-button>
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
.filter-bar {
  margin-bottom: 8px;
}
/* 窄屏下筛选项自动换行（响应式） */
.filter-bar :deep(.el-form-item) {
  margin-bottom: 8px;
  margin-right: 16px;
}
@media (max-width: 900px) {
  .filter-bar :deep(.el-form--inline) {
    flex-wrap: wrap;
  }
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
.one-click-btn {
  margin-left: auto;
}
.platform-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  align-items: center;
}
.platform-more {
  color: var(--text-3);
  font-size: 12px;
}
.pagination {
  margin-top: 16px;
  justify-content: flex-end;
}
</style>
