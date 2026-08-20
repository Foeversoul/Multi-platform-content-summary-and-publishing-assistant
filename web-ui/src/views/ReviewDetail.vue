<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'

import { fetchReviewDetail, previewCopy, publishReview, regenerateCopy, regenerateSummary, rejectReview, updateSummary } from '@/api/reviews'
import CopyText from '@/components/CopyText.vue'
import EmptyState from '@/components/EmptyState.vue'
import ScorePanel from '@/components/ScorePanel.vue'
import { useConfirm } from '@/composables/useConfirm'
import type { ReviewDetail } from '@/types/api'
import { ApiClientError } from '@/utils/request'
import { formatDateTime, platformLabel } from '@/utils/format'

const { confirm, prompt } = useConfirm()

const route = useRoute()
const router = useRouter()
const currentCopyId = computed(() => Number(route.params.copyId))
const detail = ref<ReviewDetail | null>(null)
const loading = ref(true)
const acting = ref(false)

/** 摘要编辑弹窗 */
const summaryDialog = ref(false)
const summarySaving = ref(false)
const summaryForm = ref({ summary_text: '', key_points: [] as string[], short_title: '' })
/** 摘要/文案 AI 处理中标记 */
const regenerating = ref(false)
/** 风格预览弹窗 */
const previewDialog = ref(false)
const previewPlatform = ref('xhs')
const previewing = ref(false)
const previewText = ref('')

const PLATFORMS = [
  { value: 'weibo', label: '微博' },
  { value: 'moments', label: '朋友圈' },
  { value: 'xhs', label: '小红书' },
]

async function load() {
  loading.value = true
  try {
    detail.value = await fetchReviewDetail(currentCopyId.value)
  } finally {
    loading.value = false
  }
}

onMounted(load)
watch(
  () => route.params.copyId,
  () => {
    void load()
  },
)

async function onPublish() {
  acting.value = true
  try {
    await publishReview(currentCopyId.value)
    ElMessage.success('发布成功')
    await load()
  } catch {
    // 统一错误提示已由 request 弹出
  } finally {
    acting.value = false
  }
}

async function onReject() {
  const reason = await prompt({
    title: '驳回文案',
    message: '请填写驳回理由（必填）',
    placeholder: '例如：风格不符 / 信息有误',
    confirmText: '确认驳回',
    type: 'danger',
  })
  if (reason === null) return
  acting.value = true
  try {
    await rejectReview(currentCopyId.value, reason)
    ElMessage.success('已驳回')
    await load()
  } catch (err) {
    if (err instanceof ApiClientError) ElMessage.error(err.message)
  } finally {
    acting.value = false
  }
}

/** 多平台聚合切换（U2）：跳转至对应平台文案的详情 */
function onTabChange(name: string | number) {
  const id = Number(name)
  if (id !== currentCopyId.value) {
    // 使用 replace 替换当前历史条目，避免多次切换平台后需要多次回退才能返回列表
    router.replace(`/reviews/${id}`)
  }
}

// ---------- 摘要：重新生成 / 编辑 ----------

/** AI 重新生成摘要（级联重写全部平台文案） */
async function onRegenerateSummary() {
  const ok = await confirm({
    title: '重新生成摘要',
    message: '将调用 AI 重新生成摘要，并同步重写该文章全部平台的文案（已发布文案除外）。确认继续？',
    confirmText: '重新生成',
    type: 'warning',
  })
  if (!ok) return
  regenerating.value = true
  try {
    await regenerateSummary(currentCopyId.value)
    ElMessage.success('摘要已重新生成，平台文案已同步更新')
    await load()
  } catch {
    // 统一错误提示
  } finally {
    regenerating.value = false
  }
}

function openSummaryEdit() {
  if (!detail.value) return
  summaryForm.value = {
    summary_text: detail.value.summary.summary_text,
    key_points: [...detail.value.summary.key_points],
    short_title: detail.value.summary.short_title,
  }
  summaryDialog.value = true
}

function addKeyPoint() {
  summaryForm.value.key_points.push('')
}

function removeKeyPoint(index: number) {
  summaryForm.value.key_points.splice(index, 1)
}

