<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import ConfirmDialog from '@/components/ConfirmDialog.vue'
import PromptDialog from '@/components/PromptDialog.vue'

const route = useRoute()
const navs = [
  { path: '/', label: '待审列表', sub: 'REVIEWS' },
  { path: '/scrape', label: '内容导入', sub: 'INGEST' },
  { path: '/status', label: '运行总览', sub: 'HEALTH' },
  { path: '/recycle', label: '回收站', sub: 'TRASH' },
  { path: '/failed', label: '死信管理', sub: 'DLQ' },
  { path: '/chat', label: 'AI 助手', sub: 'CHAT' },
]

const isMobile = ref(false)
const drawerVisible = ref(false)
function onResize() {
  isMobile.value = window.innerWidth < 900
}
onMounted(() => {
  onResize()
  window.addEventListener('resize', onResize)
})
onBeforeUnmount(() => window.removeEventListener('resize', onResize))

const pageTitle = () => (route.meta.title as string) || '控制台'
</script>

<template>
  <el-container class="app-shell">
    <el-aside v-if="!isMobile" width="232px" class="app-aside">
      <div class="app-logo">
        <span class="app-logo-mark" aria-hidden="true">文</span>
        <span class="app-logo-block">
          <span class="app-logo-name">内容发布助手</span>
          <span class="app-logo-sub">Multi-platform Console</span>
        </span>
      </div>

      <div class="aside-caption">工作台</div>
      <el-menu :default-active="route.path" router class="app-menu">
        <el-menu-item v-for="nav in navs" :key="nav.path" :index="nav.path">
          <span class="nav-item">
            <span class="nav-label">{{ nav.label }}</span>
            <span class="nav-sub">{{ nav.sub }}</span>
          </span>
        </el-menu-item>
      </el-menu>

      <div class="aside-engine">
        <span class="engine-dot" />
        <span>采集引擎在线</span>
      </div>
      <div class="app-aside-foot">v0.1.0 · OpenCLI 增强采集</div>
    </el-aside>

    <el-container class="app-body">
      <el-header v-if="isMobile" class="app-topbar mobile" height="52px">
        <button class="menu-toggle" aria-label="打开导航" @click="drawerVisible = true">
          <span /><span /><span />
        </button>
        <span class="topbar-title">{{ pageTitle() }}</span>
      </el-header>

      <el-header v-else class="app-topbar" height="60px">
        <div class="topbar-title">{{ pageTitle() }}</div>
        <div class="topbar-meta">
          <span class="meta-chip engine-chip"><span class="engine-dot" />采集引擎</span>
          <span class="topbar-route">{{ route.path }}</span>
        </div>
      </el-header>

      <el-main class="app-main">
        <router-view v-slot="{ Component }">
          <transition name="page-fade" mode="out-in">
            <component :is="Component" />
          </transition>
        </router-view>
      </el-main>
    </el-container>
  </el-container>

  <el-drawer v-model="drawerVisible" direction="ltr" size="240px" :with-header="false">
    <div class="app-drawer-body">
      <div class="app-logo">
        <span class="app-logo-mark" aria-hidden="true">文</span>
        <span class="app-logo-name">内容发布助手</span>
      </div>
      <div class="aside-caption">工作台</div>
      <el-menu :default-active="route.path" router class="app-menu" @select="drawerVisible = false">
        <el-menu-item v-for="nav in navs" :key="nav.path" :index="nav.path">{{ nav.label }}</el-menu-item>
      </el-menu>
    </div>
  </el-drawer>

  <ConfirmDialog />
  <PromptDialog />
</template>

