/** Display label for Member.channel / bot platform. */
export function channelLabel(channel: string | null | undefined): string {
  switch ((channel || '').trim().toLowerCase()) {
    case 'dingtalk':
      return '钉钉'
    case 'feishu':
      return '飞书'
    case 'wecom':
      return '企业微信'
    case 'web':
      return 'Web'
    case 'none':
    case '':
      return '无 IM'
    default:
      return channel || '—'
  }
}

export function channelMeta(
  channel: string | null | undefined,
  channelUserId: string | null | undefined,
): string {
  const id = (channelUserId || '').trim()
  const label = channelLabel(channel)
  return id ? `${label} · ${id}` : label
}
