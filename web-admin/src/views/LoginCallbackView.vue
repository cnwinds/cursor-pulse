<template>
  <div class="callback-page">
    <el-result icon="loading" :title="loadingTitle" />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import client from '@/api/client'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const provider = computed(() => sessionStorage.getItem('oauth_provider') || 'dingtalk_oauth')
const loadingTitle = computed(() =>
  provider.value === 'feishu_oauth' ? '正在完成飞书登录…' : '正在完成钉钉登录…'
)

onMounted(async () => {
  const code = route.query.code as string
  const state = route.query.state as string
  const saved = sessionStorage.getItem('oauth_state')
  const providerId = sessionStorage.getItem('oauth_provider') || 'dingtalk_oauth'
  if (!code) {
    ElMessage.error('缺少授权码')
    router.replace({ name: 'login' })
    return
  }
  if (saved && state && saved !== state) {
    ElMessage.error('OAuth state 校验失败')
    router.replace({ name: 'login' })
    return
  }
  const callbackPath =
    providerId === 'feishu_oauth' ? '/api/auth/feishu/callback' : '/api/auth/dingtalk/callback'
  const body: Record<string, string> = { code }
  const redirectUri = sessionStorage.getItem('oauth_redirect_uri')
  if (providerId === 'feishu_oauth' && redirectUri) {
    body.redirect_uri = redirectUri
  }
  try {
    const { data, status } = await client.post(callbackPath, body)
    if (status === 202 && data.status === 'pending') {
      sessionStorage.setItem('portal_pending_user', JSON.stringify(data.user))
      sessionStorage.removeItem('oauth_state')
      sessionStorage.removeItem('oauth_redirect')
      sessionStorage.removeItem('oauth_provider')
      sessionStorage.removeItem('oauth_redirect_uri')
      router.replace({ name: 'pending-approval' })
      return
    }
    auth.setSession(data.access_token, data.user, data.refresh_token)
    const redirect = sessionStorage.getItem('oauth_redirect') || '/'
    sessionStorage.removeItem('oauth_state')
    sessionStorage.removeItem('oauth_redirect')
    sessionStorage.removeItem('oauth_provider')
    sessionStorage.removeItem('oauth_redirect_uri')
    router.replace(redirect)
  } catch (e: any) {
    const label = providerId === 'feishu_oauth' ? '飞书' : '钉钉'
    ElMessage.error(e.response?.data?.detail || `${label}登录失败`)
    router.replace({ name: 'login' })
  }
})
</script>

<style scoped>
.callback-page {
  min-height: 100vh;
  display: grid;
  place-items: center;
}
</style>
