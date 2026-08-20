/** 全局确认/输入弹框状态（配合 ConfirmDialog.vue / PromptDialog.vue，Promise 风格）。 */
import { reactive } from 'vue'

export type ConfirmType = 'info' | 'warning' | 'danger'

export interface ConfirmOptions {
  title?: string
  message?: string
  confirmText?: string
  cancelText?: string
  type?: ConfirmType
}

export interface PromptOptions extends ConfirmOptions {
  /** 输入框占位提示 */
  placeholder?: string
  /** 是否必填（空值不允许确认，默认 true） */
  required?: boolean
}

const CONFIRM_DEFAULTS = {
  title: '操作确认',
  message: '',
  confirmText: '确认',
  cancelText: '取消',
  type: 'warning' as ConfirmType,
}
const PROMPT_DEFAULTS = {
  title: '请输入',
  message: '',
  placeholder: '',
  required: true,
  confirmText: '确定',
  cancelText: '取消',
  type: 'info' as ConfirmType,
}

const state = reactive({
  visible: false,
  options: { ...CONFIRM_DEFAULTS },
})
const promptState = reactive({
  visible: false,
  value: '',
  options: { ...PROMPT_DEFAULTS },
})

let resolver: ((result: boolean) => void) | null = null
let promptResolver: ((result: string | null) => void) | null = null

export function useConfirm() {
  function confirm(options: ConfirmOptions = {}): Promise<boolean> {
    state.options = { ...CONFIRM_DEFAULTS, ...options }
    state.visible = true
    return new Promise<boolean>((resolve) => {
      resolver = resolve
    })
  }

  function settle(result: boolean): void {
    state.visible = false
    resolver?.(result)
    resolver = null
  }

  /** 打开输入弹框（如驳回理由），确认返回字符串，取消返回 null */
  function prompt(options: PromptOptions = {}): Promise<string | null> {
    promptState.options = { ...PROMPT_DEFAULTS, ...options }
    promptState.value = ''
    promptState.visible = true
    return new Promise((resolve) => {
      promptResolver = resolve
    })
  }

  function settlePrompt(result: string | null): void {
    promptState.visible = false
    promptResolver?.(result)
    promptResolver = null
  }

  return { state, promptState, confirm, settle, prompt, settlePrompt }
}
