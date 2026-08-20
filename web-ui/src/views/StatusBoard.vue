<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted } from 'vue'

import { useStatusStore } from '@/stores/status'
import { formatNumber, platformLabel } from '@/utils/format'

const store = useStatusStore()

const pipelineSteps = [
  { key: 'crawl.requested', label: '采集', hint: 'CRAWL' },
  { key: 'article.crawled', label: '入库', hint: 'PERSIST' },
  { key: 'summary.generated', label: '摘要', hint: 'SUMMARIZE' },
  { key: 'copy.adapted', label: '文案', hint: 'ADAPT' },
  { key: 'review.passed', label: '审核', hint: 'REVIEW' },
]

const articlesTotal = computed(() => Object.values(store.data?.article_counts ?? {}).reduce((a, b) => a + b, 0))
const failedTotal = computed(() => {
  const fc = store.data?.failed_counts
  return (fc?.events ?? 0) + (fc?.articles ?? 0)
})

const platforms = computed(() =>
  Object.entries(store.data?.copy_by_platform ?? {})
    .map(([platform, count]) => ({ platform, label: platformLabel(platform), count }))
    .sort((a, b) => b.count - a.count),
)

const articleStatus = computed(() =>
  Object.entries(store.data?.article_counts ?? {}).map(([status, count]) => ({ status, count })),
)
const copyStatus = computed(() =>
  Object.entries(store.data?.copy_counts ?? {}).map(([status, count]) => ({ status, count })),
)
const reviewVerdicts = computed(() =>
  Object.entries(store.data?.review_verdicts ?? {}).map(([verdict, count]) => ({ verdict, count })),
)
const scrapeJobs = computed(() =>
  Object.entries(store.data?.scrape_jobs ?? {}).map(([status, count]) => ({ status, count })),
)
const eventStatus = computed(() =>
  Object.entries(store.data?.event_counts ?? {}).map(([status, count]) => ({ status, count })),
)

const articleStatusLabel = (s: string) =>
  ({ reviewed: '已审核', summarized: '已摘要', dead_letter: '死信' } as Record<string, string>)[s] ?? s
const copyStatusLabel = (s: string) =>
  ({ adapted: '已适配', reviewed: '已审核' } as Record<string, string>)[s] ?? s
const verdictLabel = (s: string) =>
  ({ pass: '通过', pending: '待审', reject: '驳回' } as Record<string, string>)[s] ?? s
const jobLabel = (s: string) =>
  ({ succeeded: '成功', failed: '失败', pending: '待处理', partial: '部分成功', validating: '校验中', crawling: '爬取中' } as Record<
    string,
    string
  >)[s] ?? s
const eventStatusLabel = (s: string) =>
  ({ processed: '已处理', dead: '死信', queued: '排队', discarded: '已丢弃' } as Record<string, string>)[s] ?? s

onMounted(() => store.startPolling())
onBeforeUnmount(() => store.stopPolling())
</script>

