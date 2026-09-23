<template>
  <section class="block">
    <h4>questions</h4>
    <el-collapse accordion>
      <el-collapse-item v-for="item in items" :key="item.name" :name="item.name">
        <template #title>
          <span class="q-title">{{ item.title }}</span>
          <el-tag size="small" effect="plain" class="q-type">{{ item.type }}</el-tag>
        </template>
        <p class="q-instructions">{{ item.instructions }}</p>
        <ul v-if="item.criteriaLines.length" class="criteria-list">
          <li v-for="line in item.criteriaLines" :key="line.key">
            <strong>{{ line.key }}</strong>
            <span>{{ line.text }}</span>
          </li>
        </ul>
      </el-collapse-item>
    </el-collapse>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { JevTraceAccountLookup } from './jevTraceTypes'

const props = defineProps<{
  questions: Record<string, Record<string, unknown>>
  lookup: Map<string, JevTraceAccountLookup>
}>()

function accountLabel(id: string): string {
  return props.lookup.get(id)?.account_identifier || id
}

const items = computed(() =>
  Object.entries(props.questions).map(([name, q]) => {
    const type = String(q.type || 'unknown')
    const instructions = String(q.instructions || '')
    let title = name
    if (name === 'pick') title = 'pick · 选哪个账号'
    else if (name.startsWith('safe_for_owner_')) {
      const id = name.replace('safe_for_owner_', '')
      title = `主负责人安全 · ${accountLabel(id)}`
    }
    const criteria = q.criteria
    const criteriaLines: { key: string; text: string }[] = []
    if (criteria && typeof criteria === 'object' && !Array.isArray(criteria)) {
      for (const [key, text] of Object.entries(criteria as Record<string, unknown>)) {
        const label = name === 'pick' ? accountLabel(key) : key
        criteriaLines.push({ key: label, text: String(text) })
      }
    }
    return { name, type, title, instructions, criteriaLines }
  }),
)
</script>

<style scoped>
.block {
  margin-bottom: 16px;
}
.block h4 {
  margin: 0 0 8px;
  font-size: 13px;
  font-weight: 600;
}
.q-title {
  margin-right: 8px;
}
.q-type {
  vertical-align: middle;
}
.q-instructions {
  margin: 0 0 10px;
  font-size: 13px;
  color: var(--el-text-color-regular);
  line-height: 1.5;
}
.criteria-list {
  margin: 0;
  padding-left: 0;
  list-style: none;
  font-size: 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.criteria-list li {
  display: grid;
  grid-template-columns: 140px 1fr;
  gap: 8px;
  padding: 8px;
  background: var(--el-fill-color-light);
  border-radius: 6px;
}
.criteria-list strong {
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
