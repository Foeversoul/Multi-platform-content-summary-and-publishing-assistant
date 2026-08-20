<script setup lang="ts">
/**
 * 全局输入弹框（挂载于 App.vue，配合 useConfirm().prompt() 编程式调用）。
 * 用于驳回理由等需要必填输入的场景，样式与 ConfirmDialog 保持一致。
 */
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'

import { useConfirm } from '@/composables/useConfirm'

const { promptState, settlePrompt } = useConfirm()

const inputRef = ref<HTMLElement | null>(null)
const error = ref('')
const titleId = 'prompt-dialog-title'
const descId = 'prompt-dialog-desc'

let restoreFocus: HTMLElement | null = null
let prevOverflow = ''

watch(
  () => promptState.visible,
  async (visible) => {
    if (visible) {
      restoreFocus = document.activeElement as HTMLElement | null
      prevOverflow = document.body.style.overflow
      document.body.style.overflow = 'hidden'
      error.value = ''
      await nextTick()
      inputRef.value?.focus?.()
    } else {
      document.body.style.overflow = prevOverflow
      if (restoreFocus?.isConnected) restoreFocus.focus()
      restoreFocus = null
    }
  },
)

onBeforeUnmount(() => {
  if (promptState.visible) {
    document.body.style.overflow = prevOverflow
    restoreFocus?.focus?.()
  }
})

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') {
    event.preventDefault()
    settlePrompt(null)
  }
}

function confirmAction() {
  const value = promptState.value.trim()
  if (promptState.options.required && !value) {
    error.value = '该项为必填，请填写后再确认'
    return
  }
  settlePrompt(value)
}
</script>

<template>
  <Teleport to="body">
    <Transition name="confirm">
      <div v-if="promptState.visible" class="confirm-mask" @click.self="settlePrompt(null)">
        <div
          class="prompt-dialog"
          role="dialog"
          aria-modal="true"
          :aria-labelledby="titleId"
          :aria-describedby="descId"
          tabindex="-1"
          @keydown="onKeydown"
        >
          <header class="confirm-header">
            <span class="confirm-icon is-info" aria-hidden="true">
              <svg viewBox="0 0 24 24" fill="none">
                <path d="M12 9v4m0 4h.01M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18Z" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
              </svg>
            </span>
            <h3 :id="titleId" class="confirm-title">{{ promptState.options.title }}</h3>
          </header>

          <div :id="descId" class="prompt-body">
            <p v-if="promptState.options.message" class="prompt-message">{{ promptState.options.message }}</p>
            <el-input
              ref="inputRef"
              v-model="promptState.value"
              :placeholder="promptState.options.placeholder || '请输入'"
              clearable
              @keyup.enter="confirmAction"
            />
            <p v-if="error" class="prompt-error" role="alert">{{ error }}</p>
          </div>

          <footer class="confirm-footer">
            <el-button @click="settlePrompt(null)">{{ promptState.options.cancelText }}</el-button>
            <el-button type="primary" @click="confirmAction">{{ promptState.options.confirmText }}</el-button>
          </footer>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.confirm-mask {
  position: fixed;
  inset: 0;
  z-index: 3100;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
  background: rgba(16, 20, 32, 0.55);
  backdrop-filter: blur(2px);
}
.prompt-dialog {
  width: min(100%, 440px);
  max-height: calc(100vh - 48px);
  overflow-y: auto;
  background: var(--bg-card, #fff);
  border-radius: var(--radius-lg, 12px);
  box-shadow: var(--shadow-2, 0 6px 16px rgba(29, 33, 41, 0.08));
  outline: none;
}
.confirm-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 18px 20px 0;
}
.confirm-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  color: var(--brand-500, #4f6bf5);
  flex-shrink: 0;
}
.confirm-icon svg {
  width: 22px;
  height: 22px;
}
.confirm-title {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: var(--text-1, #1d2129);
}
.prompt-body {
  padding: 16px 20px 0;
}
.prompt-message {
  margin: 0 0 12px;
  font-size: 14px;
  line-height: 1.7;
  color: var(--text-2, #4e5969);
  white-space: pre-wrap;
  word-break: break-word;
}
.prompt-error {
  margin: 8px 0 0;
  font-size: 13px;
  color: var(--el-color-danger, #f56c6c);
}
.confirm-footer {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  padding: 20px;
}
</style>
