<template>
  <div class="sessions-page" v-loading="loading">
    <header class="page-header">
      <div>
        <h2>会话账本</h2>
        <p class="desc">按用户聚合查看与小脉的对话记录，点击用户可浏览跨会话的连续消息流。</p>
      </div>
      <el-button @click="loadSessions">刷新</el-button>
    </header>

    <el-form :inline="true" class="filters">
      <el-form-item label="状态">
        <el-select v-model="filters.status" clearable placeholder="全部" style="width: 140px">
          <el-option label="open" value="open" />
          <el-option label="closed" value="closed" />
        </el-select>
      </el-form-item>
      <el-form-item v-if="canReadAll" label="用户">
        <el-select
          v-model="filters.memberUserId"
          filterable
          clearable
          placeholder="全部用户"
          style="width: 220px"
        >
          <el-option
            v-for="member in members"
            :key="member.dingtalk_user_id"
            :label="member.display_name"
            :value="member.dingtalk_user_id"
          />
        </el-select>
      </el-form-item>
      <el-form-item>
        <el-button type="primary" @click="loadSessions">查询</el-button>
      </el-form-item>
    </el-form>

    <el-table :data="userGroups" stripe @row-click="openUserTimeline">
      <el-table-column label="用户" min-width="180">
        <template #default="{ row }">
          <div class="user-name">{{ row.user_display_name }}</div>
          <div v-if="row.user_id !== row.user_display_name" class="user-id-muted">
            {{ row.user_id }}
          </div>
        </template>
      </el-table-column>
      <el-table-column prop="session_count" label="会话数" width="90" align="center" />
      <el-table-column label="进行中" width="90" align="center">
        <template #default="{ row }">
          <el-tag v-if="row.open_count" type="warning" size="small">{{ row.open_count }}</el-tag>
          <span v-else class="muted">0</span>
        </template>
      </el-table-column>
      <el-table-column prop="channel" label="渠道" width="100" />
      <el-table-column label="最近活动" min-width="180">
        <template #default="{ row }">{{ formatChinaTime(row.last_activity_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="120" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click.stop="openUserTimeline(row)">查看对话</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-drawer
      v-model="drawerOpen"
      :title="drawerTitle"
      size="640px"
      class="chat-drawer"
    >
      <div v-if="activeGroup" v-loading="timelineLoading" class="chat-panel">
        <div class="chat-summary">
          <div class="chat-summary-text">
            <span>{{ activeGroup.session_count }} 个会话</span>
            <span v-if="activeGroup.open_count">· {{ activeGroup.open_count }} 个进行中</span>
            <span>· 最近 {{ formatChinaTime(activeGroup.last_activity_at) }}</span>
          </div>
          <el-popover
            v-if="sessionCatalog.length"
            placement="bottom-end"
            :width="300"
            trigger="click"
          >
            <template #reference>
              <el-button :icon="List" circle size="small" title="会话目录" />
            </template>
            <div class="session-toc">
              <div class="session-toc-title">会话目录</div>
              <button
                v-for="session in sessionCatalog"
                :key="session.id"
                type="button"
                class="session-toc-item"
                @click="jumpToSession(session.id)"
              >
                <div class="session-toc-item-head">
                  <span>会话 {{ shortSessionId(session.id) }}</span>
                  <el-tag
                    :type="session.status === 'open' ? 'warning' : 'info'"
                    size="small"
                  >
                    {{ session.status }}
                  </el-tag>
                </div>
                <div class="session-toc-preview">
                  {{ sessionFirstUserPreview(session) }}
                </div>
                <div class="session-toc-time">
                  {{ formatChinaTime(session.opened_at) }} 打开
                </div>
                <div v-if="session.closed_at" class="session-toc-time muted">
                  {{ formatChinaTime(session.closed_at) }} 关闭
                </div>
              </button>
            </div>
          </el-popover>
        </div>

        <div ref="chatStreamRef" class="chat-stream" @scroll="onChatStreamScroll">
          <div v-if="prependLoading" class="chat-stream-prepend">加载更早会话…</div>
          <div v-else-if="hasOlderSessions" class="chat-stream-prepend muted">上滚加载更早会话</div>
          <template v-for="(item, index) in streamItems" :key="streamItemKey(item, index)">
            <div
              v-if="item.kind === 'divider'"
              :id="sessionAnchorId(item.session.id)"
              class="session-divider"
            >
              <span class="divider-line" />
              <div class="divider-card">
                <div class="divider-title">
                  会话 {{ shortSessionId(item.session.id) }}
                  <el-tag :type="item.session.status === 'open' ? 'warning' : 'info'" size="small">
                    {{ item.session.status }}
                  </el-tag>
                </div>
                <div class="divider-meta">
                  {{ formatChinaTime(item.session.opened_at) }} 打开
                  <template v-if="item.session.closed_at">
                    · {{ formatChinaTime(item.session.closed_at) }} 关闭
                  </template>
                </div>
                <div class="divider-actions">
                  <el-button
                    v-if="item.session.status === 'open'"
                    link
                    type="warning"
                    size="small"
                    @click="closeSession(item.session)"
                  >
                    关闭会话
                  </el-button>
                  <el-button link size="small" @click="exportSession(item.session)">导出</el-button>
                </div>
              </div>
              <span class="divider-line" />
            </div>

            <div
              v-else
              class="chat-row"
              :class="messageRowClass(item.message)"
            >
              <div class="bubble" :class="bubbleClass(item.message)">
                <template v-if="isToolMessage(item.message)">
                  <details class="tool-details">
                    <summary class="tool-summary">
                      <span class="tool-summary-main">{{ messageRoleLabel(item.message) }}</span>
                      <span class="tool-summary-hint">参数与返回</span>
                      <span class="tool-summary-time">{{ formatChinaTime(item.message.created_at) }}</span>
                    </summary>
                    <div class="tool-meta">
                      <div class="tool-block">
                        <div class="tool-label">参数</div>
                        <pre class="tool-json">{{ formatToolArgs(item.message) }}</pre>
                      </div>
                      <div class="tool-block">
                        <div class="tool-label">返回</div>
                        <pre class="tool-json">{{ formatToolResult(item.message) }}</pre>
                      </div>
                    </div>
                  </details>
                </template>
                <template v-else-if="isContextMessage(item.message)">
                  <details class="tool-details">
                    <summary class="tool-summary">
                      <span class="tool-summary-main">上下文 · {{ contextSummary(item.message) }}</span>
                      <span class="tool-summary-time">{{ formatChinaTime(item.message.created_at) }}</span>
                    </summary>
                    <div class="tool-meta">
                      <div class="tool-block">
                        <div class="tool-label">技能名片</div>
                        <pre class="tool-json">{{ formatContextSkills(item.message) }}</pre>
                      </div>
                      <div class="tool-block">
                        <div class="tool-label">可用工具</div>
                        <pre class="tool-json">{{ formatContextTools(item.message) }}</pre>
                      </div>
                    </div>
                  </details>
                </template>
                <template v-else>
                  <div class="bubble-role">
                    {{ messageRoleLabel(item.message) }}
                  </div>
                  <div
                    class="bubble-text bubble-markdown"
                    v-html="renderMessageHtml(item.message.text_redacted)"
                  />
                  <div class="bubble-time">{{ formatChinaTime(item.message.created_at) }}</div>
                </template>
              </div>
            </div>
          </template>

          <el-empty v-if="!streamItems.length && !timelineLoading" description="暂无消息" />
        </div>
      </div>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { List } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import client from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { formatChinaTime, parseApiDateTime } from '@/utils/time'
