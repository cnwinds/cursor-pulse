import { ElMessage, ElMessageBox } from 'element-plus'
import client from '@/api/client'
import { copyText } from '@/utils/clipboard'

/** 重置 proxy_alias 借用的 IDE 密钥，确认后调用 rotate 并复制新接入命令。 */
export async function rotateLoanIdeKey(
  loanId: string,
  setBusy?: (busy: boolean) => void,
): Promise<void> {
  try {
    await ElMessageBox.confirm(
      '重置后旧的 IDE 密钥会立即失效，所有已接入的电脑都无法继续计费；每台电脑都必须重新运行新的接入命令。确认继续？',
      '重置 IDE 密钥',
      {
        type: 'warning',
        confirmButtonText: '重置并复制新命令',
        cancelButtonText: '取消',
      },
    )
  } catch {
    return
  }

  setBusy?.(true)
  try {
    const res = await client.post(`/api/v2/loans/${loanId}/ide-key/rotate`)
    const command = res.data?.command
    if (!command) {
      ElMessage.error('未返回命令')
      return
    }
    await copyText(command)
    const label = res.data?.proxy_url || ''
    const suffix = label
      ? `（${label}；其他代理地址可用「复制命令」下拉复制同一密钥）`
      : '；其他代理地址可用「复制命令」下拉复制同一密钥'
    ElMessage.success(`已重置 IDE 密钥并复制 Cursor IDE 接入命令${suffix}`)
  } catch (err: any) {
    const detail = err?.response?.data?.detail
    ElMessage.error(typeof detail === 'string' ? detail : err?.message || '重置失败')
  } finally {
    setBusy?.(false)
  }
}
