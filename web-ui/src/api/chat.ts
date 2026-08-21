/** AI 对话助手 API */
import request, { request as req } from '@/utils/request'

export interface ChatReply {
  reply: string
  source: string
  kind?: string
}

/** 发送消息并获取 AI 回复（可能为动作执行结果） */
export async function sendChatMessage(message: string) {
  return req<ChatReply>(request.post('/chat', { message }, { timeout: 30000 }))
}
