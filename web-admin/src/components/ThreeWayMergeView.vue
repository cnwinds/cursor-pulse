<template>
  <div class="merge-shell">
    <header class="merge-toolbar">
      <div class="toolbar-left">
        <el-button size="small" @click="applyNonConflicting">应用无冲突变更</el-button>
        <span class="stats muted">
          {{ changeCount }} 处可合并变更 · {{ conflictCount }} 处冲突
        </span>
      </div>
      <div class="toolbar-right">
        <el-button size="small" @click="acceptLeft">全部采用左侧</el-button>
        <el-button size="small" @click="acceptRight">全部采用右侧</el-button>
      </div>
    </header>

    <div class="merge-panes">
      <section class="pane pane-side">
        <div class="pane-title">{{ leftLabel }}</div>
        <div class="pane-body" ref="leftPaneRef">
          <div
            v-for="(line, idx) in leftLines"
            :key="`l-${idx}`"
            class="code-line"
            :class="lineClass(line.role)"
          >
            <div class="gutter">
              <button
                v-if="line.role !== 'context'"
                type="button"
                class="gutter-btn"
                title="采纳到结果"
                @click="acceptSideLine('left', idx)"
              >
                »
              </button>
            </div>
            <span class="ln">{{ line.line_number }}</span>
            <code class="txt">{{ line.text }}</code>
          </div>
        </div>
      </section>

      <section class="pane pane-result">
        <div class="pane-title">合并结果（可编辑）</div>
        <textarea
          v-model="resultText"
          class="result-editor"
          spellcheck="false"
          @input="onResultInput"
        />
      </section>

      <section class="pane pane-side">
        <div class="pane-title">{{ rightLabel }}</div>
        <div class="pane-body" ref="rightPaneRef">
          <div
            v-for="(line, idx) in rightLines"
            :key="`r-${idx}`"
            class="code-line"
            :class="lineClass(line.role)"
          >
            <div class="gutter">
              <button
                v-if="line.role !== 'context'"
                type="button"
                class="gutter-btn"
                title="采纳到结果"
                @click="acceptSideLine('right', idx)"
              >
                «
              </button>
            </div>
            <span class="ln">{{ line.line_number }}</span>
            <code class="txt">{{ line.text }}</code>
          </div>
        </div>
      </section>
    </div>

    <footer class="merge-footer">
      <el-button @click="$emit('cancel')">取消</el-button>
      <el-button type="primary" @click="$emit('apply', resultText)">应用</el-button>
    </footer>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'

export type MergeLineRole = 'context' | 'change' | 'conflict'

export interface MergeLineDto {
  text: string
  role: MergeLineRole
  line_number: number
}

const props = defineProps<{
  leftLabel: string
  rightLabel: string
  leftLines: MergeLineDto[]
  rightLines: MergeLineDto[]
  resultText: string
  changeCount: number
  conflictCount: number
}>()

defineEmits<{
  cancel: []
  apply: [text: string]
}>()

const resultText = ref(props.resultText)
const leftLines = ref<MergeLineDto[]>([])
const rightLines = ref<MergeLineDto[]>([])

watch(
  () => props,
  () => {
    resultText.value = props.resultText
    leftLines.value = props.leftLines
    rightLines.value = props.rightLines
  },
  { immediate: true, deep: true },
)

const leftLabel = computed(() => props.leftLabel)
const rightLabel = computed(() => props.rightLabel)
const changeCount = computed(() => props.changeCount)
const conflictCount = computed(() => props.conflictCount)

function lineClass(role: MergeLineRole) {
  if (role === 'conflict') return 'is-conflict'
  if (role === 'change') return 'is-change'
  return 'is-context'
}

function applyNonConflicting() {
  const kept: string[] = []
  const max = Math.max(leftLines.value.length, rightLines.value.length)
  for (let i = 0; i < max; i += 1) {
    const left = leftLines.value[i]
    const right = rightLines.value[i]
    if (left?.role === 'conflict' || right?.role === 'conflict') {
      continue
    }
    if (left?.role === 'change') {
      kept.push(left.text)
    } else if (right?.role === 'change') {
      kept.push(right.text)
    } else if (left) {
      kept.push(left.text)
    } else if (right) {
      kept.push(right.text)
    }
  }
  resultText.value = kept.join('\n')
}

function acceptLeft() {
  resultText.value = leftLines.value.map((line) => line.text).join('\n')
}

function acceptRight() {
  resultText.value = rightLines.value.map((line) => line.text).join('\n')
}

function acceptSideLine(side: 'left' | 'right', index: number) {
  const line = side === 'left' ? leftLines.value[index] : rightLines.value[index]
  if (!line) return
  const lines = resultText.value.split('\n')
  if (index < lines.length) {
    lines[index] = line.text
    resultText.value = lines.join('\n')
  } else {
    resultText.value = `${resultText.value}\n${line.text}`.replace(/^\n/, '')
  }
}

function onResultInput() {
  // result is already bound via v-model
}
</script>

<style scoped>
.merge-shell {
  display: flex;
  flex-direction: column;
  height: min(78vh, 820px);
  min-height: 420px;
}
.merge-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 0 12px;
  border-bottom: 1px solid #e2e8f0;
}
.toolbar-left,
.toolbar-right {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.stats {
  font-size: 12px;
}
.merge-panes {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: 1fr 1.1fr 1fr;
  gap: 8px;
  padding: 8px 0;
}
.pane {
  display: flex;
  flex-direction: column;
  min-width: 0;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  overflow: hidden;
  background: #fff;
}
.pane-title {
  padding: 6px 10px;
  font-size: 12px;
  font-weight: 600;
  color: #475569;
  background: #f8fafc;
  border-bottom: 1px solid #e2e8f0;
}
.pane-body {
  flex: 1;
  overflow: auto;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 12px;
  line-height: 1.45;
}
.code-line {
  display: grid;
  grid-template-columns: 28px 36px 1fr;
  gap: 4px;
  padding: 0 6px;
  white-space: pre;
}
.code-line.is-change {
  background: #dcfce7;
}
.code-line.is-conflict {
  background: #fee2e2;
}
.gutter {
  display: flex;
  align-items: center;
  justify-content: center;
}
.gutter-btn {
  border: none;
  background: transparent;
  color: #64748b;
  cursor: pointer;
  font-size: 11px;
  padding: 0;
}
.gutter-btn:hover {
  color: #0f172a;
}
.ln {
  color: #94a3b8;
  text-align: right;
  user-select: none;
}
.txt {
  color: #0f172a;
}
.pane-result {
  border-color: #cbd5e1;
}
.result-editor {
  flex: 1;
  width: 100%;
  min-height: 0;
  border: none;
  resize: none;
  padding: 8px 10px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 12px;
  line-height: 1.45;
  outline: none;
}
.merge-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding-top: 8px;
  border-top: 1px solid #e2e8f0;
}
.muted {
  color: #94a3b8;
}
</style>