/** 手动编辑摘要（级联重写全部平台文案） */
async function saveSummary() {
  if (!summaryForm.value.summary_text.trim()) {
    ElMessage.warning('摘要内容不能为空')
    return
  }
  summarySaving.value = true
  try {
    await updateSummary(currentCopyId.value, {
      summary_text: summaryForm.value.summary_text.trim(),
      key_points: summaryForm.value.key_points.map((k) => k.trim()).filter(Boolean),
      short_title: summaryForm.value.short_title.trim(),
    })
    summaryDialog.value = false
    ElMessage.success('摘要已保存，平台文案已同步更新')
    await load()
  } catch {
    // 统一错误提示
  } finally {
    summarySaving.value = false
  }
}

// ---------- 文案：重新扩写 / 风格预览 ----------

/** 重新扩写当前平台文案 */
async function onRegenerateCopy() {
  const ok = await confirm({
    title: '重新扩写',
    message: '将调用 AI 按当前平台风格重新扩写本条文案，审核状态将重置为待审核。确认继续？',
    confirmText: '重新扩写',
    type: 'warning',
  })
  if (!ok) return
  regenerating.value = true
  try {
    await regenerateCopy(currentCopyId.value)
    ElMessage.success('文案已重新生成')
    await load()
  } catch {
    // 统一错误提示
  } finally {
    regenerating.value = false
  }
}

/** 打开风格预览弹窗 */
function openPreview() {
  previewPlatform.value = detail.value?.copy.platform ?? 'xhs'
  previewText.value = ''
  previewDialog.value = true
}

/** 按所选平台风格生成预览文案（不落库） */
async function runPreview() {
  previewing.value = true
  try {
    const result = await previewCopy(currentCopyId.value, previewPlatform.value)
    previewText.value = result.text
  } catch {
    // 统一错误提示
  } finally {
    previewing.value = false
  }
}
</script>

