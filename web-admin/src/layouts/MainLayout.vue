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
      <el-menu
        :default-active="active"
        :default-openeds="['grp-pulse', 'grp-assistant', 'grp-system']"
        router
      >
        <el-sub-menu index="grp-pulse">
          <template #title>
            <span>Pulse</span>
          </template>
          <el-menu-item v-if="auth.hasPermission('settings:read')" index="/">
            <el-icon><Odometer /></el-icon>
            <span>概览</span>
          </el-menu-item>
          <el-menu-item v-if="auth.hasPermission('accounts:read')" index="/accounts">
            <el-icon><Wallet /></el-icon>
            <span>账号台账</span>
          </el-menu-item>
          <el-menu-item v-if="auth.hasPermission('accounts:read')" index="/quota-board">
            <el-icon><TrendCharts /></el-icon>
            <span>额度看板</span>
          </el-menu-item>
          <el-menu-item v-if="auth.hasPermission('accounts:read')" index="/loans">
            <el-icon><Share /></el-icon>
            <span>借用记录</span>
          </el-menu-item>
          <el-menu-item v-if="auth.hasPermission('proxy:read')" index="/proxy-keys">
            <el-icon><Key /></el-icon>
            <span>代理 Key</span>
          </el-menu-item>
          <el-menu-item v-if="auth.hasPermission('knowledge:read')" index="/tool-tips">
            <el-icon><Reading /></el-icon>
            <span>技巧知识库</span>
          </el-menu-item>
        </el-sub-menu>

        <el-sub-menu index="grp-assistant">
          <template #title>
            <span>助手中心</span>
          </template>
          <el-menu-item v-if="auth.hasPermission('assistant:skills:read')" index="/skills">
            <el-icon><Reading /></el-icon>
            <span>技能一览</span>
          </el-menu-item>
          <el-menu-item v-if="auth.hasPermission('assistant:capabilities:read')" index="/capabilities">
            <el-icon><Grid /></el-icon>
            <span>工具授权</span>
          </el-menu-item>
          <el-menu-item
            v-if="auth.hasPermission('assistant:sessions:read:self') || auth.hasPermission('assistant:sessions:read:all')"
            index="/sessions"
          >
            <el-icon><ChatLineRound /></el-icon>
            <span>会话账本</span>
          </el-menu-item>
          <el-menu-item
            v-if="auth.hasPermission('assistant:prompts:read')"
            index="/prompts"
          >
            <el-icon><EditPen /></el-icon>
            <span>Prompt 一览</span>
          </el-menu-item>
        </el-sub-menu>

        <el-sub-menu index="grp-system">
          <template #title>
            <span>系统</span>
          </template>
          <el-menu-item v-if="auth.hasPermission('audit:read')" index="/audit">
            <el-icon><Notebook /></el-icon>
            <span>审计</span>
          </el-menu-item>
          <el-menu-item v-if="auth.hasPermission('settings:read')" index="/settings">
            <el-icon><Setting /></el-icon>
            <span>系统设置</span>
          </el-menu-item>
          <el-menu-item v-if="auth.hasPermission('admin:users')" index="/users">
            <el-icon><Key /></el-icon>
            <span>用户管理</span>
          </el-menu-item>
        </el-sub-menu>
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
:deep(.el-sub-menu__title) {
  color: #cbd5e1;
}
:deep(.el-sub-menu .el-menu) {
  background: transparent;
}
</style>
