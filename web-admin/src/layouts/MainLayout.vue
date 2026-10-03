<template>
  <el-container class="layout">
    <el-aside width="228px" class="aside">
      <div class="brand">
        <img class="logo" src="/logo.svg" alt="Cursor Pulse" />
        <div class="brand-text">
          <div class="title">小脉</div>
          <div class="subtitle">Cursor Pulse</div>
        </div>
      </div>
      <el-menu
        :default-active="active"
        :default-openeds="['grp-pulse', 'grp-assistant', 'grp-system']"
        router
        class="nav-menu"
      >
        <el-sub-menu index="grp-pulse">
          <template #title>
            <span>Pulse</span>
          </template>
          <el-menu-item index="/">
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
          <el-menu-item v-if="auth.hasPermission('accounts:read')" index="/usage-analytics">
            <el-icon><DataAnalysis /></el-icon>
            <span>用量分析</span>
          </el-menu-item>
          <el-menu-item
            v-if="
              auth.hasPermission('accounts:read') ||
              auth.hasPermission('proxy:read') ||
              auth.hasPermission('settings:read')
            "
            index="/borrow-management"
          >
            <el-icon><Share /></el-icon>
            <span>借用管理</span>
          </el-menu-item>
          <el-menu-item v-if="auth.hasPermission('accounts:read')" index="/membership">
            <el-icon><Wallet /></el-icon>
            <span>会员管理</span>
          </el-menu-item>
          <el-menu-item
            v-if="auth.hasPermission('loans:self') && !auth.hasPermission('accounts:read')"
            index="/my-loans"
          >
            <el-icon><Share /></el-icon>
            <span>我的借用</span>
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
      <div class="aside-footer">
        <span class="pulse-dot" aria-hidden="true" />
        <span>团队用量协调</span>
      </div>
    </el-aside>
    <el-container class="content-shell">
      <el-header class="header">
        <div class="page-title">{{ pageTitle }}</div>
        <div class="user-bar">
          <span class="user-name">{{ auth.user?.display_name }}</span>
          <el-tag size="small" effect="plain" type="info">{{ auth.user?.portal_role }}</el-tag>
          <el-button link type="danger" @click="onLogout">退出</el-button>
        </div>
      </el-header>
      <el-main class="main pulse-main-fade">
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

async function onLogout() {
  await auth.logout()
  router.push({ name: 'login' })
}
</script>

<style scoped>
.layout {
  min-height: 100vh;
}

.aside {
  display: flex;
  flex-direction: column;
  background: var(--pulse-sidebar-bg-gradient);
  color: var(--pulse-sidebar-text);
  border-right: 1px solid rgba(255, 255, 255, 0.06);
  box-shadow: 4px 0 24px rgba(0, 0, 0, 0.12);
}

.brand {
  display: flex;
  gap: 12px;
  align-items: center;
  padding: 22px 18px 18px;
}

.logo {
  width: 42px;
  height: 42px;
  border-radius: var(--pulse-radius-md);
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.25);
}

.brand-text {
  min-width: 0;
}

.title {
  font-weight: var(--pulse-font-bold);
  font-size: var(--pulse-text-md);
  letter-spacing: 0.02em;
  color: #f8fafc;
}

.subtitle {
  font-size: var(--pulse-text-xs);
  font-weight: var(--pulse-font-medium);
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--pulse-sidebar-text);
  margin-top: 2px;
}

.nav-menu {
  flex: 1;
  padding: 0 10px 12px;
  overflow-y: auto;
}

.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 56px;
  padding: 0 22px;
  border-bottom: 1px solid var(--pulse-header-border);
  background: var(--pulse-header-bg);
  backdrop-filter: blur(10px);
}

.page-title {
  color: var(--pulse-text-strong);
}

.user-bar {
  display: flex;
  align-items: center;
  gap: 12px;
}

.user-name {
  font-size: var(--pulse-text-md);
  font-weight: 500;
  color: var(--pulse-text-primary);
}

.main {
  background-color: var(--pulse-bg-page);
  background-image: var(--pulse-bg-page-pattern);
  background-size: 20px 20px;
  padding: var(--pulse-space-page) !important;
}

.aside-footer {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 14px 18px 18px;
  font-size: var(--pulse-text-xs);
  color: rgba(148, 163, 184, 0.85);
  border-top: 1px solid rgba(255, 255, 255, 0.06);
}

.pulse-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--pulse-color-accent-soft);
  box-shadow: 0 0 10px var(--pulse-color-accent-soft);
  animation: pulse-glow 2.4s ease-in-out infinite;
}

@keyframes pulse-glow {
  0%,
  100% {
    opacity: 1;
    transform: scale(1);
  }
  50% {
    opacity: 0.55;
    transform: scale(0.85);
  }
}

:deep(.el-menu) {
  border-right: none;
  background: transparent;
}

:deep(.el-menu-item) {
  color: var(--pulse-sidebar-text);
  border-radius: var(--pulse-radius-sm);
  margin: 2px 0;
  height: 42px;
  transition:
    background var(--pulse-transition),
    color var(--pulse-transition);
}

:deep(.el-menu-item:hover) {
  color: var(--pulse-sidebar-text-hover);
  background: rgba(255, 255, 255, 0.05);
}

:deep(.el-menu-item.is-active) {
  background: var(--pulse-sidebar-active-bg);
  color: var(--pulse-sidebar-text-active);
  position: relative;
}

:deep(.el-menu-item.is-active)::before {
  content: '';
  position: absolute;
  left: 0;
  top: 8px;
  bottom: 8px;
  width: 3px;
  border-radius: 0 3px 3px 0;
  background: var(--pulse-sidebar-active-border);
}

:deep(.el-sub-menu__title) {
  color: var(--pulse-sidebar-text-hover);
  font-weight: 600;
  font-size: var(--pulse-text-sm);
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

:deep(.el-sub-menu .el-menu) {
  background: transparent;
}
</style>
