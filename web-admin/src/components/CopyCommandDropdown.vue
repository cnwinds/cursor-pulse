<template>
  <el-dropdown
    trigger="click"
    @visible-change="onVisible"
    @command="copyItem"
  >
    <el-button :size="size" type="primary" plain>复制命令</el-button>
    <template #dropdown>
      <el-dropdown-menu>
        <el-dropdown-item
          v-for="item in menuItems"
          :key="item.key"
          :command="item"
        >
          {{ item.label }}
        </el-dropdown-item>
        <el-dropdown-item v-if="!menuItems.length" disabled>
          尚未配置代理地址
        </el-dropdown-item>
      </el-dropdown-menu>
    </template>
  </el-dropdown>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import client from '@/api/client'
import { copyText } from '@/utils/clipboard'

export type ShellKind = 'bash' | 'powershell'

export interface ProxyCommandMenuItem {
  key: string
  proxy_url: string
  proxy_name: string
  shell: ShellKind
  label: string
}

const props = defineProps<{
  setupUrl: string
  size?: 'small' | 'default' | 'large'
}>()

const router = useRouter()
const addresses = ref<Array<{ url: string; display_name: string }>>([])

const menuItems = computed<ProxyCommandMenuItem[]>(() =>
  addresses.value.flatMap((addr) => {
    const name = addr.display_name || addr.url
    return [
      {
        key: `${addr.url}|powershell`,
        proxy_url: addr.url,
        proxy_name: name,
        shell: 'powershell',
        label: `${name} · Windows PowerShell`,
      },
      {
        key: `${addr.url}|bash`,
        proxy_url: addr.url,
        proxy_name: name,
        shell: 'bash',
        label: `${name} · Linux / macOS`,
      },
    ]
  }),
)

async function promptConfigure() {
  try {
    await ElMessageBox.confirm(
      '尚未配置代理地址，请前往「系统设置 → 代理地址」添加',
      '需要配置代理地址',
      {
        confirmButtonText: '前往配置',
        cancelButtonText: '取消',
        type: 'warning',
      },
    )
    await router.push({ path: '/settings', query: { tab: 'proxy_addresses' } })
  } catch {
    // 用户取消
  }
}

async function loadAddresses() {
  try {
    const res = await client.get('/api/v2/proxy-addresses')
    addresses.value = Array.isArray(res.data?.addresses) ? res.data.addresses : []
  } catch {
    addresses.value = []
  }
}

async function onVisible(visible: boolean) {
  if (!visible) return
  await loadAddresses()
  if (!addresses.value.length) {
    await promptConfigure()
  }
}

async function copyItem(item: ProxyCommandMenuItem) {
  try {
    const res = await client.get(props.setupUrl, {
      params: { shell: item.shell, proxy_url: item.proxy_url },
    })
    const command = res.data?.command
    if (!command) {
      ElMessage.error('未返回命令')
      return
    }
    await copyText(command)
    ElMessage.success(
      item.shell === 'powershell'
        ? `已复制 ${item.proxy_name} · PowerShell 命令`
        : `已复制 ${item.proxy_name} · Linux 命令`,
    )
  } catch (err: any) {
    const status = err?.response?.status
    const detail = err?.response?.data?.detail
    if (status === 422) {
      await promptConfigure()
      return
    }
    ElMessage.error(typeof detail === 'string' ? detail : err?.message || '复制失败')
  }
}
</script>
