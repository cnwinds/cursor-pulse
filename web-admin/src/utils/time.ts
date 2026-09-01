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

/**
 * 解析额度重置时刻：优先精确 billingCycleEnd；无则 cycle_end 当日 UTC 23:59:59
 * （与后端 hours_until_deadline 回退一致）。
 */
export function resolveResetInstant(
  cycleEndAt: string | null | undefined,
  cycleEnd: string | null | undefined,
): Date | null {
  const exact = parseApiDateTime(cycleEndAt)
  if (exact) return exact
  if (!cycleEnd) return null
  return parseApiDateTime(`${cycleEnd.trim()}T23:59:59Z`)
}

/**
 * 距下次额度重置的自适应文案：≥1 天用「N天后」；否则「N小时后」/「N分钟后」。
 */
export function formatResetCountdown(
  cycleEndAt: string | null | undefined,
  cycleEnd: string | null | undefined,
  now: Date = new Date(),
): string {
  const end = resolveResetInstant(cycleEndAt, cycleEnd)
  if (!end) return '—'

  const ms = end.getTime() - now.getTime()
  if (ms <= 0) return '即将重置'

  const totalMinutes = Math.floor(ms / 60_000)
  const totalHours = Math.floor(ms / 3_600_000)
  const totalDays = Math.floor(ms / 86_400_000)

  if (totalDays >= 1) return `${totalDays}天后重置`
  if (totalHours >= 1) return `${totalHours}小时后重置`
  return `${Math.max(totalMinutes, 1)}分钟后重置`
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
