/** 中国时区（Asia/Shanghai，UTC+8） */

const HAS_TZ = /([zZ]|[+-]\d{2}:?\d{2})$/

/**
 * 将后端时间解析为 Date。
 * SQLite / SQLAlchemy 常返回无时区的 UTC（如 `2026-07-14 10:10:37`），
 * 浏览器会误当成本地时间；此处无偏移量时一律按 UTC 解析。
 */
export function parseApiDateTime(iso: string | null | undefined): Date | null {
  if (!iso) return null
  const trimmed = iso.trim()
  if (!trimmed) return null

  let normalized = trimmed.includes('T') ? trimmed : trimmed.replace(' ', 'T')
  if (!HAS_TZ.test(normalized)) {
    normalized = `${normalized}Z`
  }

  const date = new Date(normalized)
  if (Number.isNaN(date.getTime())) return null
  return date
}

/** 将 ISO / SQLite 时间格式化为中国时区，精确到秒 */
export function formatChinaTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const date = parseApiDateTime(iso)
  if (!date) return iso

  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).formatToParts(date)

  const get = (type: Intl.DateTimeFormatPartTypes) =>
    parts.find((p) => p.type === type)?.value ?? ''

  return `${get('year')}-${get('month')}-${get('day')} ${get('hour')}:${get('minute')}:${get('second')}`
}

/** 毫秒差值格式化为中文时长，如「2天3小时15分」 */
export function formatDurationMs(ms: number): string {
  const safeMs = Math.max(ms, 0)
  const totalMinutes = Math.floor(safeMs / 60_000)
  const days = Math.floor(totalMinutes / (60 * 24))
  const hours = Math.floor((totalMinutes % (60 * 24)) / 60)
  const minutes = totalMinutes % 60

  const parts: string[] = []
  if (days > 0) parts.push(`${days}天`)
  if (hours > 0) parts.push(`${hours}小时`)
  if (minutes > 0 || parts.length === 0) parts.push(`${minutes}分`)
  return parts.join('')
}

/** 借用时长：未归还时计至当前时刻 */
export function formatLoanDuration(
  createdAt: string | null | undefined,
  revokedAt: string | null | undefined,
): string {
  const start = parseApiDateTime(createdAt)
  if (!start) return '—'
  const end = revokedAt ? parseApiDateTime(revokedAt) : new Date()
  if (!end) return '—'
  return formatDurationMs(end.getTime() - start.getTime())
}
