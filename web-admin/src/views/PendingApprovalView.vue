<template>
  <div class="pending-page">
    <el-card shadow="never" class="card">
      <el-result icon="info" title="等待管理员审批">
        <template #sub-title>
          <p>你好，<strong>{{ userName }}</strong></p>
          <p class="hint">你已通过钉钉扫码登录，账号正在等待超级管理员审批开通后台权限。</p>
          <p class="hint">审批通过后请重新扫码登录。</p>
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

function backLogin() {
  sessionStorage.removeItem('portal_pending_user')
  auth.logout()
  router.replace({ name: 'login' })
}

function recheck() {
  ElMessage.info('请重新扫码登录以检查审批状态')
  backLogin()
}
</script>

<style scoped>
.pending-page {
  min-height: 100vh;
  display: grid;
  place-items: center;
  background: #f8fafc;
  padding: 24px;
}
.card {
  width: min(520px, 100%);
}
.hint {
  color: #64748b;
  font-size: 14px;
  margin: 8px 0 0;
}
</style>