<template>
  <div class="dashboard">
    <section class="page-card hero">
      <div class="hero-main">
        <h3 class="hero-title">运行总览</h3>
        <p class="hero-sub">采集 → 摘要 → 适配 → 审核 → 发布的实时运行数据</p>
      </div>
      <div class="hero-meta">
        <span class="engine-badge"><span class="engine-dot" />采集引擎在线</span>
        <span class="refresh-hint">每 30 秒自动刷新 · {{ store.lastUpdatedAt || '等待首次拉取…' }}</span>
      </div>
    </section>

    <el-row :gutter="16" class="stat-row">
      <el-col :span="6" :xs="12">
        <dl class="stat-card">
          <dt>待审核文案</dt>
          <dd>{{ formatNumber(store.data?.pending_reviews) }}</dd>
          <span class="stat-foot">PENDING REVIEW</span>
        </dl>
      </el-col>
      <el-col :span="6" :xs="12">
        <dl class="stat-card">
          <dt>已发布文案</dt>
          <dd>{{ formatNumber(store.data?.publish_count) }}</dd>
          <span class="stat-foot">PUBLISHED</span>
        </dl>
      </el-col>
      <el-col :span="6" :xs="12">
        <dl class="stat-card">
          <dt>文章总数</dt>
          <dd>{{ formatNumber(articlesTotal) }}</dd>
          <span class="stat-foot">ARTICLES · 摘要 {{ formatNumber(store.data?.summary_count) }}</span>
        </dl>
      </el-col>
      <el-col :span="6" :xs="12">
        <dl class="stat-card danger">
          <dt>死信事件</dt>
          <dd>{{ formatNumber(failedTotal) }}</dd>
          <span class="stat-foot">DLQ · EVENTS {{ formatNumber(store.data?.failed_counts?.events) }}</span>
        </dl>
      </el-col>
    </el-row>

    <section class="page-card pipeline">
      <div class="section-head">
        <h4>流水线概览</h4>
        <span class="section-count">按事件计数，反映实际业务流程</span>
      </div>
      <div class="pipeline-track">
        <div v-for="(step, i) in pipelineSteps" :key="step.key" class="pipeline-step">
          <span class="step-index">{{ String(i + 1).padStart(2, '0') }}</span>
          <span class="step-body">
            <span class="step-label">{{ step.label }}</span>
            <span class="step-count">{{ formatNumber(store.data?.event_types?.[step.key]) }}</span>
            <span class="step-hint">{{ step.hint }}</span>
          </span>
          <span v-if="i < pipelineSteps.length - 1" class="step-arrow" aria-hidden="true">→</span>
        </div>
      </div>
    </section>

    <el-row :gutter="16">
      <el-col :span="12" :xs="24">
        <section class="page-card stat-section">
          <div class="section-head">
            <h4>平台文案分布</h4>
            <span class="section-count">PLATFORM</span>
          </div>
          <el-table :data="platforms" size="small">
            <el-table-column label="平台">
              <template #default="{ row }">{{ row.label }}</template>
            </el-table-column>
            <el-table-column prop="count" label="数量" width="110" align="right" />
          </el-table>
        </section>
      </el-col>
      <el-col :span="12" :xs="24">
        <section class="page-card stat-section">
          <div class="section-head">
            <h4>文章状态分布</h4>
            <span class="section-count">STATUS</span>
          </div>
          <el-table :data="articleStatus" size="small">
            <el-table-column label="状态">
              <template #default="{ row }">{{ articleStatusLabel(row.status) }}</template>
            </el-table-column>
            <el-table-column prop="count" label="数量" width="110" align="right" />
          </el-table>
        </section>
      </el-col>
    </el-row>

    <el-row :gutter="16" class="stat-row bottom">
      <el-col :span="12" :xs="24">
        <section class="page-card stat-section">
          <div class="section-head">
            <h4>审核结果分布</h4>
            <span class="section-count">REVIEW</span>
          </div>
          <el-table :data="reviewVerdicts" size="small">
            <el-table-column label="结果">
              <template #default="{ row }">{{ verdictLabel(row.verdict) }}</template>
            </el-table-column>
            <el-table-column prop="count" label="数量" width="110" align="right" />
          </el-table>
        </section>
      </el-col>
      <el-col :span="12" :xs="24">
        <section class="page-card stat-section">
          <div class="section-head">
            <h4>文案处理状态</h4>
            <span class="section-count">COPY STATUS</span>
          </div>
          <el-table :data="copyStatus" size="small">
            <el-table-column label="状态">
              <template #default="{ row }">{{ copyStatusLabel(row.status) }}</template>
            </el-table-column>
            <el-table-column prop="count" label="数量" width="110" align="right" />
          </el-table>
        </section>
      </el-col>
    </el-row>

    <el-row :gutter="16" class="stat-row bottom">
      <el-col :span="12" :xs="24">
        <section class="page-card stat-section">
          <div class="section-head">
            <h4>爬取任务状态</h4>
            <span class="section-count">SCRAPE</span>
          </div>
          <el-table :data="scrapeJobs" size="small">
            <el-table-column label="状态">
              <template #default="{ row }">{{ jobLabel(row.status) }}</template>
            </el-table-column>
            <el-table-column prop="count" label="数量" width="110" align="right" />
          </el-table>
        </section>
      </el-col>
      <el-col :span="12" :xs="24">
        <section class="page-card stat-section">
          <div class="section-head">
            <h4>事件处理状态</h4>
            <span class="section-count">EVENTS</span>
          </div>
          <el-table :data="eventStatus" size="small">
            <el-table-column label="状态">
              <template #default="{ row }">{{ eventStatusLabel(row.status) }}</template>
            </el-table-column>
            <el-table-column prop="count" label="数量" width="110" align="right" />
          </el-table>
        </section>
      </el-col>
    </el-row>
  </div>
</template>

<style scoped>
.dashboard {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.hero {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 20px 24px;
}
.hero-title {
  margin: 0;
  font-size: 20px;
}
.hero-sub {
  margin: 4px 0 0;
  color: var(--text-3);
  font-size: 13px;
}
.hero-meta {
  display: flex;
  align-items: center;
  gap: 14px;
}
.engine-badge {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 6px 12px;
  border-radius: 20px;
  font-size: 12px;
  color: var(--ok);
  background: rgba(22, 200, 148, 0.1);
  border: 1px solid rgba(22, 200, 148, 0.28);
}
.engine-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--ok);
  display: inline-block;
}
.refresh-hint {
  color: var(--text-3);
  font-size: 12px;
}
.stat-row {
  margin-bottom: 0;
}
.stat-card {
  margin: 0;
  padding: 16px 18px;
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-1);
}
.stat-card dt {
  color: var(--text-3);
  font-size: 13px;
}
.stat-card dd {
  margin: 6px 0 8px;
  font-size: 28px;
  font-weight: 700;
  color: var(--text-1);
}
.stat-card .stat-foot {
  font-size: 10px;
  color: var(--text-4);
  letter-spacing: 0;
  font-family: ui-monospace, 'SFMono-Regular', Consolas, monospace;
}
.stat-card.danger dd {
  color: var(--el-color-danger);
}
.pipeline {
  padding: 18px 24px;
}
.section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 14px;
}
.section-head h4 {
  margin: 0;
}
.section-count {
  font-size: 11px;
  color: var(--text-3);
  font-family: ui-monospace, 'SFMono-Regular', Consolas, monospace;
}
.pipeline-track {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}
.pipeline-step {
  display: flex;
  align-items: center;
  flex: 1 1 150px;
  background: var(--el-fill-color-light);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  padding: 12px;
  gap: 12px;
  min-width: 150px;
}
.step-index {
  font-size: 12px;
  color: var(--brand-500);
  font-weight: 700;
  font-family: ui-monospace, Consolas, monospace;
}
.step-body {
  display: flex;
  flex-direction: column;
  line-height: 1.3;
}
.step-label {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-1);
}
.step-count {
  font-size: 22px;
  font-weight: 700;
  color: var(--text-1);
}
.step-hint {
  font-size: 10px;
  color: var(--text-4);
  letter-spacing: 0;
  font-family: ui-monospace, Consolas, monospace;
}
.step-arrow {
  color: var(--text-4);
  font-size: 16px;
  margin-left: auto;
}
.stat-section {
  padding: 18px 20px;
}
.bottom {
  margin-top: 0;
}
@media (max-width: 900px) {
  .hero {
    flex-direction: column;
    align-items: flex-start;
  }
}
</style>
