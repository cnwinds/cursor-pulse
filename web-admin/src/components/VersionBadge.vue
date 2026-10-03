<template>
  <el-tooltip placement="right" :show-after="300" effect="dark">
    <template #content>
      <div class="version-tip">
        <div><span class="k">版本</span>{{ build.version }}{{ isDev ? '（开发版）' : '（正式版）' }}</div>
        <div v-if="build.commit"><span class="k">提交</span>{{ commitLabel }}</div>
        <div v-if="build.describe"><span class="k">描述</span>{{ build.describe }}</div>
        <div><span class="k">构建</span>{{ builtAtLabel }}</div>
        <div class="hint">点击复制版本信息</div>
      </div>
    </template>
    <button type="button" class="version-badge" :class="{ dev: isDev }" @click="onCopy">
      <span v-if="isDev" class="channel">DEV</span>
      <span class="ver">v{{ build.version }}</span>
      <template v-if="isDev && build.commit">
        <span class="sep" aria-hidden="true">·</span>
        <span class="commit">{{ commitLabel }}</span>
      </template>
    </button>
  </el-tooltip>
</template>

<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { copyText } from '@/utils/clipboard'

const build = __APP_BUILD__
const isDev = build.channel === 'dev'
const commitLabel = build.dirty ? `${build.commit}*` : build.commit
const builtAtLabel = new Date(build.builtAt).toLocaleString('zh-CN', { hour12: false })

async function onCopy() {
  const parts = [`Cursor Pulse v${build.version}`, isDev ? 'dev' : 'release']
  if (build.commit) parts.push(commitLabel)
  try {
    await copyText(parts.join(' '))
    ElMessage.success('已复制版本信息')
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
}
</script>

<style scoped>
.version-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  max-width: 100%;
  margin-top: 6px;
  padding: 2px 8px 2px 7px;
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.04);
  color: rgba(203, 213, 225, 0.9);
  font-family: var(--pulse-font-mono);
  font-size: 11px;
  line-height: 16px;
  white-space: nowrap;
  cursor: pointer;
  transition:
    background var(--pulse-transition),
    border-color var(--pulse-transition);
}

.version-badge:hover {
  background: rgba(255, 255, 255, 0.08);
  border-color: rgba(255, 255, 255, 0.18);
}

.version-badge.dev {
  border-color: rgba(251, 191, 36, 0.35);
  background: rgba(251, 191, 36, 0.08);
}

.version-badge.dev:hover {
  border-color: rgba(251, 191, 36, 0.55);
  background: rgba(251, 191, 36, 0.14);
}

.channel {
  padding: 0 4px;
  border-radius: 4px;
  background: rgba(251, 191, 36, 0.9);
  color: #1f2937;
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.06em;
  line-height: 13px;
}

.sep {
  opacity: 0.5;
}

.commit {
  overflow: hidden;
  text-overflow: ellipsis;
  color: #fcd34d;
}

.version-tip {
  font-size: 12px;
  line-height: 1.7;
}

.version-tip .k {
  display: inline-block;
  width: 36px;
  opacity: 0.65;
}

.version-tip .hint {
  margin-top: 4px;
  opacity: 0.55;
}
</style>
