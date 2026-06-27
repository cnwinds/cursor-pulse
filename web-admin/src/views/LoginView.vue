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

      <el-button type="primary" size="large" class="full" :loading="dingLoading" @click="loginDingTalk">
        钉钉扫码登录
      </el-button>

      <el-divider>或灾备本地登录</el-divider>

      <el-form :model="form" @submit.prevent="loginPassword">
        <el-form-item label="钉钉 User ID">
          <el-input v-model="form.dingtalk_user_id" placeholder="与成员表 dingtalk_user_id 一致" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input v-model="form.password" type="password" show-password />
        </el-form-item>
        <el-button type="default" class="full" native-type="submit" :loading="pwdLoading">
          密码登录
        </el-button>
      </el-form>
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

const dingLoading = ref(false)
const pwdLoading = ref(false)
const form = reactive({ dingtalk_user_id: '', password: '' })

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
  pwdLoading.value = true
  try {
    const { data } = await client.post('/api/auth/login', form)
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
.full {
  width: 100%;
}
</style>
