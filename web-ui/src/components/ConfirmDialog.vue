<script setup lang="ts">
/**
 * 全局二次确认弹框（挂载于 App.vue，配合 useConfirm() 编程式调用）。
 * - 半透明遮罩 + 居中卡片，响应式适配小屏
 * - Enter 确认 / Esc 取消 / 点击遮罩取消
 * - Tab 焦点循环（focus trap），关闭后焦点还原到触发元素
 * - ARIA：role="dialog" / aria-modal / aria-labelledby / aria-describedby
 */
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'

import { useConfirm } from '@/composables/useConfirm'

const { state, settle } = useConfirm()

const dialogRef = ref<HTMLElement | null>(null)
const cancelBtnRef = ref<HTMLElement | null>(null)
const titleId = 'confirm-dialog-title'
const descId = 'confirm-dialog-desc'

/** 打开前记录的触发元素与页面滚动状态，关闭后恢复 */
let restoreFocus: HTMLElement | null = null
let prevOverflow = ''

// 打开/关闭副作用：焦点与滚动锁定
watch(
  () => state.visible,
  async (visible) => {
    if (visible) {
      restoreFocus = document.activeElement as HTMLElement | null
      prevOverflow = document.body.style.overflow
      document.body.style.overflow = 'hidden'
      await nextTick()
      // 默认聚焦「取消」，防止误触 Enter 直接确认危险操作（勿再聚焦容器覆盖按钮焦点）
      cancelBtnRef.value?.focus()
    } else {
      document.body.style.overflow = prevOverflow
      const target = restoreFocus
      // 触发元素可能已被列表刷新移除（如确认删除后该行消失），仅在仍挂载时还原焦点
      if (target?.isConnected) {
        target.focus()
      }
      restoreFocus = null
    }
  },
)

onBeforeUnmount(() => {
  if (state.visible) {
    document.body.style.overflow = prevOverflow
    restoreFocus?.focus?.()
  }
})

/** 键盘操作：Esc 取消；Enter 确认（焦点在按钮上时交给按钮自身语义，避免重复触发）；Tab 焦点循环 */
function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') {
    event.preventDefault()
    settle(false)
  } else if (event.key === 'Enter') {
    const target = event.target as HTMLElement
    if (target.tagName === 'BUTTON' || target.tagName === 'TEXTAREA') return
    event.preventDefault()
    settle(true)
  } else if (event.key === 'Tab') {
    trapFocus(event)
  }
}

/** 焦点陷阱：Tab 循环限制在弹框内，避免焦点逃逸到背景页面 */
function trapFocus(event: KeyboardEvent) {
  const nodes = Array.from(
    dialogRef.value?.querySelectorAll<HTMLElement>('button, [href], input, [tabindex]:not([tabindex="-1"])') ?? [],
  )
  if (!nodes.length) return
  const first = nodes[0]
  const last = nodes[nodes.length - 1]
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault()
    last.focus()
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault()
    first.focus()
  }
}
</script>

<template>
  <Teleport to="body">
    <Transition name="confirm">
      <div v-if="state.visible" class="confirm-mask" @click.self="settle(false)">
        <div
          ref="dialogRef"
          class="confirm-dialog"
          role="dialog"
          aria-modal="true"
          :aria-labelledby="titleId"
          :aria-describedby="descId"
          tabindex="-1"
          @keydown="onKeydown"
        >
          <header class="confirm-header">
            <span class="confirm-icon" :class="`is-${state.options.type}`" aria-hidden="true">
              <svg v-if="state.options.type === 'danger'" viewBox="0 0 24 24" fill="none">
                <path d="M12 9v4m0 4h.01M10.3 3.9 1.9 18.5a2 2 0 0 0 1.7 3h16.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
              </svg>
              <svg v-else-if="state.options.type === 'info'" viewBox="0 0 24 24" fill="none">
                <circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="2" />
                <path d="M12 11v5m0-9h.01" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
              </svg>
              <svg v-else viewBox="0 0 24 24" fill="none">
                <path d="M12 9v4m0 4h.01M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18Z" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
              </svg>
            </span>
            <h3 :id="titleId" class="confirm-title">{{ state.options.title }}</h3>
          </header>

          <div :id="descId" class="confirm-body">
            <p class="confirm-message">{{ state.options.message }}</p>
          </div>

          <footer class="confirm-footer">
            <button ref="cancelBtnRef" type="button" class="confirm-btn" @click="settle(false)">
              {{ state.options.cancelText }}
            </button>
            <button
              type="button"
              class="confirm-btn confirm-btn--primary"
              :class="`is-${state.options.type}`"
              @click="settle(true)"
            >
              {{ state.options.confirmText }}
            </button>
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
  z-index: 3000;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
  background: rgba(16, 20, 32, 0.55);
  backdrop-filter: blur(2px);
}

