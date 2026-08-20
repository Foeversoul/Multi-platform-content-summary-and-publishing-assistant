<script setup lang="ts">
import { ElEmpty } from 'element-plus'

export interface BaseColumn {
  /** 字段名 */
  prop: string
  /** 表头文本 */
  label: string
  /** 列宽 */
  width?: string | number
}

defineProps<{
  columns: BaseColumn[]
  data: Record<string, unknown>[]
  loading: boolean
  emptyText?: string
  /** U3：是否显示多选列（配合 selection 事件做批量操作） */
  selectable?: boolean
  /** 操作列宽度（默认 180，按钮较多时可调大） */
  actionWidth?: number
}>()
defineEmits<{ action: []; selection: [rows: Record<string, unknown>[]] }>()
</script>

<template>
  <el-table
    v-loading="loading"
    :data="data"
    stripe
    :row-key="(row: Record<string, unknown>) => String(row.id ?? row.copy_id ?? row.event_id)"
    @selection-change="(rows: Record<string, unknown>[]) => $emit('selection', rows)"
  >
    <el-table-column v-if="selectable" type="selection" width="48" />
    <el-table-column v-for="col in columns" :key="col.prop" :prop="col.prop" :label="col.label" :width="col.width">
      <template #default="{ row }">
        <slot :name="col.prop" :row="row">{{ row[col.prop] }}</slot>
      </template>
    </el-table-column>
    <el-table-column v-if="$slots.actions" label="操作" :width="actionWidth ?? 180" fixed="right">
      <template #default="{ row }">
        <slot name="actions" :row="row" />
      </template>
    </el-table-column>
    <template #empty>
      <el-empty :description="emptyText ?? '暂无数据'">
        <el-button type="primary" plain @click="$emit('action')">刷新</el-button>
      </el-empty>
    </template>
  </el-table>
</template>
