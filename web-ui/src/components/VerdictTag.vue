<script setup lang="ts">
import { computed } from 'vue'

import { verdictTag } from '@/utils/format'

const props = defineProps<{ verdict: string | null | undefined }>()

const label = computed(() => {
  if (props.verdict === 'pass') return '已发布'
  if (props.verdict === 'reject') return '已驳回'
  return '待审核'
})
</script>

<template>
  <el-tag :type="verdictTag(verdict)" size="small">{{ label }}</el-tag>
</template>
