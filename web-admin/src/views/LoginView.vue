<template>
  <div class="login-page">
    <el-card class="login-card" shadow="hover">
      <template #header>
        <div class="card-header">
          <span class="logo">脉</span>
          <div>
            <h2>小脉管理后台</h2>
            <p>Cursor Pulse · 团队用量协调</p>
          </div>
        </div>
      </template>

      <div class="login-tabs">
        <button
          type="button"
          class="login-tab"
          :class="{ active: activeTab === 'admin' }"
          @click="activeTab = 'admin'"
        >
          超管登录
        </button>
        <button
          type="button"
          class="login-tab"
          :class="{ active: activeTab === 'dingtalk' }"
          @click="activeTab = 'dingtalk'"
        >
          钉钉扫码
        </button>
      </div>

      <form v-if="activeTab === 'admin'" class="login-form" @submit.prevent="loginPassword">
        <p class="admin-hint">超管账号：<strong>admin</strong></p>
        <el-input
          v-model="form.password"
          type="password"
          placeholder="密码"
          size="large"
          show-password
        />
        <el-button type="primary" size="large" class="full" native-type="submit" :loading="pwdLoading">
          登录
        </el-button>
      </form>

      <div v-else class="dingtalk-panel">
        <p class="dingtalk-hint">使用钉钉 App 扫码，首次登录需超级管理员审批</p>
        <el-button type="primary" size="large" class="full" :loading="dingLoading" @click="loginDingTalk">
          打开钉钉扫码
        </el-button>
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import client from '@/api/client'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()

const activeTab = ref<'admin' | 'dingtalk'>('admin')
const dingLoading = ref(false)
const pwdLoading = ref(false)
const form = reactive({ password: '' })

async function loginDingTalk() {
  dingLoading.value = true
  try {
    const { data } = await client.get('/api/auth/dingtalk/login-url')
    sessionStorage.setItem('oauth_state', data.state)
    sessionStorage.setItem('oauth_redirect', (route.query.redirect as string) || '/')
    window.location.href = data.url
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '获取钉钉登录地址失败')
  } finally {
    dingLoading.value = false
  }
}

async function loginPassword() {
  if (!form.password) {
    ElMessage.warning('请输入密码')
    return
  }
  pwdLoading.value = true
  try {
    const { data } = await client.post('/api/auth/login', { username: 'admin', password: form.password })
    auth.setSession(data.access_token, data.user)
    const redirect = (route.query.redirect as string) || '/'
    router.push(redirect)
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '登录失败')
  } finally {
    pwdLoading.value = false
  }
}
</script>

<style scoped>
.login-page {
  min-height: 100vh;
  display: grid;
  place-items: center;
  background: linear-gradient(160deg, #0f172a 0%, #1e293b 40%, #f8fafc 40%);
}
.login-card {
  width: min(420px, 92vw);
}
.card-header {
  display: flex;
  gap: 12px;
  align-items: center;
}
.card-header h2 {
  margin: 0;
  font-size: 1.25rem;
}
.card-header p {
  margin: 4px 0 0;
  color: #64748b;
  font-size: 0.875rem;
}
.logo {
  width: 48px;
  height: 48px;
  border-radius: 14px;
  background: linear-gradient(135deg, #38bdf8, #6366f1);
  color: #fff;
  display: grid;
  place-items: center;
  font-weight: 700;
  font-size: 1.25rem;
}
.login-tabs {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
  margin-bottom: 20px;
}
.login-tab {
  padding: 10px 12px;
  border: 1px solid #dcdfe6;
  border-radius: 8px;
  background: #fff;
  color: #606266;
  font-size: 14px;
  cursor: pointer;
  transition: border-color 0.15s, color 0.15s;
}
.login-tab:hover {
  border-color: #409eff;
  color: #409eff;
}
.login-tab.active {
  border-color: #409eff;
  color: #409eff;
  font-weight: 600;
}
.login-form {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.admin-hint {
  margin: 0;
  font-size: 13px;
  color: #64748b;
}
.dingtalk-panel {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.dingtalk-hint {
  margin: 0;
  font-size: 13px;
  color: #64748b;
  text-align: center;
  line-height: 1.5;
}
.full {
  width: 100%;
}
</style>
