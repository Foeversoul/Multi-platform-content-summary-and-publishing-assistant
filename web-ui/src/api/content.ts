/** 手动内容上传（文本/Markdown/Word 文件） */
import type { ManualContentResult } from '@/types/api'
import request, { request as req } from '@/utils/request'

/** 文本/Markdown 内容直传 */
export async function submitManualContent(data: { title: string; content: string }) {
  return req<ManualContentResult>(request.post('/content/manual', data))
}

/** 文件上传（.txt / .md / .docx） */
export async function uploadManualFile(file: File) {
  const form = new FormData()
  form.append('file', file)
  return req<ManualContentResult>(request.post('/content/manual/file', form))
}
