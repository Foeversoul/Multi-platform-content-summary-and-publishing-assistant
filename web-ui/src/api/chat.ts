/** AI 对话助手 API */
import request, { request as req } from '@/utils/request'

export interface ChatReply {
  reply: string
  source: string
  kind?: string
}

export interface ChatHistoryItem {
  id: number
  role: 'user' | 'assistant'
  text: string
  created_at: string
}

/** 发送消息并获取 AI 回复（可能为动作执行结果） */
export async function sendChatMessage(message: string) {
  return req<ChatReply>(request.post('/chat', { message }, { timeout: 30000 }))
}

/** 查询最近 24 小时的对话历史 */
export async function fetchChatHistory(limit = 50) {
  return req<{ items: ChatHistoryItem[]; total: number }>(request.get('/chat/history', { params: { limit } }))
}

/** 清空对话记忆 */
export async function clearChatHistory() {
  return req<{ cleared: number }>(request.post('/chat/history/clear'))
}
