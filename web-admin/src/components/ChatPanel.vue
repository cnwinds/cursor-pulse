<template>
  <div class="chat-fab">
    <el-button type="primary" circle size="large" @click="open = true">
      <el-icon :size="22"><ChatDotRound /></el-icon>
    </el-button>

    <el-drawer v-model="open" title="小脉" direction="rtl" size="400px" :with-header="true">
      <div class="chat-body" ref="scrollRef">
        <div
          v-for="(msg, i) in messages"
          :key="i"
          class="bubble-row"
          :class="[msg.role, msg.kind === 'interim' ? 'interim' : '']"
        >
          <div class="bubble">
            <div class="meta">{{ msg.role === 'user' ? '我' : '小脉' }}</div>
            <div class="text">{{ msg.text }}</div>
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
        <div v-if="loading" class="typing">小脉正在想…</div>
      </div>
      <div class="chat-input">
        <el-input
          v-model="input"
          type="textarea"
          :rows="3"
          placeholder="跟小脉说点什么，例如：谁还没交？催一下没交的"
          @keydown.enter.exact.prevent="send"
        />
        <el-button type="primary" :loading="loading" @click="send">发送</el-button>
      </div>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { nextTick, onUnmounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import client from '@/api/client'

interface ChatMsg {
  role: 'user' | 'assistant'
  text: string
  kind?: 'interim' | 'final'
  actions?: Array<{ tool: string; status: string; message?: string }>
}

const open = ref(false)
const input = ref('')
const loading = ref(false)
const messages = ref<ChatMsg[]>([
  {
    role: 'assistant',
    text: '嗨，我是小脉。可以问我团队用量、提交进度；有权限的话也能让我催办、聚合或发月报。',
    kind: 'final',
  },
])
const scrollRef = ref<HTMLElement | null>(null)
const pollAfter = ref(0)
let pollTimer: ReturnType<typeof setInterval> | null = null

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
}

async function pollMessages() {
  try {
    const { data } = await client.get('/api/chat/messages', {
      params: { after: pollAfter.value },
    })
    const items = data.items || []
    for (const item of items) {
      pollAfter.value = Math.max(pollAfter.value, item.id)
      messages.value.push({
        role: 'assistant',
        text: item.text,
        kind: item.kind === 'interim' ? 'interim' : 'final',
      })
      if (item.kind === 'final') {
        loading.value = false
        stopPolling()
      }
    }
    if (items.length) {
      await scrollBottom()
    }
  } catch {
    /* keep polling until final or timeout */
  }
}

function startPolling(fromId = 0) {
  stopPolling()
  pollAfter.value = fromId
  pollTimer = setInterval(pollMessages, 800)
  void pollMessages()
}

onUnmounted(stopPolling)

async function send() {
  const text = input.value.trim()
  if (!text || loading.value) return
  messages.value.push({ role: 'user', text })
  input.value = ''
  loading.value = true
  await scrollBottom()
  try {
    const { data } = await client.post('/api/chat', { message: text })
    const fromId = typeof data.poll_after === 'number' ? data.poll_after : 0
    startPolling(fromId)
    if (data.reply && data.status !== 'accepted') {
      messages.value.push({ role: 'assistant', text: data.reply, kind: 'final' })
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
  background: #e2e8f0;
  border: 1px dashed #94a3b8;
  font-style: italic;
}
.bubble {
  max-width: 300px;
  padding: 10px 12px;
  border-radius: 12px;
  background: #f1f5f9;
}
.bubble-row.user .bubble {
  background: #6366f1;
  color: #fff;
}
.bubble-row.user .meta {
  color: rgba(255, 255, 255, 0.8);
}
.meta {
  font-size: 11px;
  opacity: 0.7;
  margin-bottom: 4px;
}
.text {
  white-space: pre-wrap;
  line-height: 1.5;
  font-size: 14px;
}
.actions {
  margin-top: 8px;
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.chat-input {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding-top: 8px;
  border-top: 1px solid #e2e8f0;
}
.typing {
  font-size: 13px;
  color: #64748b;
}
</style>
