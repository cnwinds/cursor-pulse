<template>
  <el-button
    v-if="iconOnly"
    :size="size"
    link
    type="primary"
    class="copy-cmd-icon-btn"
    aria-label="复制命令"
    title="复制命令"
    @click="open"
  >
    <el-icon><DocumentCopy /></el-icon>
  </el-button>
  <el-button v-else :size="size" type="primary" plain @click="open">复制命令</el-button>

  <el-dialog
    v-model="visible"
    title="复制命令"
    width="680px"
    append-to-body
    class="copy-cmd-dialog"
  >
    <div v-if="addresses.length > 1" class="addr-row">
      <span class="addr-label">代理地址</span>
      <el-radio-group v-model="proxyUrl" size="small" @change="loadActiveTab">
        <el-radio-button v-for="addr in addresses" :key="addr.url" :value="addr.url">
          {{ addr.display_name || addr.url }}
        </el-radio-button>
      </el-radio-group>
    </div>

    <el-tabs v-model="tab" @tab-change="loadActiveTab">
      <el-tab-pane label="CLI" name="cli">
        <div v-loading="cliLoading" class="tab-body">
          <el-alert v-if="cliError" type="error" :closable="false" show-icon :title="cliError" />
          <template v-else>
            <p class="tab-hint">在终端运行，代理与密钥只对本次启动的 agent 进程生效，不修改系统环境变量。</p>
            <div v-for="item in cliItems" :key="item.key" class="cmd-block">
              <div class="cmd-head">
                <span class="cmd-title">{{ item.title }}</span>
                <el-button size="small" type="primary" link @click="copy(item)">
                  <el-icon><DocumentCopy /></el-icon>
                  <span>复制</span>
                </el-button>
              </div>
              <pre class="cmd-text">{{ item.display }}</pre>
            </div>
          </template>
        </div>
      </el-tab-pane>

      <el-tab-pane label="IDE" name="ide">
        <div v-loading="ideLoading" class="tab-body">
          <el-alert v-if="ideError" type="error" :closable="false" show-icon :title="ideError" />
          <template v-else>
            <p class="tab-hint">适用于 Windows 上的 Cursor IDE，在 PowerShell 中运行。安装完成后自己启动 Cursor 即可正常使用（已打开的需先完全退出再重开）。</p>
            <div v-for="item in ideItems" :key="item.key" class="cmd-block">
              <div class="cmd-head">
                <span class="cmd-title">{{ item.title }}</span>
                <el-button size="small" type="primary" link @click="copy(item)">
                  <el-icon><DocumentCopy /></el-icon>
                  <span>复制</span>
                </el-button>
              </div>
              <p v-if="item.desc" class="cmd-desc">{{ item.desc }}</p>
              <pre class="cmd-text">{{ item.display }}</pre>
            </div>
          </template>
        </div>
      </el-tab-pane>
    </el-tabs>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import client from '@/api/client'
import { copyText } from '@/utils/clipboard'

type TabName = 'cli' | 'ide'

interface ProxyAddress {
  url: string
  display_name: string
}

interface CliCommand {
  proxy_url: string
  shell: 'bash' | 'powershell'
  command: string
}

interface IdeSetup {
  plaintext_key: string
  command: string
  uninstall_command: string
}

interface CommandItem {
  key: string
  title: string
  desc?: string
  command: string
  display: string
}

const props = defineProps<{
  setupUrl: string
  size?: 'small' | 'default' | 'large'
  iconOnly?: boolean
}>()

const router = useRouter()
const visible = ref(false)
const tab = ref<TabName>('cli')
const addresses = ref<ProxyAddress[]>([])
const proxyUrl = ref('')

const cliLoading = ref(false)
const cliError = ref('')
const cliKey = ref('')
const cliCommands = ref<CliCommand[]>([])

const ideLoading = ref(false)
const ideError = ref('')
const ideByUrl = ref<Record<string, IdeSetup>>({})

function maskKey(text: string, key: string): string {
  if (!key || key.length <= 12) return text
  return text.split(key).join(`${key.slice(0, 10)}…`)
}

const cliItems = computed<CommandItem[]>(() => {
  const titles: Record<CliCommand['shell'], string> = {
    powershell: 'Windows（PowerShell / cmd）',
    bash: 'Linux / macOS',
  }
  return cliCommands.value
    .filter((c) => c.proxy_url === proxyUrl.value)
    .map((c) => ({
      key: `cli|${c.shell}`,
      title: titles[c.shell],
      command: c.command,
      display: maskKey(c.command, cliKey.value),
    }))
})

