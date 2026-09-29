<template>
  <div class="chat-fab">
    <el-button type="primary" circle size="large" @click="open = true">
      <el-icon :size="22"><ChatDotRound /></el-icon>
    </el-button>

    <el-drawer v-model="open" title="小脉" direction="rtl" size="600px" :with-header="true">
      <div class="chat-body" ref="scrollRef">
        <div
          v-for="(msg, i) in messages"
          :key="i"
          class="bubble-row"
          :class="[msg.role, msg.kind === 'interim' ? 'interim' : '']"
        >
          <div class="bubble">
            <div class="meta">
              {{ msg.role === 'user' ? '我' : '小脉' }}
              <span v-if="msg.createdAt" class="time">{{ formatMsgTime(msg.createdAt) }}</span>
            </div>
            <div v-if="msg.role === 'user'" class="text">{{ msg.text }}</div>
            <div v-else class="text md" v-html="renderMarkdown(msg.text)" />
            <div v-if="msg.actions?.length" class="actions">
              <el-tag
                v-for="(a, j) in msg.actions"
                :key="j"
                size="small"
                :type="a.status === 'executed' ? 'success' : a.status === 'denied' ? 'danger' : 'info'"
              >
                {{ a.tool }}: {{ a.status }}
              </el-tag>
            </div>
          </div>
        </div>
        <div v-if="draft" class="bubble-row assistant streaming">
          <div class="bubble">
            <div class="meta">小脉 · 输入中</div>
            <div class="text md" v-html="renderMarkdown(draft)" />
          </div>
        </div>
        <div v-else-if="loading" class="typing">小脉正在想…</div>
      </div>
      <div class="chat-input">
        <el-input
          v-model="input"
          type="textarea"
          :rows="3"
          resize="none"
          placeholder="跟小脉说点什么，例如：谁还没交？催一下没交的"
          @keydown.enter.exact.prevent="send"
        />
        <el-button
          class="send-fab"
          type="primary"
          circle
          :loading="loading"
          :disabled="!input.trim()"
          title="发送（Enter）"
          @click="send"
        >
          <el-icon v-if="!loading"><Promotion /></el-icon>
        </el-button>
      </div>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { nextTick, onUnmounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import client from '@/api/client'
import { renderMarkdown } from '@/utils/markdown'
import {
  calendarDateInTimeZone,
  DEFAULT_DISPLAY_TIMEZONE,
  formatChinaDateTimeParts,
  formatYmd,
  parseApiDateTime,
} from '@/utils/time'

interface ChatMsg {
  role: 'user' | 'assistant'
  text: string
  kind?: 'interim' | 'final'
  createdAt?: string
  actions?: Array<{ tool: string; status: string; message?: string }>
}

interface DeliveryItem {
  id: number
  text: string
  kind: string
  created_at?: string
}

const WELCOME: ChatMsg = {
  role: 'assistant',
  text: '嗨，我是小脉。可以问我团队用量、提交进度；有权限的话也能让我催办、聚合或发月报。',
  kind: 'final',
}
const PENDING_TURN_MAX_AGE_MS = 5 * 60 * 1000

const open = ref(false)
const input = ref('')
const loading = ref(false)
const messages = ref<ChatMsg[]>([WELCOME])
let historyLoaded = false

function toChatMsg(item: DeliveryItem): ChatMsg {
  if (item.kind === 'user') return { role: 'user', text: item.text, createdAt: item.created_at }
  return {
    role: 'assistant',
    text: item.text,
    kind: item.kind === 'interim' ? 'interim' : 'final',
    createdAt: item.created_at,
  }
}

function formatMsgTime(iso: string): string {
  const parts = formatChinaDateTimeParts(iso)
  if (!parts) return ''
  const hm = parts.time.slice(0, 5)
  const today = calendarDateInTimeZone(DEFAULT_DISPLAY_TIMEZONE)
  if (parts.date === formatYmd(today.year, today.month, today.day)) return hm
  return `${parts.date.slice(5)} ${hm}`
}
const scrollRef = ref<HTMLElement | null>(null)
const pollAfter = ref(0)
const draft = ref('')
let pollTimer: ReturnType<typeof setInterval> | null = null
let pollInFlight = false

async function scrollBottom() {
  await nextTick()
  const el = scrollRef.value
  if (el) el.scrollTop = el.scrollHeight
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
  draft.value = ''
}

async function pollMessages() {
  if (pollInFlight) return
  pollInFlight = true
  try {
    const { data } = await client.get('/api/chat/messages', {
      params: { after: pollAfter.value },
    })
    if (!pollTimer) return
    const items = data.items || []
    const streams: Array<{ text: string }> = data.streams || []
    const prevDraft = draft.value
    draft.value = streams.length ? streams[streams.length - 1].text : ''
    for (const item of items as DeliveryItem[]) {
      pollAfter.value = Math.max(pollAfter.value, item.id)
      messages.value.push(toChatMsg(item))
      if (item.kind === 'final') {
        loading.value = false
        stopPolling()
        break
      }
    }
    if (items.length || draft.value !== prevDraft) {
      await scrollBottom()
    }
  } catch {
    /* keep polling until final or timeout */
  } finally {
    pollInFlight = false
  }
}

function startPolling(fromId = 0) {
  stopPolling()
  pollAfter.value = fromId
  pollTimer = setInterval(pollMessages, 400)
  void pollMessages()
}

async function loadHistory() {
  try {
    const { data } = await client.get('/api/chat/history')
    historyLoaded = true
    const items: DeliveryItem[] = data.items || []
    messages.value = [WELCOME, ...items.map(toChatMsg), ...messages.value.slice(1)]
    const last = items[items.length - 1]
    const turnPending =
      (data.streams || []).length > 0 ||
      (!!last &&
        last.kind !== 'final' &&
        Date.now() - (parseApiDateTime(last.created_at)?.getTime() ?? 0) < PENDING_TURN_MAX_AGE_MS)
    if (turnPending && !loading.value) {
      loading.value = true
      startPolling(data.last_id || 0)
    }
    await scrollBottom()
  } catch {
    /* keep the welcome message; retry on next open */
  }
}

watch(open, (visible) => {
  if (visible && !historyLoaded) void loadHistory()
})

onUnmounted(stopPolling)

async function send() {
  const text = input.value.trim()
  if (!text || loading.value) return
  messages.value.push({ role: 'user', text, createdAt: new Date().toISOString() })
  input.value = ''
  loading.value = true
  await scrollBottom()
  try {
    const { data } = await client.post('/api/chat', { message: text })
    const fromId = typeof data.poll_after === 'number' ? data.poll_after : 0
    startPolling(fromId)
    if (data.reply && data.status !== 'accepted') {
      messages.value.push({
        role: 'assistant',
        text: data.reply,
        kind: 'final',
        createdAt: new Date().toISOString(),
      })
      loading.value = false
      stopPolling()
    }
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '发送失败')
    loading.value = false
    stopPolling()
  } finally {
    await scrollBottom()
  }
}
</script>

