<template>
  <div class="skills-page" v-loading="loading">
    <header class="page-header">
      <div>
        <h2>技能一览</h2>
        <p class="desc">每个说明文件（`docs/**/*.md`）即一个技能；右侧直接展示选中文件的元数据与正文。</p>
      </div>
      <el-button @click="loadSkills">刷新</el-button>
    </header>

    <el-alert
      type="info"
      show-icon
      :closable="false"
      title="说明书以仓库文件为准，请修改 assistant_platform/skills/docs 后发版。"
      class="source-alert"
    />

    <el-row :gutter="16">
      <el-col :xs="24" :md="9" :lg="8">
        <el-card shadow="never" class="skill-list-card">
          <template #header>技能</template>
          <el-table
            :data="skills"
            highlight-current-row
            :current-row-key="selectedSkillId"
            row-key="skill_id"
            @current-change="selectSkill"
          >
            <el-table-column prop="name" label="名称" min-width="120" />
            <el-table-column prop="skill_id" label="skill_id" min-width="120">
              <template #default="{ row }">
                <code class="skill-id">{{ row.skill_id }}</code>
              </template>
            </el-table-column>
            <el-table-column prop="audience" label="受众" width="88">
              <template #default="{ row }">{{ row.audience.join('、') || '—' }}</template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>

      <el-col :xs="24" :md="15" :lg="16">
        <el-card shadow="never" class="skill-detail-card">
          <template #header>
            <div class="card-header">
              <span>{{ selectedSkill?.name || '技能说明' }}</span>
              <el-tag v-if="selectedSkill" type="info">{{ selectedSkill.skill_id }}</el-tag>
            </div>
          </template>

          <template v-if="selectedSkill">
            <div class="file-content">
              <div class="file-content-header">
                <code>{{ selectedSkill.rel_path }}</code>
              </div>

              <div v-if="parsedDoc.meta.audience?.length || parsedDoc.meta.when_to_use?.length" class="file-meta">
                <div v-if="parsedDoc.meta.audience?.length" class="meta-row">
                  <span class="meta-label">audience</span>
                  <span>{{ parsedDoc.meta.audience.join('、') }}</span>
                </div>
                <div v-if="parsedDoc.meta.when_to_use?.length" class="meta-row">
                  <span class="meta-label">适用场景</span>
                  <ul class="scenario-list">
                    <li v-for="(item, idx) in parsedDoc.meta.when_to_use" :key="idx">{{ item }}</li>
                  </ul>
                </div>
              </div>

              <div
                v-if="parsedDoc.body.trim()"
                class="markdown-body"
                v-html="renderMarkdown(parsedDoc.body)"
              />
              <el-empty v-else description="该文件正文为空" />
            </div>
          </template>
          <el-empty v-else-if="!loading" description="暂无技能" />
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import client from '@/api/client'
import { renderMarkdown } from '@/utils/markdown'

interface SkillCard {
  skill_id: string
  name: string
  summary: string
  when_to_use?: string[]
  audience: string[]
  aliases: string[]
  pending_hint: boolean
  rel_path: string
}

interface SkillDetail extends SkillCard {
  markdown: string
}

interface ParsedDoc {
  meta: {
    audience?: string[]
    when_to_use?: string[]
  }
  body: string
}

const loading = ref(false)
const skills = ref<SkillCard[]>([])
const selectedSkillId = ref('')
const selectedSkill = ref<SkillDetail | null>(null)

const parsedDoc = computed(() => parseSkillMarkdown(selectedSkill.value?.markdown || ''))

function errorMessage(error: unknown, fallback: string): string {
  const detail = (error as { response?: { data?: { detail?: string } } })?.response?.data?.detail
  return detail || fallback
}

function asStringList(value: unknown): string[] {
  if (typeof value === 'string' && value.trim()) return [value.trim()]
  if (Array.isArray(value)) {
    return value.map((item) => String(item).trim()).filter(Boolean)
  }
  return []
}

