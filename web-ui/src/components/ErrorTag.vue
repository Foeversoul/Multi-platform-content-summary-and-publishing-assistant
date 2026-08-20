<script setup lang="ts">
import { computed } from 'vue'

import type { ScrapeErrorCode } from '@/types/api'

const props = defineProps<{ code: ScrapeErrorCode | string | null }>()

const map: Record<string, { label: string; type: 'danger' | 'warning' | 'info' }> = {
  INVALID_URL_FORMAT: { label: 'URL 格式无效', type: 'warning' },
  UNSUPPORTED_PROTOCOL: { label: '不支持的协议', type: 'warning' },
  DNS_FAILED: { label: '域名解析失败', type: 'danger' },
  CONNECTION_REFUSED: { label: '无法连接', type: 'danger' },
  TIMEOUT: { label: '请求超时', type: 'warning' },
  SSL_ERROR: { label: 'SSL 校验失败', type: 'danger' },
  HTTP_403: { label: '访问被拒绝 403', type: 'danger' },
  HTTP_404: { label: '页面不存在 404', type: 'warning' },
  HTTP_429: { label: '请求受限 429', type: 'warning' },
  HTTP_5XX: { label: '服务器错误 5xx', type: 'danger' },
  ROBOTS_BLOCKED: { label: 'robots 禁止', type: 'warning' },
  EMPTY_CONTENT: { label: '正文为空', type: 'warning' },
  RENDER_UNSUPPORTED: { label: '动态渲染不支持', type: 'warning' },
  DUPLICATE: { label: '内容重复', type: 'info' },
}

const tag = computed(() => (props.code ? map[props.code] ?? { label: props.code, type: 'info' as const } : null))
</script>

<template>
  <el-tooltip v-if="code" :content="code" placement="top">
    <el-tag v-if="tag" :type="tag.type" size="small">{{ tag.label }}</el-tag>
  </el-tooltip>
  <span v-else>—</span>
</template>
