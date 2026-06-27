<template>
  <div class="callback-page">
    <el-result icon="loading" title="正在完成钉钉登录…" />
  </div>
</template>

<script setup lang="ts">
import { onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import client from '@/api/client'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

onMounted(async () => {
  const code = route.query.code as string
  const state = route.query.state as string
  const saved = sessionStorage.getItem('oauth_state')
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
  try {
    const { data } = await client.post('/api/auth/dingtalk/callback', { code })
    auth.setSession(data.access_token, data.user)
    const redirect = sessionStorage.getItem('oauth_redirect') || '/'
    sessionStorage.removeItem('oauth_state')
    sessionStorage.removeItem('oauth_redirect')
    router.replace(redirect)
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '钉钉登录失败')
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
