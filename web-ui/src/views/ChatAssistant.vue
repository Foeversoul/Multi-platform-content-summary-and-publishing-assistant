<script setup lang="ts">
import { nextTick, onMounted, ref } from 'vue'
import { ChatDotRound, Delete, Promotion } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'

import { clearChatHistory, fetchChatHistory, sendChatMessage } from '@/api/chat'
import { ApiClientError } from '@/utils/request'

interface Msg {
  role: 'user' | 'assistant'
  text: string
  source?: string
  kind?: string
}

const WELCOME_TEXT =
  '你好！我是本项目的 AI 助手，既可以解答功能用法，也能直接帮你执行项目操作。试试下面的快捷指令，或直接输入你的需求。'

const messages = ref<Msg[]>([
  {
    role: 'assistant',
    text: WELCOME_TEXT,
  },
])
const input = ref('')
const sending = ref(false)
const listRef = ref<HTMLElement | null>(null)

const suggestions = [
  '这个项目能做什么？',
  '如何导入内容？',
  '支持哪些平台？',
  '帮我爬取一个链接',
  '列一下待审列表',
  '查历史记录',
  '发布所有待审',
  '当前待审数量',
]

async function loadHistory() {
  try {
    const { items } = await fetchChatHistory(50)
    if (!items.length) return
    messages.value = [
      ...messages.value,
      ...items.map((it) => ({
        role: it.role,
        text: it.text,
        source: it.role === 'assistant' ? 'memory' : undefined,
      })),
    ]
    await scrollToBottom()
  } catch {
    // 历史加载失败不阻断聊天入口
  }
}

onMounted(() => {
  void loadHistory()
})

async function scrollToBottom() {
  await nextTick()
  if (listRef.value) {
    listRef.value.scrollTop = listRef.value.scrollHeight
  }
}

async function send(text: string) {
  const msg = text.trim()
  if (!msg || sending.value) return
  input.value = ''
  sending.value = true
  messages.value.push({ role: 'user', text: msg })
  await scrollToBottom()
  try {
    const result = await sendChatMessage(msg)
    messages.value.push({ role: 'assistant', text: result.reply, source: result.source, kind: result.kind })
  } catch (err) {
    const tip = err instanceof ApiClientError ? err.message : '回复失败，请稍后重试'
    messages.value.push({ role: 'assistant', text: tip })
  } finally {
    sending.value = false
    await scrollToBottom()
  }
}

function onSubmit() {
  void send(input.value)
}

function onSuggestion(s: string) {
  void send(s)
}

async function onClearScreen() {
  messages.value = [{ role: 'assistant', text: WELCOME_TEXT }]
  try {
    const { cleared } = await clearChatHistory()
    ElMessage.success(cleared ? `已清屏，并清除 ${cleared} 条对话记忆` : '已清屏')
  } catch {
    // 记忆清除失败时至少本地清空显示
  }
  await scrollToBottom()
}
</script>

<template>
  <div class="chat-page">
    <div class="chat-toolbar">
      <el-button size="small" :icon="Delete" :disabled="sending" @click="onClearScreen">清屏</el-button>
    </div>
    <div class="chat-suggestions">
      <el-button
        v-for="s in suggestions"
        :key="s"
        size="small"
        round
        :disabled="sending"
        @click="onSuggestion(s)"
      >{{ s }}</el-button>
    </div>

    <div ref="listRef" class="chat-list">
      <div
        v-for="(msg, i) in messages"
        :key="i"
        class="chat-msg"
        :class="msg.role === 'user' ? 'is-user' : 'is-bot'"
      >
        <div class="msg-avatar">
          <el-icon v-if="msg.role === 'assistant'"><ChatDotRound /></el-icon>
          <span v-else>我</span>
        </div>
        <div class="msg-bubble">
          <p class="msg-text">{{ msg.text }}</p>
          <span v-if="msg.source === 'action'" class="msg-tag msg-tag-action">已执行</span>
          <span v-if="msg.source === 'fallback'" class="msg-tag">离线知识库</span>
          <span v-else-if="msg.source === 'llm'" class="msg-tag msg-tag-llm">AI</span>
        </div>
      </div>
      <div v-if="sending" class="chat-msg is-bot">
        <div class="msg-avatar"><el-icon><ChatDotRound /></el-icon></div>
        <div class="msg-bubble msg-typing">
          <span class="typing-dot" /><span class="typing-dot" /><span class="typing-dot" />
        </div>
      </div>
    </div>

    <div class="chat-input-bar">
      <el-input
        v-model="input"
        placeholder="输入你的问题…"
        :disabled="sending"
        @keyup.enter="onSubmit"
      >
        <template #append>
          <el-button :icon="Promotion" :loading="sending" @click="onSubmit" />
        </template>
      </el-input>
    </div>
  </div>
</template>

<style scoped>
.chat-page {
  display: flex;
  flex-direction: column;
  height: 100%;
  max-width: 800px;
  margin: 0 auto;
}
.chat-toolbar {
  display: flex;
  justify-content: flex-end;
  margin-bottom: 8px;
}
.chat-suggestions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 16px;
}
.chat-list {
  flex: 1;
  overflow-y: auto;
  padding: 0 0 16px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.chat-msg {
  display: flex;
  gap: 10px;
  align-items: flex-start;
}
.chat-msg.is-user {
  flex-direction: row-reverse;
}
.msg-avatar {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  font-size: 13px;
  background: var(--el-color-primary-light-8);
  color: var(--brand-600);
}
.msg-bubble {
  max-width: 70%;
  padding: 10px 14px;
  border-radius: 12px;
  background: var(--bg-card);
  border: 1px solid var(--border);
  box-shadow: var(--shadow-1);
}
.is-user .msg-bubble {
  background: var(--el-color-primary);
  color: #fff;
  border-color: transparent;
}
.msg-text {
  margin: 0;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
}
.msg-tag {
  display: inline-block;
  margin-top: 6px;
  font-size: 11px;
  color: var(--text-3);
}
.msg-tag-llm {
  color: var(--brand-500);
}
.msg-tag-action {
  color: var(--el-color-success);
}
.is-user .msg-text {
  color: #fff;
}
.msg-typing {
  display: flex;
  gap: 4px;
  align-items: center;
  padding: 14px 16px;
}
.typing-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--text-3);
  animation: typing 1.2s infinite ease-in-out;
}
.typing-dot:nth-child(2) {
  animation-delay: 0.2s;
}
.typing-dot:nth-child(3) {
  animation-delay: 0.4s;
}
@keyframes typing {
  0%, 60%, 100% { opacity: 0.3; transform: translateY(0); }
  30% { opacity: 1; transform: translateY(-4px); }
}
.chat-input-bar {
  flex-shrink: 0;
  padding-top: 12px;
  border-top: 1px solid var(--border);
}
</style>