<style scoped>
.app-shell {
  height: 100vh;
}
.app-aside {
  background: linear-gradient(180deg, #202942 0%, #161c31 100%);
  display: flex;
  flex-direction: column;
  border-right: 1px solid rgba(255, 255, 255, 0.06);
}
.app-logo {
  display: flex;
  align-items: center;
  gap: 12px;
  color: #fff;
  padding: 22px 18px 14px;
}
.app-logo-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  border-radius: 10px;
  background: linear-gradient(135deg, #6b84ff 0%, #4f6bf5 100%);
  box-shadow: 0 4px 14px rgba(79, 107, 245, 0.4);
  font-size: 16px;
  font-weight: 700;
  flex-shrink: 0;
}
.app-logo-block {
  display: flex;
  flex-direction: column;
  line-height: 1.2;
}
.app-logo-name {
  font-weight: 600;
  font-size: 15px;
}
.app-logo-sub {
  margin-top: 3px;
  font-size: 11px;
  letter-spacing: 0;
  color: #7c87a3;
}
.aside-caption {
  padding: 12px 20px 6px;
  font-size: 11px;
  font-weight: 600;
  color: #5c6780;
}
.app-menu {
  border-right: none;
  background: transparent;
  --el-menu-text-color: #a6aec4;
  --el-menu-hover-bg-color: rgba(255, 255, 255, 0.06);
  --el-menu-active-color: #ffffff;
  padding: 2px 12px;
}
.app-menu .el-menu-item {
  border-radius: 8px;
  margin-bottom: 4px;
  height: 46px;
  line-height: 46px;
}
.nav-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
}
.nav-label {
  font-size: 14px;
}
.nav-sub {
  font-size: 10px;
  color: #5f6a84;
}
.app-menu .el-menu-item.is-active {
  background: linear-gradient(90deg, rgba(79, 107, 245, 0.95) 0%, rgba(79, 107, 245, 0.72) 100%);
  box-shadow: 0 4px 12px rgba(79, 107, 245, 0.35);
}
.app-menu .el-menu-item.is-active .nav-sub {
  color: rgba(255, 255, 255, 0.75);
}
.aside-engine {
  margin: auto 16px 10px;
  padding: 9px 12px;
  border-radius: 8px;
  background: rgba(22, 200, 148, 0.1);
  color: #7fe0bb;
  font-size: 12px;
  display: flex;
  align-items: center;
  gap: 8px;
}
.engine-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #16c894;
  box-shadow: 0 0 0 3px rgba(22, 200, 148, 0.22);
  display: inline-block;
}
.app-aside-foot {
  padding: 12px 18px 16px;
  color: #525c74;
  font-size: 11px;
}
.app-body {
  min-width: 0;
}
.app-topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: var(--bg-page);
  border-bottom: 1px solid var(--border);
  padding: 0 24px;
}
.topbar-title {
  font-size: 18px;
  font-weight: 600;
  color: var(--text-1);
}
.topbar-meta {
  display: flex;
  align-items: center;
  gap: 12px;
}
.meta-chip {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  font-size: 12px;
  padding: 5px 10px;
  border-radius: 20px;
  color: var(--brand-600);
  background: var(--el-color-primary-light-9);
  border: 1px solid var(--el-color-primary-light-7);
}
.topbar-route {
  font-size: 12px;
  color: var(--text-3);
  font-family: ui-monospace, 'SFMono-Regular', Consolas, monospace;
}
.app-main {
  background: var(--bg-page);
  padding: 24px;
  overflow-y: auto;
}
.app-topbar.mobile {
  justify-content: flex-start;
  gap: 12px;
  border-bottom: 1px solid var(--border);
  background: var(--bg-page);
}
.app-topbar.mobile .topbar-title {
  font-size: 16px;
}
.menu-toggle {
  background: transparent;
  border: 1px solid var(--border);
  cursor: pointer;
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 4px;
  width: 34px;
  height: 34px;
  padding: 8px;
  border-radius: 6px;
  transition: background-color 0.2s ease;
}
.menu-toggle:hover {
  background: var(--el-fill-color-light);
}
.menu-toggle span {
  display: block;
  height: 2px;
  border-radius: 1px;
  background: var(--text-1);
}
.app-drawer-body {
  background: linear-gradient(180deg, #202942 0%, #161c31 100%);
  min-height: 100%;
  display: flex;
  flex-direction: column;
}
.app-drawer-body .app-logo {
  color: #fff;
}
@media (max-width: 900px) {
  .app-main {
    padding: 16px;
  }
}
</style>
