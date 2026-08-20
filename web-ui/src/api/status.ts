/** IF-05 运行状态接口 */
import type { SystemStatus } from '@/types/api'
import request, { request as req } from '@/utils/request'

export async function fetchStatus() {
  return req<SystemStatus>(request.get('/status'))
}
