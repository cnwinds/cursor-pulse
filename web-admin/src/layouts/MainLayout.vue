<template>
  <el-container class="layout">
    <el-aside width="220px" class="aside">
      <div class="brand">
        <span class="logo">脉</span>
        <div>
          <div class="title">小脉</div>
          <div class="subtitle">Cursor Pulse</div>
        </div>
      </div>
      <el-menu :default-active="active" router>
        <el-menu-item v-if="auth.hasPermission('settings:read')" index="/">
          <el-icon><Odometer /></el-icon>
          <span>概览</span>
        </el-menu-item>
        <el-menu-item v-if="auth.hasPermission('accounts:read')" index="/accounts">
          <el-icon><Wallet /></el-icon>
          <span>账号台账</span>
        </el-menu-item>
        <el-menu-item v-if="auth.hasPermission('requests:read')" index="/access-requests">
          <el-icon><Tickets /></el-icon>
          <span>工具申请</span>
        </el-menu-item>
        <el-menu-item v-if="auth.hasPermission('knowledge:read')" index="/tool-tips">
          <el-icon><Reading /></el-icon>
          <span>技巧知识库</span>
        </el-menu-item>
        <el-menu-item v-if="auth.hasPermission('submissions:read')" index="/ingestions">
          <el-icon><Document /></el-icon>
          <span>摄取记录</span>
        </el-menu-item>
        <el-menu-item v-if="auth.hasPermission('metrics:read')" index="/metrics">
          <el-icon><DataLine /></el-icon>
          <span>指标</span>
        </el-menu-item>
        <el-menu-item v-if="auth.hasPermission('memory:read')" index="/memory">
          <el-icon><Collection /></el-icon>
          <span>记忆</span>
        </el-menu-item>
        <el-menu-item v-if="auth.hasPermission('memory:read')" index="/principles">
          <el-icon><Memo /></el-icon>
          <span>原则</span>
        </el-menu-item>
        <el-menu-item v-if="auth.hasPermission('memory:read')" index="/disclosure">
          <el-icon><View /></el-icon>
          <span>披露</span>
        </el-menu-item>
        <el-menu-item v-if="auth.hasPermission('memory:read')" index="/evolution">
          <el-icon><MagicStick /></el-icon>
          <span>自进化</span>
        </el-menu-item>
        <el-menu-item v-if="auth.hasPermission('audit:read')" index="/audit">
          <el-icon><Notebook /></el-icon>
          <span>审计</span>
        </el-menu-item>
        <el-menu-item v-if="auth.hasPermission('settings:read')" index="/integrations">
          <el-icon><Connection /></el-icon>
          <span>系统</span>
        </el-menu-item>
        <el-menu-item v-if="auth.hasPermission('settings:read')" index="/settings">
          <el-icon><Setting /></el-icon>
          <span>配置</span>
        </el-menu-item>
        <el-menu-item v-if="auth.hasPermission('admin:users')" index="/users">
          <el-icon><Key /></el-icon>
          <span>用户管理</span>
        </el-menu-item>
      </el-menu>
    </el-aside>
    <el-container>
      <el-header class="header">
        <div>{{ pageTitle }}</div>
        <div class="user-bar">
          <span>{{ auth.user?.display_name }}</span>
          <el-tag size="small" type="info">{{ auth.user?.portal_role }}</el-tag>
          <el-button link type="danger" @click="onLogout">退出</el-button>
        </div>
      </el-header>
      <el-main class="main">
        <router-view />
      </el-main>
    </el-container>
    <ChatPanel />
  </el-container>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import ChatPanel from '@/components/ChatPanel.vue'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const active = computed(() => route.path)
const pageTitle = computed(() => (route.meta.title as string) || '小脉后台')

function onLogout() {
  auth.logout()
  router.push({ name: 'login' })
}
</script>

<style scoped>
.layout {
  min-height: 100vh;
}
.aside {
  background: #0f172a;
  color: #e2e8f0;
}
.brand {
  display: flex;
  gap: 12px;
  align-items: center;
  padding: 20px 16px;
}
.logo {
  width: 40px;
  height: 40px;
  border-radius: 12px;
  background: linear-gradient(135deg, #38bdf8, #6366f1);
  display: grid;
  place-items: center;
  font-weight: 700;
}
.title {
  font-weight: 600;
}
.subtitle {
  font-size: 12px;
  opacity: 0.7;
}
.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid #e5e7eb;
  background: #fff;
}
.user-bar {
  display: flex;
  align-items: center;
  gap: 12px;
}
.main {
  background: #f8fafc;
}
:deep(.el-menu) {
  border-right: none;
  background: transparent;
}
:deep(.el-menu-item) {
  color: #cbd5e1;
}
:deep(.el-menu-item.is-active) {
  background: rgba(99, 102, 241, 0.2);
  color: #fff;
}
</style>
