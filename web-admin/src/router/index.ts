import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/login',
      name: 'login',
      component: () => import('@/views/LoginView.vue'),
      meta: { public: true },
    },
    {
      path: '/login/callback',
      name: 'login-callback',
      component: () => import('@/views/LoginCallbackView.vue'),
      meta: { public: true },
    },
    {
      path: '/pending-approval',
      name: 'pending-approval',
      component: () => import('@/views/PendingApprovalView.vue'),
      meta: { public: true },
    },
    {
      path: '/',
      component: () => import('@/layouts/MainLayout.vue'),
      children: [
        {
          path: '',
          name: 'dashboard',
          component: () => import('@/views/DashboardView.vue'),
          meta: { permission: 'settings:read', title: '概览' },
        },
        {
          path: 'accounts',
          name: 'accounts',
          component: () => import('@/views/AccountsView.vue'),
          meta: { permission: 'accounts:read', title: '账号台账' },
        },
        {
          path: 'quota-board',
          name: 'quota-board',
          component: () => import('@/views/QuotaBoardView.vue'),
          meta: { permission: 'accounts:read', title: '额度看板' },
        },
        {
          path: 'loans',
          name: 'loans',
          component: () => import('@/views/LoansView.vue'),
          meta: { permission: 'accounts:read', title: '借用记录' },
        },
        {
          path: 'tool-tips',
          name: 'tool-tips',
          component: () => import('@/views/ToolTipsView.vue'),
          meta: { permission: 'knowledge:read', title: '技巧知识库' },
        },
        {
          path: 'proxy-keys',
          name: 'proxy-keys',
          component: () => import('@/views/ProxyKeysView.vue'),
          meta: { permission: 'proxy:read', title: '代理 Key' },
        },
        {
          path: 'audit',
          name: 'audit',
          component: () => import('@/views/AuditView.vue'),
          meta: { permission: 'audit:read', title: '审计日志' },
        },
        {
          path: 'settings',
          name: 'settings',
          component: () => import('@/views/SettingsView.vue'),
          meta: { permission: 'settings:read', title: '系统设置' },
        },
        {
          path: 'integrations',
          redirect: (to) => ({ path: '/settings', query: { ...to.query, tab: 'integrations' } }),
        },
        {
          path: 'users',
          name: 'users',
          component: () => import('@/views/UsersView.vue'),
          meta: { permission: 'admin:users', title: '用户与权限' },
        },
        {
          path: 'skills',
          name: 'skills',
          component: () => import('@/views/SkillsView.vue'),
          meta: { permission: 'assistant:skills:read', title: '技能一览' },
        },
        {
          path: 'capabilities',
          name: 'capabilities',
          component: () => import('@/views/CapabilitiesView.vue'),
          meta: { permission: 'assistant:capabilities:read', title: '工具授权' },
        },
        {
          path: 'sessions',
          name: 'sessions',
          component: () => import('@/views/SessionsView.vue'),
          meta: { permission: 'assistant:sessions:read:self', title: '会话账本' },
        },
        {
          path: 'prompts',
          name: 'prompts',
          component: () => import('@/views/PromptsView.vue'),
          meta: { permission: 'assistant:prompts:read', title: 'Prompt 一览' },
        },
        {
          path: 'prompt-studio',
          redirect: '/prompts',
        },
        {
          path: 'forbidden',
          name: 'forbidden',
          component: () => import('@/views/ForbiddenView.vue'),
          meta: { title: '无权限' },
        },
      ],
    },
  ],
})

router.beforeEach(async (to) => {
  const auth = useAuthStore()
  if (to.meta.public) return true

  if (!auth.isLoggedIn) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }

  const perm = to.meta.permission as string | undefined
  if (perm && !auth.hasPermission(perm)) {
    return { name: 'forbidden' }
  }
  return true
})

export default router