.confirm-dialog {
  width: min(100%, 420px);
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
  flex-shrink: 0;
}
.confirm-icon svg {
  width: 22px;
  height: 22px;
}
.confirm-icon.is-danger {
  color: #f56c6c;
}
.confirm-icon.is-warning {
  color: #e6a23c;
}
.confirm-icon.is-info {
  color: var(--brand-500, #4f6bf5);
}

.confirm-title {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: var(--text-1, #1d2129);
}

.confirm-body {
  padding: 14px 20px 0;
}

.confirm-message {
  margin: 0;
  font-size: 14px;
  line-height: 1.7;
  color: var(--text-2, #4e5969);
  white-space: pre-wrap;
  word-break: break-word;
}

.confirm-footer {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  padding: 20px;
}

.confirm-btn {
  min-width: 76px;
  padding: 8px 16px;
  border: 1px solid var(--border, #e5e8ef);
  border-radius: 6px;
  background: var(--bg-card, #fff);
  color: var(--text-2, #4e5969);
  font-size: 14px;
  cursor: pointer;
  transition: all 0.18s var(--ease, cubic-bezier(0.4, 0, 0.2, 1));
}
.confirm-btn:hover {
  border-color: var(--brand-500, #4f6bf5);
  color: var(--brand-500, #4f6bf5);
}
.confirm-btn:active {
  transform: scale(0.97);
}
.confirm-btn:focus-visible {
  outline: 2px solid var(--brand-500, #4f6bf5);
  outline-offset: 2px;
}

.confirm-btn--primary {
  border: none;
  color: #fff;
}
.confirm-btn--primary.is-danger {
  background: #f56c6c;
}
.confirm-btn--primary.is-danger:hover {
  background: #f78989;
}
.confirm-btn--primary.is-warning {
  background: #e6a23c;
}
.confirm-btn--primary.is-warning:hover {
  background: #ebb563;
}
.confirm-btn--primary.is-info {
  background: var(--brand-500, #4f6bf5);
}
.confirm-btn--primary.is-info:hover {
  background: var(--brand-600, #3e5ae0);
}

/* 动画：遮罩淡入淡出 + 弹框缩放渐显 */
.confirm-enter-active,
.confirm-leave-active {
  transition: opacity 0.22s var(--ease, cubic-bezier(0.4, 0, 0.2, 1));
}
.confirm-enter-active .confirm-dialog,
.confirm-leave-active .confirm-dialog {
  transition: transform 0.24s var(--ease, cubic-bezier(0.4, 0, 0.2, 1)), opacity 0.22s var(--ease, cubic-bezier(0.4, 0, 0.2, 1));
}
.confirm-enter-from,
.confirm-leave-to {
  opacity: 0;
}
.confirm-enter-from .confirm-dialog,
.confirm-leave-to .confirm-dialog {
  transform: translateY(10px) scale(0.97);
  opacity: 0;
}

@media (prefers-reduced-motion: reduce) {
  .confirm-enter-active,
  .confirm-leave-active,
  .confirm-enter-active .confirm-dialog,
  .confirm-leave-active .confirm-dialog {
    transition: none;
  }
}
</style>
