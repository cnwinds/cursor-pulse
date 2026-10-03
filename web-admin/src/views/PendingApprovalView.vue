<template>
  <div class="pulse-auth-shell">
    <el-card shadow="never" class="pulse-auth-card">
      <el-result icon="info" title="等待管理员审批">
        <template #sub-title>
          <p>你好，<strong>{{ userName }}</strong></p>
          <p class="pulse-hint">账号正在等待超级管理员审批开通后台权限。</p>
          <p class="pulse-hint">审批通过后请使用原登录方式重新登录。</p>
        </template>
        <template #extra>
          <el-button type="primary" @click="recheck">重新检查</el-button>
          <el-button @click="backLogin">返回登录</el-button>
        </template>
      </el-result>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const auth = useAuthStore()
const pendingUser = ref<{ display_name?: string } | null>(null)

const userName = computed(
  () => pendingUser.value?.display_name || auth.user?.display_name || '用户',
)

onMounted(() => {
  const raw = sessionStorage.getItem('portal_pending_user')
  if (raw) {
    pendingUser.value = JSON.parse(raw)
  }
})

async function backLogin() {
  sessionStorage.removeItem('portal_pending_user')
  await auth.logout()
  router.replace({ name: 'login' })
}

function recheck() {
  ElMessage.info('请重新扫码登录以检查审批状态')
  backLogin()
}
</script>