const ideItems = computed<CommandItem[]>(() => {
  const setup = ideByUrl.value[proxyUrl.value]
  if (!setup) return []
  const scopedKey = setup.plaintext_key.startsWith('pkide_')
  return [
    {
      key: 'ide|install',
      title: '安装命令',
      desc: scopedKey
        ? '安装代理 CA 证书，并把带 IDE 专用密钥（pkide_）的代理地址写入 Cursor 设置。该密钥不能用于 CLI；换电脑时重复运行即可。'
        : '安装代理 CA 证书，并把带密钥的代理地址写入 Cursor 设置。换电脑时重复运行即可。',
      command: setup.command,
      display: maskKey(setup.command, setup.plaintext_key),
    },
    {
      key: 'ide|restore',
      title: '恢复命令',
      desc: '移除 Cursor 中的代理设置并删除代理 CA 证书，让 Cursor 恢复直连。不含密钥，可放心转发。',
      command: setup.uninstall_command,
      display: setup.uninstall_command,
    },
  ]
})

async function promptConfigure() {
  visible.value = false
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

function errorMessage(err: any, fallback: string): string {
  const detail = err?.response?.data?.detail
  return typeof detail === 'string' ? detail : err?.message || fallback
}

async function loadAddresses(): Promise<boolean> {
  try {
    const res = await client.get('/api/v2/proxy-addresses')
    addresses.value = Array.isArray(res.data?.addresses) ? res.data.addresses : []
  } catch {
    addresses.value = []
  }
  return addresses.value.length > 0
}

async function loadCli() {
  if (cliCommands.value.length) return
  cliLoading.value = true
  cliError.value = ''
  try {
    const res = await client.get(props.setupUrl, { params: { kind: 'cli' } })
    cliKey.value = res.data?.plaintext_key || ''
    cliCommands.value = Array.isArray(res.data?.commands) ? res.data.commands : []
  } catch (err: any) {
    if (err?.response?.status === 422) {
      await promptConfigure()
      return
    }
    cliError.value = errorMessage(err, '获取 CLI 命令失败')
  } finally {
    cliLoading.value = false
  }
}

async function loadIde() {
  const url = proxyUrl.value
  if (ideByUrl.value[url]) return
  ideLoading.value = true
  ideError.value = ''
  try {
    const res = await client.get(props.setupUrl, {
      params: { kind: 'ide', shell: 'powershell', proxy_url: url },
    })
    if (!res.data?.command || !res.data?.uninstall_command) {
      ideError.value = '未返回 IDE 命令'
      return
    }
    ideByUrl.value = { ...ideByUrl.value, [url]: res.data }
  } catch (err: any) {
    if (err?.response?.status === 422) {
      await promptConfigure()
      return
    }
    ideError.value = errorMessage(err, '获取 IDE 命令失败')
  } finally {
    ideLoading.value = false
  }
}

function loadActiveTab() {
  return tab.value === 'ide' ? loadIde() : loadCli()
}

async function open() {
  if (!(await loadAddresses())) {
    await promptConfigure()
    return
  }
  if (!addresses.value.some((a) => a.url === proxyUrl.value)) {
    proxyUrl.value = addresses.value[0].url
  }
  cliCommands.value = []
  cliError.value = ''
  ideByUrl.value = {}
  ideError.value = ''
  visible.value = true
  await loadActiveTab()
}

async function copy(item: CommandItem) {
  try {
    await copyText(item.command)
    const prefix = tab.value === 'ide' ? 'Cursor IDE ' : ''
    ElMessage.success(`已复制${prefix}${item.title}`)
  } catch (err: any) {
    ElMessage.error(errorMessage(err, '复制失败'))
  }
}
</script>

<style scoped>
.copy-cmd-icon-btn {
  padding: 6px;
  margin: 0;
  vertical-align: middle;
}
.copy-cmd-icon-btn :deep(.el-icon) {
  font-size: var(--pulse-text-lg);
}
.addr-row {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 8px;
}
.addr-label {
  font-size: var(--pulse-text-sm);
  color: var(--pulse-text-muted);
}
.tab-body {
  min-height: 120px;
}
.tab-hint {
  margin: 0 0 12px;
  font-size: var(--pulse-text-sm);
  color: var(--pulse-text-muted);
}
.cmd-block + .cmd-block {
  margin-top: 16px;
}
.cmd-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
}
.cmd-title {
  font-weight: var(--pulse-font-medium);
}
.cmd-desc {
  margin: 0 0 6px;
  font-size: var(--pulse-text-sm);
  color: var(--pulse-text-muted);
}
.cmd-text {
  margin: 0;
  padding: 10px 12px;
  font-family: var(--el-font-family-mono, ui-monospace, SFMono-Regular, Menlo, Consolas, monospace);
  font-size: var(--pulse-text-sm);
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-all;
  background: var(--el-fill-color-light);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
}
</style>
