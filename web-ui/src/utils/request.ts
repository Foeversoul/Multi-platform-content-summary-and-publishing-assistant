/**
 * Axios 统一封装（PRD 4.5）：baseURL=/api，响应拦截器解包 {code, message, data}，
 * 业务失败统一抛出带中文提示的错误。
 */
import axios, { AxiosError } from 'axios'
import { ElMessage } from 'element-plus'

const instance = axios.create({
  baseURL: '/api',
  timeout: 15000,
})

// 注入 API 鉴权凭证（SEC-01）：优先环境变量，其次本地登录存储
instance.interceptors.request.use((config) => {
  const token = import.meta.env.VITE_API_TOKEN || localStorage.getItem('api_token')
  if (token) {
    config.headers['X-API-Token'] = token
  }
  return config
})

instance.interceptors.response.use(
  (resp) => {
    const body = resp.data as { code: number; message: string; data?: unknown }
    if (body.code !== 0) {
      return Promise.reject(new ApiClientError(body.message || '请求失败', resp.status))
    }
    return body.data as never
  },
  (error: AxiosError<{ message?: string }>) => {
    const status = error.response?.status || 0
    if (status === 401) {
      // 登录为占位实现（P2），此处仅提示；待用户体系落地后跳转 /login
      ElMessage.error('未授权，请检查 API Token 配置')
    }
    const message =
      error.response?.data?.message || (error.code === 'ECONNABORTED' ? '请求超时，请稍后重试' : '网络异常，请检查后端服务')
    return Promise.reject(new ApiClientError(message, status))
  },
)

/** 前端统一业务错误：message 可直接展示给用户（SEC-05：不经 v-html 渲染） */
export class ApiClientError extends Error {
  constructor(
    message: string,
    public readonly status: number = 0,
  ) {
    super(message)
    this.name = 'ApiClientError'
  }
}

/** 发起请求并统一弹错（页面可传 silent 自行处理） */
export async function request<T>(promise: Promise<T>, opts: { silent?: boolean } = {}): Promise<T> {
  try {
    return await promise
  } catch (err) {
    if (!opts.silent && err instanceof Error) {
      ElMessage.error(err.message)
    }
    throw err
  }
}

export default instance
