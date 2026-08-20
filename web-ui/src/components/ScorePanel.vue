<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ scores: Record<string, number> }>()

/** 中文标签映射 */
const labels: Record<string, string> = {
  style_score: '风格',
  clarity_score: '清晰',
  compliance_score: '合规',
  length_ok: '字数',
  overall_score: '综合',
}

const items = computed(() =>
  Object.entries(props.scores)
    .filter(([key]) => typeof props.scores[key] === 'number' && key !== 'top_sentence_scores')
    .map(([key, value]) => ({ key, label: labels[key] ?? key, value: Math.round(value) })),
)
</script>

<template>
  <div class="score-panel">
    <div v-for="item in items" :key="item.key" class="score-item">
      <span class="score-label">{{ item.label }}</span>
      <el-progress :percentage="item.value" :stroke-width="10" :format="() => String(item.value)" />
    </div>
  </div>
</template>

<style scoped>
.score-item {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 8px;
}
.score-label {
  width: 48px;
  flex-shrink: 0;
  color: #606266;
  font-size: 13px;
}
</style>
