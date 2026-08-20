/** 路由定义与守卫（PRD 4.2 页面路由清单；History 模式） */
import { createRouter, createWebHistory } from 'vue-router'

import { useUserStore } from '@/stores/user'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'review-list', component: () => import('@/views/ReviewList.vue'), meta: { title: '待审列表' } },
    { path: '/reviews/:copyId(\\d+)', name: 'review-detail', component: () => import('@/views/ReviewDetail.vue'), meta: { title: '详情预览' } },
    { path: '/scrape', name: 'scrape-console', component: () => import('@/views/ScrapeConsole.vue'), meta: { title: '爬取控制台' } },
    { path: '/recycle', name: 'recycle-bin', component: () => import('@/views/RecycleBin.vue'), meta: { title: '回收站' } },
    { path: '/status', name: 'status-board', component: () => import('@/views/StatusBoard.vue'), meta: { title: '运行状态' } },
    { path: '/failed', name: 'failed-list', component: () => import('@/views/FailedList.vue'), meta: { title: '死信管理' } },
    { path: '/chat', name: 'chat-assistant', component: () => import('@/views/ChatAssistant.vue'), meta: { title: 'AI 助手' } },
    { path: '/login', name: 'login', component: () => import('@/views/Login.vue'), meta: { title: '登录' } },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})

router.beforeEach((to) => {
  document.title = `${to.meta.title ?? ''} · 多平台内容总结与发布助手`
  // 预留登录守卫：SEC-01 首期无认证，直接放行
  const user = useUserStore()
  if (to.name !== 'login' && user.token === '') return true
  return true
})

export default router