<style scoped>
.chat-fab {
  position: fixed;
  right: 24px;
  bottom: 24px;
  z-index: 2000;
}

.chat-fab :deep(.el-button--primary) {
  box-shadow: var(--pulse-shadow-fab);
}
.chat-body {
  height: calc(100vh - 200px);
  overflow-y: auto;
  padding: 8px 4px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.bubble-row.user {
  align-self: flex-end;
}
.bubble-row.assistant {
  align-self: flex-start;
}
.bubble-row.interim .bubble {
  background: var(--pulse-bg-inset);
  border: 1px dashed var(--pulse-text-muted);
  font-style: italic;
}
.bubble-row.streaming .bubble {
  border-style: dashed;
}
.bubble {
  max-width: 500px;
  padding: 10px 12px;
  border-radius: var(--pulse-radius-md);
  background: var(--pulse-bg-code);
  border: 1px solid var(--pulse-border-soft);
}
.bubble-row.user .bubble {
  background: linear-gradient(135deg, var(--el-color-primary) 0%, var(--pulse-color-accent-hover) 100%);
  color: #fff;
  border-color: transparent;
}
.bubble-row.user .meta {
  color: rgba(255, 255, 255, 0.8);
}
.meta {
  font-size: var(--pulse-text-xs);
  opacity: 0.7;
  margin-bottom: 4px;
}
.meta .time {
  margin-left: 6px;
  font-variant-numeric: tabular-nums;
}
.text {
  white-space: pre-wrap;
  line-height: 1.5;
  font-size: var(--pulse-text-md);
}
.text.md {
  white-space: normal;
  overflow-x: auto;
}
.text.md :deep(p),
.text.md :deep(ul),
.text.md :deep(ol) {
  margin: 0 0 6px;
}
.text.md :deep(h1),
.text.md :deep(h2),
.text.md :deep(h3) {
  font-size: var(--pulse-text-md);
  margin: 4px 0 6px;
}
.text.md :deep(hr) {
  border: none;
  border-top: 1px solid var(--pulse-border-soft);
  margin: 8px 0;
}
.text.md :deep(table) {
  border-collapse: collapse;
  font-size: var(--pulse-text-xs);
}
.text.md :deep(th),
.text.md :deep(td) {
  border: 1px solid var(--pulse-border-soft);
  padding: 2px 6px;
  white-space: nowrap;
}
.actions {
  margin-top: 8px;
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.chat-input {
  position: relative;
  padding-top: 8px;
  border-top: 1px solid var(--pulse-border);
}
.chat-input :deep(.el-textarea__inner) {
  padding: 10px 56px 10px 12px;
  border-radius: var(--pulse-radius-md);
}
.send-fab {
  position: absolute;
  right: 10px;
  bottom: 10px;
  width: 36px;
  height: 36px;
  box-shadow: 0 4px 12px rgba(99, 102, 241, 0.32);
  transition:
    transform 0.15s ease,
    box-shadow 0.15s ease;
}
.send-fab:not(.is-disabled):hover {
  transform: translateY(-1px) scale(1.05);
}
.send-fab.is-disabled {
  box-shadow: none;
}
.typing {
  font-size: var(--pulse-text-base);
  color: var(--pulse-text-secondary);
}
</style>
