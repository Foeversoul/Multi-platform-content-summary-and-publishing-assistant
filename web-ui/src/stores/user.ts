/** 会话状态（PRD 4.3 useUserStore，首期无认证，保留结构） */
import { defineStore } from 'pinia'
import { ref } from 'vue'

export const useUserStore = defineStore('user', () => {
  const token = ref<string | null>(null)
  const username = ref<string>('')

  function setSession(nextToken: string | null, nextUsername: string) {
    token.value = nextToken
    username.value = nextUsername
  }

  function logout() {
    token.value = null
    username.value = ''
  }

  return { token, username, setSession, logout }
})
