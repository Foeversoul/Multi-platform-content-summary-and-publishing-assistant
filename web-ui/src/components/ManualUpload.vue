<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Close, Document, UploadFilled } from '@element-plus/icons-vue'

import { submitManualContent, uploadManualFile } from '@/api/content'
import { useConfirm } from '@/composables/useConfirm'

const router = useRouter()
const { confirm } = useConfirm()
const title = ref('')
const content = ref('')
const submitting = ref(false)
const pickedFile = ref<File | null>(null)
const mode = ref<'text' | 'file'>('text')

const canSubmit = computed(() =>
  mode.value === 'text' ? content.value.trim().length > 0 : pickedFile.value !== null,
)

const fileSizeText = computed(() => {
  if (!pickedFile.value) return ''
  const kb = pickedFile.value.size / 1024
  return kb > 1024 ? `${(kb / 1024).toFixed(1)} MB` : `${kb.toFixed(0)} KB`
})

async function handleSubmit() {
  if (submitting.value || !canSubmit.value) return
  submitting.value = true
  try {
    let copyCount = 0
    if (mode.value === 'text') {
      const result = await submitManualContent({ title: title.value.trim(), content: content.value })
      copyCount = result.copy_ids.length
    } else if (pickedFile.value) {
      const result = await uploadManualFile(pickedFile.value)
      copyCount = result.copy_ids.length
    }
    const go = await confirm({
      title: '上传成功',
      message: `已生成 ${copyCount} 个平台文案并进入待审列表。`,
      confirmText: '查看待审列表',
      cancelText: '继续上传',
      type: 'info',
    })
    if (go) {
      router.push('/')
    } else {
      resetForm()
    }
  } catch {
    // 统一错误提示
  } finally {
    submitting.value = false
  }
}

function onFileChange(file: { raw: File; name: string }) {
  pickedFile.value = file.raw
}

function removeFile() {
  pickedFile.value = null
}

function resetForm() {
  title.value = ''
  content.value = ''
  pickedFile.value = null
}
</script>

<template>
  <div class="manual-upload">
    <el-radio-group v-model="mode" class="mode-switch">
      <el-radio-button value="text">粘贴文本</el-radio-button>
      <el-radio-button value="file">上传文件</el-radio-button>
    </el-radio-group>

    <el-form label-position="top" class="manual-form">
      <el-form-item label="标题">
        <el-input
          v-model="title"
          maxlength="200"
          show-word-limit
          :placeholder="mode === 'text' ? '留空则取内容首行' : '留空则取文件名'"
        />
      </el-form-item>

      <template v-if="mode === 'text'">
        <el-form-item label="内容">
          <el-input
            v-model="content"
            type="textarea"
            :rows="10"
            show-word-limit
            :maxlength="100000"
            placeholder="粘贴文章正文，支持 Markdown"
          />
        </el-form-item>
      </template>

      <template v-else>
        <el-form-item label="文件">
          <div class="file-area">
            <el-upload
              accept=".txt,.md,.docx"
              :auto-upload="false"
              :limit="1"
              :show-file-list="false"
              :on-change="onFileChange"
            >
              <el-button plain :icon="UploadFilled">选择文件</el-button>
            </el-upload>
            <div v-if="pickedFile" class="file-tag">
              <el-icon class="file-icon"><Document /></el-icon>
              <span class="file-name" :title="pickedFile.name">{{ pickedFile.name }}</span>
              <span class="file-size">{{ fileSizeText }}</span>
              <el-button text size="small" :icon="Close" @click="removeFile" />
            </div>
            <span v-else class="file-hint">支持 .txt / .md / .docx，不超过 2MB</span>
          </div>
        </el-form-item>
      </template>
    </el-form>

    <div class="manual-actions">
      <el-button type="primary" :loading="submitting" :disabled="!canSubmit" @click="handleSubmit">
        {{ mode === 'text' ? '提交并生成' : '上传并生成' }}
      </el-button>
      <el-button @click="resetForm" :disabled="submitting">清空</el-button>
    </div>
  </div>
</template>

<style scoped>
.mode-switch {
  margin-bottom: 16px;
}
.manual-form {
  margin-bottom: 4px;
}
.manual-actions {
  display: flex;
  gap: 10px;
  margin-top: 4px;
}
.file-area {
  display: flex;
  align-items: center;
  gap: 8px;
}
.file-tag {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 4px 8px 4px 12px;
  background: var(--el-fill-color-light);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
}
.file-icon {
  color: var(--text-3);
  font-size: 16px;
  flex-shrink: 0;
}
.file-name {
  font-size: 13px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 200px;
}
.file-size {
  color: var(--text-3);
  font-size: 12px;
  white-space: nowrap;
}
.file-hint {
  color: var(--text-3);
  font-size: 12px;
}
</style>
