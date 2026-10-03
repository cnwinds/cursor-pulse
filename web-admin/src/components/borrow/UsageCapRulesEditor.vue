<template>
  <div class="cap-rules">
    <div v-for="(rule, index) in modelValue" :key="index" class="cap-rule-row">
      <el-select v-model="rule.period" size="small" class="cap-period">
        <el-option label="5 小时" value="5h" />
        <el-option label="7 天" value="week" />
        <el-option label="30 天" value="month" />
      </el-select>
      <el-select v-model="rule.pool" size="small" class="cap-pool">
        <el-option label="Auto" value="auto" />
        <el-option label="API" value="api" />
        <el-option v-if="allowTotal" label="合计" value="total" />
      </el-select>
      <el-input-number
        v-model="rule.cost_usd"
        :min="1"
        :precision="0"
        :step="1"
        :value-on-clear="null"
        :controls="false"
        size="small"
        placeholder="美元"
        class="cap-amount"
      />
      <el-button link type="danger" aria-label="删除这条限制" @click="remove(index)">
        <el-icon><Close /></el-icon>
      </el-button>
    </div>
    <el-button link type="primary" class="cap-add" aria-label="添加用量限制" @click="add">
      <el-icon><Plus /></el-icon>
    </el-button>
    <p v-if="modelValue.length" class="cap-rule-hint">
      多条是「或」：任一条件达到就限制对应的 Auto 或 API。未添加则不限制。
    </p>
  </div>
</template>

<script setup lang="ts">
import { Close, Plus } from '@element-plus/icons-vue'

export interface UsageCapRule {
  period: '5h' | 'week' | 'month'
  pool: 'auto' | 'api' | 'total'
  cost_usd: number | null
}

const props = withDefaults(
  defineProps<{
    modelValue: UsageCapRule[]
    /** 会员套餐规则可含「合计」桶；借用场景默认不展示 */
    allowTotal?: boolean
  }>(),
  { allowTotal: false },
)

const emit = defineEmits<{
  'update:modelValue': [UsageCapRule[]]
}>()

function add() {
  const used = new Set(props.modelValue.map((rule) => `${rule.period}:${rule.pool}`))
  const presets: UsageCapRule[] = [
    { period: '5h', pool: 'auto', cost_usd: null },
    { period: 'week', pool: 'auto', cost_usd: null },
    { period: 'month', pool: 'auto', cost_usd: null },
    { period: '5h', pool: 'api', cost_usd: null },
    { period: 'week', pool: 'api', cost_usd: null },
    { period: 'month', pool: 'api', cost_usd: null },
  ]
  if (props.allowTotal) {
    presets.push(
      { period: '5h', pool: 'total', cost_usd: null },
      { period: 'week', pool: 'total', cost_usd: null },
      { period: 'month', pool: 'total', cost_usd: null },
    )
  }
  const next = presets.find((rule) => !used.has(`${rule.period}:${rule.pool}`)) ?? presets[0]
  emit('update:modelValue', [...props.modelValue, { ...next }])
}

function remove(index: number) {
  emit(
    'update:modelValue',
    props.modelValue.filter((_, i) => i !== index),
  )
}
</script>

<style scoped>
.cap-rules {
  width: 100%;
}
.cap-rule-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}
.cap-period {
  width: 108px;
}
.cap-pool {
  width: 88px;
}
.cap-amount {
  width: 96px;
}
.cap-add {
  padding-left: 0;
}
.cap-rule-hint {
  margin: 6px 0 0;
  font-size: var(--pulse-text-sm);
  color: var(--pulse-text-secondary);
  line-height: 1.45;
}
</style>