<template>
  <div v-loading="loading" class="page-card">
    <el-page-header content="详情预览" @back="router.back()" />

    <template v-if="detail">
      <el-alert v-if="detail.publish" type="success" :closable="false" class="pub-alert">
        该文案已于 {{ formatDateTime(detail.publish.published_at) }} 发布
      </el-alert>

      <el-row :gutter="16" class="detail-grid">
        <el-col :xs="24" :md="8">
          <section class="detail-section">
            <h4>文章原文</h4>
            <div class="meta">{{ detail.article.title }}</div>
            <div class="meta">发布时间：{{ formatDateTime(detail.article.publish_time) }}</div>
            <div class="meta"><el-link type="info" :href="detail.article.url" target="_blank">{{ detail.article.url }}</el-link></div>
            <p class="article-text">{{ detail.article.text }}</p>
          </section>
        </el-col>
        <el-col :xs="24" :md="8">
          <section class="detail-section">
            <h4>标准摘要</h4>
            <div class="section-actions">
              <el-button size="small" type="primary" plain :loading="regenerating" @click="onRegenerateSummary">重新生成</el-button>
              <el-button size="small" :disabled="regenerating" @click="openSummaryEdit">编辑</el-button>
            </div>
            <div class="summary-text">{{ detail.summary.summary_text }}</div>
            <h5>要点</h5>
            <ul class="key-points">
              <li v-for="(point, i) in detail.summary.key_points" :key="i">{{ point }}</li>
            </ul>
            <h5>短标题</h5>
            <div class="meta">{{ detail.summary.short_title || '—' }}</div>
          </section>
        </el-col>
        <el-col :xs="24" :md="8">
          <section class="detail-section">
            <h4>平台文案</h4>
            <el-tabs
              v-if="detail.siblings && detail.siblings.length"
              class="platform-tabs"
              :model-value="detail.copy.id"
              @tab-change="onTabChange"
            >
              <el-tab-pane :label="`${platformLabel(detail.copy.platform)}（当前）`" :name="detail.copy.id" />
              <el-tab-pane
                v-for="sib in detail.siblings"
                :key="sib.copy_id"
                :label="`${platformLabel(sib.platform)}${sib.verdict !== 'pending' ? '（' + sib.verdict + '）' : ''}`"
                :name="sib.copy_id"
              />
            </el-tabs>
            <p class="copy-text">{{ detail.copy.text }}</p>
            <div class="actions">
              <CopyText :text="detail.copy.text" />
              <el-button :loading="regenerating" :disabled="!!detail.publish" @click="onRegenerateCopy">重新扩写</el-button>
              <el-button plain @click="openPreview">风格预览</el-button>
              <el-button v-if="!detail.publish" type="success" :loading="acting" @click="onPublish">确认发布</el-button>
              <el-button v-if="!detail.publish" type="danger" plain :disabled="acting" @click="onReject">驳回</el-button>
            </div>
            <h5>评分明细</h5>
            <ScorePanel :scores="detail.review.scores" />
          </section>
        </el-col>
      </el-row>
    </template>

    <EmptyState v-else-if="!loading" description="该文案不存在或已被处理（404）" text="返回列表" @action="router.push('/')" />

    <!-- 摘要编辑弹窗 -->
    <el-dialog v-model="summaryDialog" title="编辑摘要" width="640px" top="6vh" destroy-on-close>
      <el-form label-width="72px">
        <el-form-item label="短标题" required>
          <el-input v-model="summaryForm.short_title" maxlength="200" show-word-limit placeholder="不超过 200 字的精简标题" />
        </el-form-item>
        <el-form-item label="摘要内容" required>
          <el-input
            v-model="summaryForm.summary_text"
            type="textarea"
            :rows="8"
            show-word-limit
            placeholder="200-400 字的客观摘要"
          />
        </el-form-item>
        <el-form-item label="要点">
          <div class="key-point-editor">
            <div v-for="(_, i) in summaryForm.key_points" :key="i" class="key-point-row">
              <el-input v-model="summaryForm.key_points[i]" maxlength="60" placeholder="要点内容（不超过 60 字）" />
              <el-button type="danger" plain circle size="small" @click="removeKeyPoint(i)">－</el-button>
            </div>
            <el-button size="small" plain @click="addKeyPoint">＋ 添加要点</el-button>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="summaryDialog = false">取消</el-button>
        <el-button type="primary" :loading="summarySaving" @click="saveSummary">保存（将同步重写平台文案）</el-button>
      </template>
    </el-dialog>

    <!-- 多平台风格预览弹窗 -->
    <el-dialog v-model="previewDialog" title="多平台风格预览" width="640px" top="6vh">
      <div class="preview-bar">
        <el-select v-model="previewPlatform" style="width: 160px">
          <el-option v-for="p in PLATFORMS" :key="p.value" :label="p.label" :value="p.value" />
        </el-select>
        <el-button type="primary" :loading="previewing" @click="runPreview">生成预览</el-button>
        <span class="preview-tip">按所选平台调性即时生成，不保存到系统</span>
      </div>
      <div v-if="previewText" class="preview-result">
        <p class="copy-text">{{ previewText }}</p>
        <CopyText :text="previewText" />
      </div>
      <el-empty v-else-if="!previewing" description="选择平台后点击「生成预览」" :image-size="72" />
    </el-dialog>
  </div>
</template>

<style scoped>
.pub-alert {
  margin: 16px 0;
}
.detail-grid {
  margin-top: 16px;
}
.detail-section {
  position: relative;
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 18px;
  min-height: 300px;
  box-shadow: var(--shadow-1);
  transition:
    box-shadow 0.2s var(--ease),
    transform 0.2s var(--ease);
}
.detail-section::before {
  content: '';
  position: absolute;
  top: 0;
  left: 18px;
  right: 18px;
  height: 3px;
  border-radius: 0 0 3px 3px;
  background: linear-gradient(90deg, var(--brand-500) 0%, #6b84ff 100%);
}
.detail-section:hover {
  box-shadow: var(--shadow-2);
  transform: translateY(-2px);
}
.meta {
  color: #909399;
  font-size: 13px;
  margin-bottom: 6px;
}
.article-text {
  white-space: pre-wrap;
  max-height: 420px;
  overflow-y: auto;
}
.summary-text {
  line-height: 1.7;
}
.copy-text {
  line-height: 1.8;
  white-space: pre-wrap;
}
.key-points li {
  margin-bottom: 4px;
}
.actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 12px;
}
.section-actions {
  display: flex;
  gap: 8px;
  margin-bottom: 8px;
}
.key-point-editor {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.key-point-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.preview-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
}
.preview-tip {
  color: var(--text-3);
  font-size: 12px;
}
.preview-result {
  background: var(--bg-page);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  padding: 16px;
}
</style>