/** 拆开 YAML frontmatter，避免把 meta 当 Markdown 渲染。 */
function parseSkillMarkdown(raw: string): ParsedDoc {
  const text = raw.replace(/^\uFEFF/, '')
  if (!text.startsWith('---')) {
    return { meta: {}, body: text }
  }
  const end = text.indexOf('\n---', 3)
  if (end < 0) {
    return { meta: {}, body: text }
  }
  const front = text.slice(3, end).trim()
  const body = text.slice(end + 4).replace(/^\r?\n/, '')
  const meta: ParsedDoc['meta'] = {}
  const audienceMatch = front.match(/^audience:\s*\[([^\]]*)\]/m)
  if (audienceMatch) {
    meta.audience = audienceMatch[1]
      .split(',')
      .map((part) => part.trim().replace(/^['"]|['"]$/g, ''))
      .filter(Boolean)
  }
  const whenMatch = front.match(/^when_to_use:\s*\n((?:[ \t]*-[ \t].+\n?)*)/m)
  if (whenMatch) {
    meta.when_to_use = whenMatch[1]
      .split('\n')
      .map((line) => line.replace(/^\s*-\s*/, '').trim())
      .filter(Boolean)
  } else {
    const alt = front.match(/^适用场景:\s*\n((?:[ \t]*-[ \t].+\n?)*)/m)
    if (alt) {
      meta.when_to_use = asStringList(
        alt[1]
          .split('\n')
          .map((line) => line.replace(/^\s*-\s*/, '').trim())
          .filter(Boolean),
      )
    }
  }
  return { meta, body }
}

async function loadSkillDetail(skillId: string) {
  const { data } = await client.get<SkillDetail>(`/api/v2/assistant/skills/${skillId}`)
  selectedSkill.value = data
  selectedSkillId.value = data.skill_id
}

async function selectSkill(skill: SkillCard | undefined) {
  if (!skill || skill.skill_id === selectedSkillId.value) return
  loading.value = true
  try {
    await loadSkillDetail(skill.skill_id)
  } catch (error) {
    ElMessage.error(errorMessage(error, '加载技能说明失败'))
  } finally {
    loading.value = false
  }
}

async function loadSkills() {
  loading.value = true
  try {
    const { data } = await client.get<{ skills: SkillCard[] }>('/api/v2/assistant/skills')
    skills.value = data.skills || []
    const current = skills.value.find((skill) => skill.skill_id === selectedSkillId.value)
    if (current) {
      await loadSkillDetail(current.skill_id)
    } else if (skills.value.length) {
      await loadSkillDetail(skills.value[0].skill_id)
    } else {
      selectedSkill.value = null
      selectedSkillId.value = ''
    }
  } catch (error) {
    ElMessage.error(errorMessage(error, '加载技能列表失败'))
  } finally {
    loading.value = false
  }
}

onMounted(loadSkills)
</script>

<style scoped>
.page-header,
.card-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}
.page-header {
  margin-bottom: 16px;
}
.desc {
  margin: 4px 0 0;
  color: #64748b;
  line-height: 1.5;
}
.source-alert {
  margin-bottom: 16px;
}
.skill-list-card,
.skill-detail-card {
  margin-bottom: 16px;
}
.skill-id {
  font-size: 12px;
  color: #64748b;
}
.file-content {
  min-width: 0;
}
.file-content-header {
  margin-bottom: 12px;
}
.file-content-header code {
  color: #64748b;
  font-size: 12px;
}
.file-meta {
  margin-bottom: 16px;
  padding: 12px;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  background: #f8fafc;
  font-size: 13px;
  color: #475569;
}
.meta-row + .meta-row {
  margin-top: 8px;
}
.meta-label {
  display: block;
  margin-bottom: 4px;
  color: #334155;
  font-weight: 600;
}
.scenario-list {
  margin: 0;
  padding-left: 1.2em;
}
.scenario-list li {
  margin: 2px 0;
}
.markdown-body {
  line-height: 1.7;
  overflow-wrap: anywhere;
}
:deep(.markdown-body pre) {
  overflow-x: auto;
  padding: 12px;
  border-radius: 6px;
  background: #f1f5f9;
}
:deep(.markdown-body code) {
  padding: 1px 4px;
  border-radius: 3px;
  background: #f1f5f9;
}
</style>
