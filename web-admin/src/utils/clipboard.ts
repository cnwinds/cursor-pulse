/** Copy text; falls back when Clipboard API is unavailable (HTTP / non-secure context). */
export async function copyText(text: string): Promise<void> {
  const value = text ?? ''
  if (!value) {
    throw new Error('没有可复制的内容')
  }

  if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(value)
      return
    } catch {
      // fall through to legacy path (common on http://LAN-IP)
    }
  }

  const ta = document.createElement('textarea')
  ta.value = value
  ta.setAttribute('readonly', '')
  ta.style.position = 'fixed'
  ta.style.left = '-9999px'
  ta.style.top = '0'
  document.body.appendChild(ta)
  ta.focus()
  ta.select()
  ta.setSelectionRange(0, ta.value.length)
  let ok = false
  try {
    ok = document.execCommand('copy')
  } finally {
    document.body.removeChild(ta)
  }
  if (!ok) {
    throw new Error('浏览器不允许复制，请手动选中文本')
  }
}
