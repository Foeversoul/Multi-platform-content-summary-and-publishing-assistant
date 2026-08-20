<script setup lang="ts">
defineProps<{ text?: string; description?: string }>()
defineEmits<{ action: [] }>()
</script>

<template>
  <el-empty :description="description ?? '暂无数据'" class="empty-state">
    <template v-if="text">
      <el-button type="primary" plain @click="$emit('action')">{{ text }}</el-button>
    </template>
  </el-empty>
</template>
