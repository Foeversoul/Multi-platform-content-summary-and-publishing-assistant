import { beforeEach, describe, expect, it, vi } from 'vitest'

/** useConfirm 为模块级单例，测试前需 resetModules 保证各用例状态隔离 */
async function freshConfirm() {
  vi.resetModules()
  return await import('./useConfirm')
}

describe('useConfirm', () => {
  beforeEach(() => {
    vi.resetModules()
  })

  it('confirm 打开弹框并合并默认选项', async () => {
    const { useConfirm } = await freshConfirm()
    const { state, confirm } = useConfirm()
    void confirm({ message: '确定要删除吗？' })
    expect(state.visible).toBe(true)
    expect(state.options.title).toBe('操作确认')
    expect(state.options.message).toBe('确定要删除吗？')
    expect(state.options.confirmText).toBe('确认')
    expect(state.options.cancelText).toBe('取消')
    expect(state.options.type).toBe('warning')
  })

  it('自定义选项覆盖默认值', async () => {
    const { useConfirm } = await freshConfirm()
    const { state, confirm } = useConfirm()
    void confirm({ title: '永久删除', confirmText: '永久删除', cancelText: '再想想', type: 'danger' })
    expect(state.options.title).toBe('永久删除')
    expect(state.options.confirmText).toBe('永久删除')
    expect(state.options.cancelText).toBe('再想想')
    expect(state.options.type).toBe('danger')
  })

  it('确认后 Promise resolve true 并关闭', async () => {
    const { useConfirm } = await freshConfirm()
    const { state, confirm, settle } = useConfirm()
    const result = confirm()
    settle(true)
    await expect(result).resolves.toBe(true)
    expect(state.visible).toBe(false)
  })

  it('取消后 Promise resolve false', async () => {
    const { useConfirm } = await freshConfirm()
    const { state, confirm, settle } = useConfirm()
    const result = confirm()
    settle(false)
    await expect(result).resolves.toBe(false)
    expect(state.visible).toBe(false)
  })

  it('未打开时状态为关闭', async () => {
    const { useConfirm } = await freshConfirm()
    const { state } = useConfirm()
    expect(state.visible).toBe(false)
  })
})
