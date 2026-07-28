<template>
  <div class="login-page">
    <el-card class="login-card" shadow="hover">
      <template #header>
        <div class="card-header">
          <img class="logo" src="/logo.svg" alt="Cursor Pulse" />
          <div>
            <h2>小脉管理后台</h2>
            <p>Cursor Pulse · 团队用量协调</p>
          </div>
        </div>
      </template>

      <div v-if="loadingProviders" class="loading-hint">加载登录方式…</div>

      <template v-else>
        <div
          v-if="visibleTabs.length > 1"
          class="login-tabs"
          :style="{ gridTemplateColumns: `repeat(${visibleTabs.length}, 1fr)` }"
        >
          <button
            v-for="tab in visibleTabs"
            :key="tab.id"
            type="button"
            class="login-tab"
            :class="{ active: activeTab === tab.id }"
            @click="activeTab = tab.id"
          >
            {{ tab.label }}
          </button>
        </div>

        <form v-if="activeTab === 'password'" class="login-form" @submit.prevent="loginPassword">
          <p class="admin-hint">本地账号登录；首次部署可用超管 <strong>admin</strong> + ADMIN_PASSWORD</p>
          <el-input
            v-model="form.username"
            placeholder="用户名"
            size="large"
            autocomplete="username"
          />
          <el-input
            v-model="form.password"
            type="password"
            placeholder="密码"
            size="large"
            show-password
            autocomplete="current-password"
          />
          <el-button type="primary" size="large" class="full" native-type="submit" :loading="pwdLoading">
            登录
          </el-button>
        </form>

        <div v-else-if="activeTab === 'dingtalk_oauth'" class="oauth-panel">
          <p class="oauth-hint">使用钉钉 App 扫码，首次登录需超级管理员审批</p>
          <el-button type="primary" size="large" class="full" :loading="oauthLoading" @click="loginOAuth('dingtalk_oauth')">
            打开钉钉扫码
          </el-button>
        </div>

        <div v-else-if="activeTab === 'feishu_oauth'" class="oauth-panel">
          <p class="oauth-hint">使用飞书 App 扫码，首次登录需超级管理员审批</p>
          <el-button type="primary" size="large" class="full" :loading="oauthLoading" @click="loginOAuth('feishu_oauth')">
            打开飞书授权
          </el-button>
        </div>

        <p v-else-if="!visibleTabs.length" class="oauth-hint">
          未配置可用登录方式。请设置 ADMIN_PASSWORD，或配置钉钉/飞书应用凭证。
        </p>
      </template>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import client from '@/api/client'
import { useAuthStore } from '@/stores/auth'

type ProviderId = 'password' | 'dingtalk_oauth' | 'feishu_oauth'

interface AuthProvider {
  id: ProviderId
  label: string
  kind: string
  enabled: boolean
  login_url_path?: string
  callback_path?: string
}

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()

const providers = ref<AuthProvider[]>([])
const loadingProviders = ref(true)
const activeTab = ref<ProviderId>('password')
const oauthLoading = ref(false)
const pwdLoading = ref(false)
const form = reactive({ username: 'admin', password: '' })

const visibleTabs = computed(() => {
  // Always show password tab so bootstrap is possible; mark disabled via hint if needed.
  const list = providers.value.filter((p) => p.id === 'password' || p.enabled)
  if (!list.some((p) => p.id === 'password')) {
    list.unshift({ id: 'password', label: '本地密码', kind: 'password', enabled: true })
  }
  return list
})

function oauthCallbackUri(): string {
  const base = import.meta.env.BASE_URL || '/'
  const path = `${base}login/callback`.replace(/\/{2,}/g, '/')
  return `${window.location.origin}${path.startsWith('/') ? path : `/${path}`}`
}

async function loadProviders() {
  loadingProviders.value = true
  try {
    const { data } = await client.get('/api/auth/providers')
    providers.value = (data.providers || []) as AuthProvider[]
    const first = visibleTabs.value[0]
    if (first) activeTab.value = first.id
  } catch {
    providers.value = [{ id: 'password', label: '本地密码', kind: 'password', enabled: true }]
    activeTab.value = 'password'
  } finally {
    loadingProviders.value = false
  }
}

async function loginOAuth(providerId: ProviderId) {
  const provider = providers.value.find((p) => p.id === providerId)
  if (!provider?.login_url_path) {
    ElMessage.error('该登录方式不可用')
    return
  }
  oauthLoading.value = true
  try {
    const redirectUri = oauthCallbackUri()
    const { data } = await client.get(provider.login_url_path, {
      params: { redirect_uri: redirectUri },
    })
    sessionStorage.setItem('oauth_state', data.state)
    sessionStorage.setItem('oauth_provider', providerId)
    sessionStorage.setItem('oauth_redirect', (route.query.redirect as string) || '/')
    sessionStorage.setItem('oauth_redirect_uri', data.redirect_uri || redirectUri)
    window.location.href = data.url
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '获取登录地址失败')
  } finally {
    oauthLoading.value = false
  }
}

async function loginPassword() {
  if (!form.username.trim()) {
    ElMessage.warning('请输入用户名')
    return
  }
  if (!form.password) {
    ElMessage.warning('请输入密码')
    return
  }
  pwdLoading.value = true
  try {
    const { data } = await client.post('/api/auth/login', {
      username: form.username.trim(),
      password: form.password,
    })
    if (data?.status === 'pending') {
      ElMessage.info(data.message || '等待审批')
      router.push('/pending-approval')
      return
    }
    auth.setSession(data.access_token, data.user)
    const redirect = (route.query.redirect as string) || '/'
    router.push(redirect)
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '登录失败')
  } finally {
    pwdLoading.value = false
  }
}

onMounted(loadProviders)
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
}
.login-tabs {
  display: grid;
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
.oauth-panel {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.oauth-hint,
.loading-hint {
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