import { renderMarkdown } from '@/utils/markdown'

interface SessionRow {
  id: string
  channel: string
  conversation_type: string
  user_id?: string
  user_display_name?: string
  status: string
  opened_at?: string
  last_activity_at?: string
  closed_at?: string
  first_user_text?: string | null
}

interface MemberOption {
  display_name: string
  dingtalk_user_id: string
}

interface SessionMessage {
  id: string
  role: string
  text_redacted: string
  meta_json?: Record<string, unknown>
  created_at?: string
}

interface SessionDetail extends SessionRow {
  messages?: SessionMessage[]
}

interface UserGroup {
  user_id: string
  user_display_name: string
  channel: string
  session_count: number
  open_count: number
  last_activity_at?: string
  sessions: SessionRow[]
}

type StreamItem =
  | { kind: 'divider'; session: SessionDetail }
  | { kind: 'message'; message: SessionMessage; sessionId: string }

const auth = useAuthStore()
const loading = ref(false)
const timelineLoading = ref(false)
const prependLoading = ref(false)
const sessions = ref<SessionRow[]>([])
const members = ref<MemberOption[]>([])
const drawerOpen = ref(false)
const activeGroup = ref<UserGroup | null>(null)
const streamItems = ref<StreamItem[]>([])
const sessionCatalog = ref<SessionRow[]>([])
const loadedDetails = ref<Map<string, SessionDetail>>(new Map())
const oldestLoadedIndex = ref(0)
const chatStreamRef = ref<HTMLElement | null>(null)

