<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'

import { useUserStore } from '@/stores/user'

const router = useRouter()
const user = useUserStore()
const username = ref('')
const password = ref('')

function onLogin() {
  if (!username.value.trim() || !password.value.trim()) {
    ElMessage.warning('请输入用户名和密码')
    return
  }
  // SEC-01 首期无认证：本地模拟会话，保留接口结构供后续接入
  user.setSession('mock-token', username.value.trim())
  ElMessage.success('登录成功（模拟会话）')
  void router.push('/')
}
</script>

<template>
  <div class="login-wrap">
    <div class="login-card">
      <div class="login-brand">
        <span class="login-logo" aria-hidden="true">文</span>
        <h3>内容发布助手</h3>
        <p>多平台内容总结与发布 · 审核工作台</p>
      </div>
      <el-form label-position="top" @submit.prevent>
        <el-form-item label="用户名">
          <el-input v-model="username" placeholder="请输入用户名" size="large" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input v-model="password" type="password" placeholder="请输入密码" size="large" show-password />
        </el-form-item>
        <el-button type="primary" size="large" class="login-btn" @click="onLogin">登录</el-button>
      </el-form>
    </div>
  </div>
</template>

<style scoped>
.login-wrap {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(160deg, #202942 0%, #161c31 100%);
  padding: 24px;
}
.login-card {
  width: 400px;
  background: var(--bg-card);
  border-radius: var(--radius-lg);
  padding: 34px 30px 30px;
  box-shadow: var(--shadow-2);
}
.login-brand {
  text-align: center;
  margin-bottom: 26px;
}
.login-logo {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 48px;
  height: 48px;
  border-radius: 12px;
  background: linear-gradient(135deg, #6b84ff 0%, #4f6bf5 100%);
  box-shadow: 0 4px 14px rgba(79, 107, 245, 0.4);
  color: #fff;
  font-size: 22px;
  font-weight: 700;
  margin-bottom: 14px;
}
.login-brand h3 {
  margin: 0 0 6px;
  font-size: 19px;
}
.login-brand p {
  margin: 0;
  color: var(--text-3);
  font-size: 13px;
}
.login-btn {
  width: 100%;
  margin-top: 8px;
}
</style>
