<script setup lang="ts">
import { ref } from 'vue'

const props = defineProps<{ text: string }>()
const copied = ref(false)

async function copy() {
  try {
    await navigator.clipboard.writeText(props.text)
  } catch {
    // 非安全上下文降级：textarea 兜底
    const ta = document.createElement('textarea')
    ta.value = props.text
    document.body.appendChild(ta)
    ta.select()
    document.execCommand('copy')
    ta.remove()
  }
  copied.value = true
  setTimeout(() => (copied.value = false), 1500)
}
</script>

<template>
  <el-button size="small" :type="copied ? 'success' : 'primary'" plain @click="copy">
    {{ copied ? '已复制' : '一键复制' }}
  </el-button>
</template>