const INITIAL_SESSION_BATCH = 2
const SCROLL_SESSION_BATCH = 2
const SCROLL_TOP_THRESHOLD = 40

const filters = reactive({
  status: '',
  memberUserId: '',
})

const canReadAll = computed(() => auth.hasPermission('assistant:sessions:read:all'))

const hasOlderSessions = computed(
  () => sessionCatalog.value.length > 0 && oldestLoadedIndex.value > 0,
)

const userGroups = computed<UserGroup[]>(() => {
  const grouped = new Map<string, UserGroup>()
  for (const session of sessions.value) {
    const userId = session.user_id || 'unknown'
    const displayName = session.user_display_name || userId
    const existing = grouped.get(userId)
    if (!existing) {
      grouped.set(userId, {
        user_id: userId,
        user_display_name: displayName,
        channel: session.channel,
        session_count: 1,
        open_count: session.status === 'open' ? 1 : 0,
        last_activity_at: session.last_activity_at,
        sessions: [session],
      })
      continue
    }
    existing.sessions.push(session)
    existing.session_count += 1
    if (session.status === 'open') existing.open_count += 1
    if (isAfter(session.last_activity_at, existing.last_activity_at)) {
      existing.last_activity_at = session.last_activity_at
    }
  }
  return Array.from(grouped.values()).sort((a, b) =>
    compareTimeDesc(b.last_activity_at, a.last_activity_at),
  )
})

const drawerTitle = computed(() =>
  activeGroup.value ? `${activeGroup.value.user_display_name} 的对话` : '对话记录',
)

function isAfter(left?: string, right?: string): boolean {
  const l = parseApiDateTime(left)
  const r = parseApiDateTime(right)
  if (!l) return false
  if (!r) return true
  return l.getTime() > r.getTime()
}

function compareTimeAsc(left?: string, right?: string): number {
  const l = parseApiDateTime(left)?.getTime() ?? 0
  const r = parseApiDateTime(right)?.getTime() ?? 0
  return l - r
}

function compareTimeDesc(left?: string, right?: string): number {
  return -compareTimeAsc(left, right)
}

function renderMessageHtml(text: string): string {
  return renderMarkdown(text)
}

function messageKind(message: SessionMessage): string {
  const meta = message.meta_json || {}
  return String(meta.kind || '').toLowerCase()
}

function isToolMessage(message: SessionMessage): boolean {
  return message.role === 'tool' || messageKind(message) === 'tool'
}

function isThinkingMessage(message: SessionMessage): boolean {
  return messageKind(message) === 'thinking'
}

function isContextMessage(message: SessionMessage): boolean {
  return messageKind(message) === 'context'
}

function messageRowClass(message: SessionMessage): string {
  if (message.role === 'user') return 'is-user'
  if (isToolMessage(message) || isThinkingMessage(message) || isContextMessage(message)) {
    return 'is-trace'
  }
  return 'is-assistant'
}

function bubbleClass(message: SessionMessage): string {
  if (isToolMessage(message)) return 'is-tool'
  if (isContextMessage(message)) return 'is-context'
  if (isThinkingMessage(message)) return 'is-thinking'
  if (messageKind(message) === 'interim') return 'is-interim'
  return ''
}

function messageRoleLabel(message: SessionMessage): string {
  if (message.role === 'user') return '用户'
  if (isToolMessage(message)) {
    const name = String((message.meta_json || {}).name || 'tool')
    return `工具 · ${name}`
  }
  if (isContextMessage(message)) return '上下文'
  if (isThinkingMessage(message)) return '思考'
  if (messageKind(message) === 'interim') return '小脉 · 进度'
  return '小脉'
}

