<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage } from 'element-plus'

const emit = defineEmits<{ submit: [urls: string[]] }>()
const text = ref('')
/** 单条输入 + 批量粘贴 + .txt 文件导入（FR-20） */
const MAX_BATCH = 1000

/** 解析文本为 URL 列表：空行忽略、前后空白裁剪（BR-20-03） */
function parseUrls(raw: string): string[] {
  return raw
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
}

async function onFileChange(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  try {
    text.value = await file.text()
  } catch {
    ElMessage.error('文件读取失败：编码异常或非文本文件')
  } finally {
    input.value = ''
  }
}

function handleSubmit() {
  const urls = parseUrls(text.value)
  if (urls.length === 0) {
    ElMessage.warning('请至少输入一个 URL')
    return
  }
  if (urls.length > MAX_BATCH) {
    ElMessage.error(`单批 URL 数量不能超过 ${MAX_BATCH} 条，当前 ${urls.length} 条（BR-20-01）`)
    return
  }
  emit('submit', urls)
}
</script>

<template>
  <div class="url-uploader">
    <el-input
      v-model="text"
      type="textarea"
      :rows="8"
      placeholder="每行一个 URL，支持 http/https 协议，例如：&#10;https://example.com/article/1"
    />
    <div class="url-actions">
      <el-upload :show-file-list="false" :auto-upload="false" accept=".txt" :on-change="onFileChange">
        <el-button plain>导入 .txt 文件</el-button>
      </el-upload>
      <el-button type="primary" @click="handleSubmit">创建爬取任务</el-button>
      <el-button @click="text = ''">清空</el-button>
    </div>
  </div>
</template>

<style scoped>
.url-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 12px;
}
</style>
