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
          path: 'members',
          name: 'members',
          component: () => import('@/views/MembersView.vue'),
          meta: { permission: 'members:read', title: '成员' },
        },
        {
          path: 'submissions',
          name: 'submissions',
          component: () => import('@/views/SubmissionsView.vue'),
          meta: { permission: 'submissions:read', title: '提交进度' },
        },
        {
          path: 'metrics',
          name: 'metrics',
          component: () => import('@/views/MetricsView.vue'),
          meta: { permission: 'metrics:read', title: '指标' },
        },
        {
          path: 'memory',
          name: 'memory',
          component: () => import('@/views/MemoryView.vue'),
          meta: { permission: 'memory:read', title: '记忆原子' },
        },
        {
          path: 'principles',
          name: 'principles',
          component: () => import('@/views/PrinciplesView.vue'),
          meta: { permission: 'memory:read', title: '原则' },
        },
        {
          path: 'disclosure',
          name: 'disclosure',
          component: () => import('@/views/DisclosureView.vue'),
          meta: { permission: 'memory:read', title: '披露审计' },
        },
        {
          path: 'evolution',
          name: 'evolution',
          component: () => import('@/views/EvolutionView.vue'),
          meta: { permission: 'memory:read', title: '自进化' },
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
          meta: { permission: 'settings:read', title: '业务配置' },
        },
        {
          path: 'integrations',
          name: 'integrations',
          component: () => import('@/views/IntegrationsView.vue'),
          meta: { permission: 'settings:read', title: '系统与集成' },
        },
        {
          path: 'users',
          name: 'users',
          component: () => import('@/views/UsersView.vue'),
          meta: { permission: 'admin:users', title: '账号权限' },
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