function prettyJson(value: unknown): string {
  if (value == null || value === '') return '—'
  if (typeof value === 'string') {
    try {
      return JSON.stringify(JSON.parse(value), null, 2)
    } catch {
      return value
    }
  }
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

function formatToolArgs(message: SessionMessage): string {
  return prettyJson((message.meta_json || {}).arguments)
}

function formatToolResult(message: SessionMessage): string {
  return prettyJson(message.text_redacted)
}

function contextSummary(message: SessionMessage): string {
  const meta = message.meta_json || {}
  const skills = Array.isArray(meta.skills) ? meta.skills : []
  const tools = Array.isArray(meta.tools) ? meta.tools : []
  return `技能 ${skills.length} · 工具 ${tools.length}`
}

function formatContextSkills(message: SessionMessage): string {
  const skills = (message.meta_json || {}).skills
  if (!Array.isArray(skills) || !skills.length) return '（未注入技能名片）'
  return skills
    .map((item) => {
      if (!item || typeof item !== 'object') return String(item)
      const row = item as Record<string, unknown>
      const id = String(row.skill_id || '')
      const name = String(row.name || '')
      const summary = String(row.summary || '')
      const header = id && name ? `${name} (${id})` : id || name
      const total = row.total_lines
      const loaded = row.loaded_lines
      const linesMeta =
        typeof total === 'number' && typeof loaded === 'number'
          ? `已载入 ${loaded}/${total} 行`
          : ''
      return [header, summary, linesMeta].filter(Boolean).join('\n  ')
    })
    .join('\n\n')
}

function formatContextTools(message: SessionMessage): string {
  const tools = (message.meta_json || {}).tools
  if (!Array.isArray(tools) || !tools.length) return '（无可用工具）'
  return tools
    .map((item) => {
      if (!item || typeof item !== 'object') return String(item)
      const row = item as Record<string, unknown>
      const name = String(row.name || '')
      const display = String(row.display_name || '')
      return display && display !== name ? `${name} — ${display}` : name
    })
    .filter(Boolean)
    .join('\n')
}

function shortSessionId(id: string): string {
  return id.slice(0, 8)
}

function sessionAnchorId(sessionId: string): string {
  return `session-anchor-${sessionId}`
}

async function jumpToSession(sessionId: string) {
  if (!loadedDetails.value.has(sessionId)) {
    await ensureSessionsLoadedThrough(sessionId)
  }
  await nextTick()
  const container = chatStreamRef.value
  const target = container?.querySelector<HTMLElement>(`#${sessionAnchorId(sessionId)}`)
  if (!target || !container) return
  const top =
    target.getBoundingClientRect().top -
    container.getBoundingClientRect().top +
    container.scrollTop -
    8
  container.scrollTo({ top: Math.max(top, 0), behavior: 'smooth' })
}

function streamItemKey(item: StreamItem, index: number): string {
  if (item.kind === 'divider') return `divider-${item.session.id}`
  return `msg-${item.sessionId}-${item.message.id}-${index}`
}

function sessionFirstUserPreview(session: SessionRow): string {
  const text = (session.first_user_text || '').trim()
  return text || '（暂无用户消息）'
}

function sessionSortKey(session: SessionRow): string | undefined {
  return session.opened_at || session.last_activity_at
}

function rebuildStreamItems() {
  const items: StreamItem[] = []
  for (let i = oldestLoadedIndex.value; i < sessionCatalog.value.length; i += 1) {
    const meta = sessionCatalog.value[i]
    const detail = loadedDetails.value.get(meta.id)
    if (!detail) continue
    items.push({ kind: 'divider', session: detail })
    const messages = [...(detail.messages || [])].sort((a, b) =>
      compareTimeAsc(a.created_at, b.created_at),
    )
    for (const message of messages) {
      items.push({ kind: 'message', message, sessionId: detail.id })
    }
  }
  streamItems.value = items
}

async function fetchSessionDetail(sessionId: string): Promise<SessionDetail> {
  const cached = loadedDetails.value.get(sessionId)
  if (cached) return cached
  const { data } = await client.get(`/api/v2/assistant/sessions/${sessionId}`)
  const detail = data as SessionDetail
  loadedDetails.value.set(sessionId, detail)
  return detail
}

async function loadSessionDetails(sessionIds: string[]) {
  const missing = sessionIds.filter((id) => !loadedDetails.value.has(id))
  if (!missing.length) return
  await Promise.all(missing.map((id) => fetchSessionDetail(id)))
}

async function ensureSessionsLoadedThrough(targetSessionId: string) {
  const targetIndex = sessionCatalog.value.findIndex((s) => s.id === targetSessionId)
  if (targetIndex < 0) return
  if (targetIndex >= oldestLoadedIndex.value) {
    // Already in the loaded window range (may still need detail if race).
    await loadSessionDetails(
      sessionCatalog.value.slice(oldestLoadedIndex.value).map((s) => s.id),
    )
    rebuildStreamItems()
    return
  }
  const idsToLoad = sessionCatalog.value
    .slice(targetIndex, oldestLoadedIndex.value)
    .map((s) => s.id)
  prependLoading.value = true
  try {
    await loadSessionDetails(idsToLoad)
    oldestLoadedIndex.value = targetIndex
    rebuildStreamItems()
  } catch {
    ElMessage.error('加载对话记录失败')
  } finally {
    prependLoading.value = false
  }
}

async function loadOlderSessions() {
  if (prependLoading.value || timelineLoading.value || !hasOlderSessions.value) return
  const nextOldest = Math.max(0, oldestLoadedIndex.value - SCROLL_SESSION_BATCH)
  const ids = sessionCatalog.value
    .slice(nextOldest, oldestLoadedIndex.value)
    .map((s) => s.id)
  if (!ids.length) return

  const container = chatStreamRef.value
  const prevHeight = container?.scrollHeight ?? 0
  const prevTop = container?.scrollTop ?? 0

  prependLoading.value = true
  try {
    await loadSessionDetails(ids)
    oldestLoadedIndex.value = nextOldest
    rebuildStreamItems()
    await nextTick()
    if (container) {
      const delta = container.scrollHeight - prevHeight
      container.scrollTop = prevTop + delta
    }
  } catch {
    ElMessage.error('加载更早会话失败')
  } finally {
    prependLoading.value = false
  }
}

function onChatStreamScroll() {
  const el = chatStreamRef.value
  if (!el) return
  if (el.scrollTop <= SCROLL_TOP_THRESHOLD) {
    void loadOlderSessions()
  }
}

async function loadMembers() {
  if (!canReadAll.value) return
  const { data } = await client.get('/api/v2/members')
  members.value = data
}

async function fetchSessionsForUser(userId: string): Promise<SessionRow[]> {
  const params: Record<string, string | number> = { limit: 100, offset: 0 }
  if (filters.status) params.status = filters.status
  if (canReadAll.value) {
    params.member_user_id = userId
  }
  const { data } = await client.get('/api/v2/assistant/sessions', { params })
  return data.items || []
}

async function loadSessions() {
  loading.value = true
  try {
    const params: Record<string, string | number> = { limit: 100, offset: 0 }
    if (filters.status) params.status = filters.status
    if (canReadAll.value && filters.memberUserId) {
      params.member_user_id = filters.memberUserId
    }
    const { data } = await client.get('/api/v2/assistant/sessions', { params })
    sessions.value = data.items || []
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : '加载失败'
    ElMessage.error(message)
  } finally {
    loading.value = false
  }
}

async function buildStreamForGroup(group: UserGroup) {
  timelineLoading.value = true
  streamItems.value = []
  loadedDetails.value = new Map()
  oldestLoadedIndex.value = 0
  sessionCatalog.value = []
  try {
    const userSessions = await fetchSessionsForUser(group.user_id)
    const sortedSessions = [...userSessions].sort((a, b) =>
      compareTimeAsc(sessionSortKey(a), sessionSortKey(b)),
    )
    sessionCatalog.value = sortedSessions
    if (!sortedSessions.length) return

    const start = Math.max(0, sortedSessions.length - INITIAL_SESSION_BATCH)
    oldestLoadedIndex.value = start
    await loadSessionDetails(sortedSessions.slice(start).map((s) => s.id))
    rebuildStreamItems()
    await nextTick()
    if (chatStreamRef.value) {
      chatStreamRef.value.scrollTop = chatStreamRef.value.scrollHeight
    }
  } catch {
    ElMessage.error('加载对话记录失败')
  } finally {
    timelineLoading.value = false
  }
}

async function openUserTimeline(group: UserGroup) {
  activeGroup.value = group
  sessionCatalog.value = []
  drawerOpen.value = true
  await buildStreamForGroup(group)
}

async function closeSession(session: SessionRow) {
  try {
    await ElMessageBox.confirm('确认关闭该会话？', '关闭会话')
    await client.post(`/api/v2/assistant/sessions/${session.id}/close`, { reason: 'manual' })
    ElMessage.success('已关闭')
    await loadSessions()
    if (activeGroup.value) {
      const refreshed = userGroups.value.find((g) => g.user_id === activeGroup.value?.user_id)
      if (refreshed) {
        activeGroup.value = refreshed
        await buildStreamForGroup(refreshed)
      }
    }
  } catch {
    /* cancelled or failed */
  }
}

async function exportSession(session: SessionRow) {
  try {
    const { data } = await client.get(`/api/v2/assistant/sessions/${session.id}/export`)
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `session-${session.id}.json`
    link.click()
    URL.revokeObjectURL(url)
  } catch {
    ElMessage.error('导出失败')
  }
}

watch(drawerOpen, (open) => {
  if (!open) {
    prependLoading.value = false
  }
})

onMounted(async () => {
  await Promise.all([loadMembers(), loadSessions()])
})
</script>

<style scoped>
.sessions-page {
  padding: 8px;
}
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 16px;
}
.desc {
  color: #64748b;
  margin: 4px 0 0;
  max-width: 720px;
  line-height: 1.5;
}
.filters {
  margin-bottom: 12px;
}
.user-name {
  font-weight: 500;
}
.user-id-muted,
.muted {
  font-size: 12px;
  color: #94a3b8;
}
.chat-panel {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 120px);
}
.chat-summary {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  font-size: 13px;
  color: #64748b;
  margin-bottom: 12px;
  padding-bottom: 12px;
  border-bottom: 1px solid #e2e8f0;
}
.chat-summary-text {
  flex: 1;
  line-height: 1.6;
}
.session-toc-title {
  font-size: 13px;
  font-weight: 600;
  color: #334155;
  margin-bottom: 8px;
}
.session-toc {
  max-height: 320px;
  overflow-y: auto;
}
.session-toc-item {
  display: block;
  width: 100%;
  margin: 0 0 8px;
  padding: 10px 12px;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  background: #fff;
  text-align: left;
  cursor: pointer;
  transition: background 0.15s ease, border-color 0.15s ease;
}
.session-toc-item:last-child {
  margin-bottom: 0;
}
.session-toc-item:hover {
  background: #f8fafc;
  border-color: #cbd5e1;
}
.session-toc-item-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  font-size: 13px;
  font-weight: 500;
  color: #0f172a;
}
.session-toc-preview {
  margin-top: 6px;
  font-size: 12px;
  line-height: 1.45;
  color: #334155;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  word-break: break-word;
}
.session-toc-time {
  margin-top: 4px;
  font-size: 12px;
  color: #64748b;
}
.chat-stream-prepend {
  text-align: center;
  font-size: 12px;
  color: #64748b;
  padding: 8px 0 12px;
}
.chat-stream {
  flex: 1;
  overflow-y: auto;
  padding: 8px 4px 24px;
  background: linear-gradient(180deg, #f8fafc 0%, #f1f5f9 100%);
  border-radius: 12px;
}
.session-divider {
  display: flex;
  align-items: center;
  gap: 12px;
  margin: 20px 0 16px;
}
.divider-line {
  flex: 1;
  height: 1px;
  background: #cbd5e1;
}
.divider-card {
  flex-shrink: 0;
  max-width: 320px;
  padding: 10px 14px;
  border-radius: 10px;
  background: #fff;
  border: 1px dashed #cbd5e1;
  text-align: center;
}
.divider-title {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  font-size: 13px;
  font-weight: 600;
  color: #334155;
}
.divider-meta {
  margin-top: 4px;
  font-size: 12px;
  color: #64748b;
}
.divider-actions {
  margin-top: 8px;
  display: flex;
  justify-content: center;
  gap: 8px;
}
.chat-row {
  display: flex;
  margin-bottom: 12px;
}
.chat-row.is-user {
  justify-content: flex-end;
}
.chat-row.is-assistant {
  justify-content: flex-start;
}
.chat-row.is-trace {
  justify-content: flex-start;
  margin-bottom: 6px;
}
.bubble {
  max-width: 82%;
  padding: 10px 12px;
  border-radius: 12px;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.06);
}
.is-user .bubble {
  background: #dbeafe;
  border-bottom-right-radius: 4px;
}
.is-assistant .bubble {
  background: #fff;
  border-bottom-left-radius: 4px;
}
.bubble.is-tool,
.bubble.is-context {
  max-width: 92%;
  padding: 2px 10px;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-bottom-left-radius: 4px;
  box-shadow: none;
}
.bubble.is-tool:has(.tool-details[open]),
.bubble.is-context:has(.tool-details[open]) {
  padding: 8px 10px;
}
.bubble.is-thinking {
  background: #f1f5f9;
  border: 1px dashed #cbd5e1;
  border-bottom-left-radius: 4px;
  opacity: 0.95;
}
.bubble.is-interim {
  background: #fefce8;
  border-bottom-left-radius: 4px;
}
.tool-details {
  margin: 0;
}
.tool-summary {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 24px;
  line-height: 24px;
  cursor: pointer;
  user-select: none;
  font-size: 12px;
  color: #64748b;
  list-style: none;
  white-space: nowrap;
  overflow: hidden;
}
.tool-summary::-webkit-details-marker {
  display: none;
}
.tool-summary::before {
  content: '▸';
  flex-shrink: 0;
  width: 12px;
  text-align: center;
  transition: transform 0.12s ease;
}
.tool-details[open] > .tool-summary::before {
  transform: rotate(90deg);
}
.tool-details[open] > .tool-summary {
  margin-bottom: 8px;
  white-space: normal;
}
.tool-summary-main {
  color: #475569;
  overflow: hidden;
  text-overflow: ellipsis;
}
.tool-summary-hint {
  flex-shrink: 0;
  color: #94a3b8;
}
.tool-summary-time {
  margin-left: auto;
  flex-shrink: 0;
  font-size: 11px;
  color: #94a3b8;
  line-height: 24px;
}
.tool-meta {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.tool-block {
  min-width: 0;
}
.tool-label {
  font-size: 11px;
  font-weight: 600;
  color: #64748b;
  margin-bottom: 4px;
}
.tool-json {
  margin: 0;
  padding: 8px;
  border-radius: 8px;
  background: #0f172a;
  color: #e2e8f0;
  font-size: 12px;
  line-height: 1.45;
  overflow-x: auto;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 320px;
  overflow-y: auto;
}
.bubble-role {
  font-size: 11px;
  color: #64748b;
  margin-bottom: 4px;
}
.bubble-text {
  word-break: break-word;
  font-size: 14px;
  line-height: 1.5;
  color: #0f172a;
}
.bubble-markdown :deep(p) {
  margin: 0 0 8px;
}
.bubble-markdown :deep(p:last-child) {
  margin-bottom: 0;
}
.bubble-markdown :deep(h3),
.bubble-markdown :deep(h4) {
  margin: 0 0 8px;
  font-size: 15px;
  line-height: 1.4;
}
.bubble-markdown :deep(strong) {
  font-weight: 600;
}
.bubble-markdown :deep(table) {
  width: 100%;
  border-collapse: collapse;
  margin: 8px 0;
  font-size: 13px;
}
.bubble-markdown :deep(th),
.bubble-markdown :deep(td) {
  border: 1px solid #cbd5e1;
  padding: 6px 8px;
  text-align: left;
}
.bubble-markdown :deep(th) {
  background: #f8fafc;
}
.bubble-markdown :deep(hr) {
  border: none;
  border-top: 1px solid #e2e8f0;
  margin: 12px 0;
}
.bubble-markdown :deep(ul),
.bubble-markdown :deep(ol) {
  margin: 0 0 8px;
  padding-left: 20px;
}
.bubble-markdown :deep(code) {
  font-family: ui-monospace, monospace;
  font-size: 12px;
  background: #f1f5f9;
  padding: 1px 4px;
  border-radius: 4px;
}
.bubble-markdown :deep(pre) {
  margin: 8px 0;
  padding: 10px;
  overflow-x: auto;
  background: #f8fafc;
  border-radius: 8px;
}
.bubble-markdown :deep(pre code) {
  background: transparent;
  padding: 0;
}
.bubble-time {
  margin-top: 6px;
  font-size: 11px;
  color: #94a3b8;
  text-align: right;
}
</style>
