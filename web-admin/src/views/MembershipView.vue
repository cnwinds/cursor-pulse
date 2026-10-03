<template>
  <div class="membership-page" v-loading="loading">
    <el-alert
      v-if="membershipRequired"
      type="warning"
      :closable="false"
      show-icon
      class="required-banner"
      title="已开启「必须开通会员才能使用」：未开通有效会员的成员将被拦截（BYOK 模型不受影响）。"
    />

    <el-tabs v-model="tab">
      <el-tab-pane label="成员" name="members">
        <div class="tab-toolbar">
          <el-input
            v-model="memberQuery"
            placeholder="搜索成员"
            clearable
            style="width: 200px"
            @keyup.enter="loadMembers"
          />
          <el-select
            v-model="hasMembershipFilter"
            clearable
            placeholder="会员状态"
            style="width: 140px"
            @change="loadMembers"
          >
            <el-option label="已开通" :value="true" />
            <el-option label="未开通" :value="false" />
          </el-select>
          <el-button @click="loadMembers">搜索</el-button>
        </div>
        <el-table :data="memberRows" stripe>
          <el-table-column prop="member_name" label="成员" min-width="120" />
          <el-table-column label="套餐" min-width="120">
            <template #default="{ row }">
              <el-tag v-if="!row.membership" size="small" type="info">未开通</el-tag>
              <el-tag v-else-if="!row.membership.plan_id" size="small" type="warning">自定义</el-tag>
              <span v-else>{{ row.membership.plan_name }}</span>
            </template>
          </el-table-column>
          <el-table-column label="余额模式" width="96">
            <template #default="{ row }">
              {{ row.membership ? creditModeLabel(row.membership.credit_mode) : '—' }}
            </template>
          </el-table-column>
          <el-table-column label="余额" width="100" align="right">
            <template #default="{ row }">
              <span
                v-if="showBalance(row)"
                :class="{ 'balance-negative': row.balance_cents <= 0 }"
              >
                {{ formatUsdCents(row.balance_cents) }}
              </span>
              <span v-else>—</span>
            </template>
          </el-table-column>
          <el-table-column label="规则状态" min-width="180">
            <template #default="{ row }">
              <UsageCapStatus
                v-if="row.membership"
                :rules="row.membership.effective_rules"
              />
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
          <el-table-column v-if="canWrite" label="操作" width="280" fixed="right" align="center">
            <template #default="{ row }">
              <el-button link type="primary" @click="openMembershipDialog(row)">
                {{ row.membership ? '更换' : '开通' }}
              </el-button>
              <el-button
                v-if="row.membership"
                link
                type="danger"
                @click="cancelMembership(row)"
              >
                取消
              </el-button>
              <el-button
                v-if="row.membership?.credit_mode === 'prepaid' || row.has_account"
                link
                @click="openGrantDialog(row)"
              >
                充值
              </el-button>
              <el-button
                v-if="row.membership?.credit_mode === 'prepaid' || row.has_account"
                link
                @click="openAdjustDialog(row)"
              >
                调整
              </el-button>
              <el-button
                v-if="row.has_account || row.membership?.credit_mode === 'prepaid'"
                link
                @click="openStatement(row)"
              >
                账单
              </el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <el-tab-pane label="套餐" name="plans">
        <div class="tab-toolbar">
          <el-button v-if="canWrite" type="primary" @click="openPlanDialog()">新建套餐</el-button>
          <el-checkbox v-model="includeArchived" @change="loadPlans">显示已归档</el-checkbox>
        </div>
        <el-table :data="plans" stripe>
          <el-table-column prop="name" label="名称" min-width="120" />
          <el-table-column label="窗口规则" min-width="200">
            <template #default="{ row }">
              <span v-if="!row.rules.length" class="muted">不限</span>
              <div v-else class="rule-chips">
                <template v-for="(rule, index) in row.rules" :key="index">
                  <span v-if="index" class="rule-or">或</span>
                  <el-tag size="small" type="info">{{ formatPlanRuleChip(rule) }}</el-tag>
                </template>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="余额模式" width="100">
            <template #default="{ row }">{{ creditModeLabel(row.credit_mode) }}</template>
          </el-table-column>
          <el-table-column label="开通赠送" width="100" align="right">
            <template #default="{ row }">
              {{
                row.credit_mode === 'prepaid' && row.opening_credit_cents != null
                  ? formatUsdCents(row.opening_credit_cents)
                  : '—'
              }}
            </template>
          </el-table-column>
          <el-table-column label="状态" width="88" align="center">
            <template #default="{ row }">
              <el-tag :type="row.status === 'active' ? 'success' : 'info'" size="small">
                {{ row.status === 'active' ? '启用' : '已归档' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="member_count" label="成员数" width="80" align="right" />
          <el-table-column v-if="canWrite" label="操作" width="140" fixed="right" align="center">
            <template #default="{ row }">
              <el-button link type="primary" @click="openPlanDialog(row)">编辑</el-button>
              <el-button
                v-if="row.status === 'active'"
                link
                type="warning"
                @click="archivePlan(row)"
              >
                归档
              </el-button>
              <el-button v-else link type="primary" @click="unarchivePlan(row)">恢复</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>
    </el-tabs>

    <el-dialog v-model="planDialogVisible" :title="planEditing ? '编辑套餐' : '新建套餐'" width="520px">
      <el-alert
        v-if="planEditing && planEditing.member_count > 0"
        type="info"
        :closable="false"
        show-icon
        class="plan-member-notice"
        :title="`修改将立即作用于 ${planEditing.member_count} 名已开通成员`"
      />
      <el-form label-width="100px">
        <el-form-item label="名称" required>
          <el-input v-model="planForm.name" />
        </el-form-item>
        <el-form-item label="窗口规则">
          <UsageCapRulesEditor v-model="planForm.rules" allow-total />
        </el-form-item>
        <el-form-item label="余额模式">
          <el-radio-group v-model="planForm.credit_mode">
            <el-radio value="unlimited">不限额</el-radio>
            <el-radio value="prepaid">预付余额</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="planForm.credit_mode === 'prepaid'" label="开通赠送">
          <el-input-number
            v-model="planForm.opening_credit_usd"
            :min="0"
            :precision="2"
            :step="1"
            :controls="false"
            placeholder="美元"
            style="width: 140px"
          />
          <span class="field-hint">仅首次开通该套餐时赠送一次</span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="planDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="planSaving" @click="savePlan">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="membershipDialogVisible" title="开通 / 更换会员" width="520px">
      <el-form label-width="110px">
        <el-form-item label="成员">
          <span>{{ membershipTarget?.member_name }}</span>
        </el-form-item>
        <el-form-item label="套餐" required>
          <el-select
            v-model="membershipForm.planMode"
            style="width: 100%"
            @change="onPlanModeChange"
          >
            <el-option label="自定义规则" value="custom" />
            <el-option
              v-for="p in membershipPlanOptions"
              :key="p.id"
              :label="p.label"
              :value="p.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item v-if="membershipForm.planMode === 'custom'" label="自定义规则">
          <UsageCapRulesEditor v-model="membershipForm.rules_override" allow-total />
        </el-form-item>
        <el-form-item label="余额模式">
          <el-select v-model="membershipForm.credit_mode_override" style="width: 100%">
            <el-option
              v-if="membershipForm.planMode !== 'custom'"
              label="跟随套餐"
              :value="null"
            />
            <el-option label="不限额" value="unlimited" />
            <el-option label="预付余额" value="prepaid" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="membershipDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="membershipSaving" @click="saveMembership">
          确认
        </el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="grantDialogVisible" title="充值" width="400px">
      <el-form label-width="80px">
        <el-form-item label="金额" required>
          <el-input-number
            v-model="grantForm.amount_usd"
            :min="0.01"
            :precision="2"
            :step="1"
            :controls="false"
            style="width: 140px"
          />
          <span class="field-suffix">美元</span>
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="grantForm.note" type="textarea" :rows="2" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="grantDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="grantSaving" @click="submitGrant">确认充值</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="adjustDialogVisible" title="调整余额" width="400px">
      <el-form label-width="80px">
        <el-form-item label="金额" required>
          <el-input-number
            v-model="adjustForm.amount_usd"
            :precision="2"
            :step="1"
            :controls="false"
            style="width: 140px"
          />
          <span class="field-suffix">美元（可正可负）</span>
        </el-form-item>
        <el-form-item label="备注" required>
          <el-input v-model="adjustForm.note" type="textarea" :rows="2" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="adjustDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="adjustSaving" @click="submitAdjust">确认调整</el-button>
      </template>
    </el-dialog>

    <el-drawer
      v-model="statementVisible"
      :title="`账单 · ${statementMemberName}`"
      size="880px"
      destroy-on-close
    >
      <CreditStatement
        v-if="statementMemberId"
        :base-url="`/api/v2/members/${statementMemberId}/credit`"
        allow-refund
        @refunded="loadMembers"
      />
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import client from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { useSettingsStore } from '@/stores/settings'
import UsageCapRulesEditor, { type UsageCapRule } from '@/components/borrow/UsageCapRulesEditor.vue'
import UsageCapStatus, { type UsageCapSnapshot } from '@/components/borrow/UsageCapStatus.vue'
import CreditStatement from '@/components/credit/CreditStatement.vue'
import { formatUsdCents } from '@/utils/money'

type CreditMode = 'unlimited' | 'prepaid'
type PlanStatus = 'active' | 'archived'

interface PlanRule {
  period: string
  pool: string
  cost_usd: number
  limit_cents: number
}

interface PlanOut {
  id: string
  name: string
  rules: PlanRule[]
  credit_mode: CreditMode
  opening_credit_cents: number | null
  status: PlanStatus
  member_count: number
  created_at: string
  updated_at: string
}

interface MembershipOut {
  id: string
  plan_id: string | null
  plan_name: string | null
  plan_status: string | null
  rules_override: PlanRule[] | null
  credit_mode_override: CreditMode | null
  credit_mode: CreditMode
  effective_rules: UsageCapSnapshot[]
  status: string
  created_at: string
  updated_at: string
}

interface MemberMembershipRow {
  member_id: string
  member_name: string
  membership: MembershipOut | null
  balance_cents: number
  has_account: boolean
}

const auth = useAuthStore()
const settingsStore = useSettingsStore()
const canWrite = computed(() => auth.hasPermission('accounts:write'))

const loading = ref(false)
const tab = ref('members')
const plans = ref<PlanOut[]>([])
const includeArchived = ref(false)
const memberRows = ref<MemberMembershipRow[]>([])
const memberQuery = ref('')
const hasMembershipFilter = ref<boolean | undefined>(undefined)
const membershipRequired = ref(false)

const planDialogVisible = ref(false)
const planEditing = ref<PlanOut | null>(null)
const planSaving = ref(false)
const planForm = ref({
  name: '',
  rules: [] as UsageCapRule[],
  credit_mode: 'unlimited' as CreditMode,
  opening_credit_usd: null as number | null,
})

const membershipDialogVisible = ref(false)
const membershipTarget = ref<MemberMembershipRow | null>(null)
const membershipSaving = ref(false)
const membershipForm = ref({
  planMode: 'custom' as string,
  rules_override: [] as UsageCapRule[],
  credit_mode_override: null as CreditMode | null,
})

const grantDialogVisible = ref(false)
const grantSaving = ref(false)
const grantTarget = ref<MemberMembershipRow | null>(null)
const grantForm = ref({ amount_usd: null as number | null, note: '' })

const adjustDialogVisible = ref(false)
const adjustSaving = ref(false)
const adjustTarget = ref<MemberMembershipRow | null>(null)
const adjustForm = ref({ amount_usd: null as number | null, note: '' })

const statementVisible = ref(false)
const statementMemberId = ref('')
const statementMemberName = ref('')

const activePlans = computed(() => plans.value.filter((p) => p.status === 'active'))

const membershipPlanOptions = computed(() => {
  const options = activePlans.value.map((p) => ({ id: p.id, label: p.name }))
  const current = membershipTarget.value?.membership
  if (current?.plan_id && !options.some((o) => o.id === current.plan_id)) {
    options.push({ id: current.plan_id, label: `${current.plan_name || current.plan_id}（已归档）` })
  }
  return options
})

let lastPlanMode = 'custom'

function onPlanModeChange(mode: string) {
  if (mode === 'custom') {
    if (membershipForm.value.credit_mode_override === null) {
      membershipForm.value.credit_mode_override = 'unlimited'
    }
  } else if (lastPlanMode === 'custom') {
    membershipForm.value.credit_mode_override = null
  }
  lastPlanMode = mode
}

function creditModeLabel(mode: CreditMode): string {
  return mode === 'prepaid' ? '预付' : '不限额'
}

function showBalance(row: MemberMembershipRow): boolean {
  if (!row.has_account) return false
  if (row.membership?.credit_mode === 'prepaid') return true
  return row.balance_cents !== 0
}

const PERIOD_LABEL: Record<string, string> = {
  '5h': '5 小时',
  week: '7 天',
  month: '30 天',
}

const POOL_LABEL: Record<string, string> = {
  auto: 'Auto',
  api: 'API',
  total: '合计',
}

function formatPlanRuleChip(rule: PlanRule): string {
  const period = PERIOD_LABEL[rule.period] || rule.period
  const pool = POOL_LABEL[rule.pool] || rule.pool
  const usd =
    typeof rule.cost_usd === 'number'
      ? rule.cost_usd
      : rule.limit_cents
        ? rule.limit_cents / 100
        : 0
  const limit = Number.isInteger(usd) ? String(usd) : usd.toFixed(2)
  return `${period} · ${pool} ≤ $${limit}`
}

function rulesToApi(rules: UsageCapRule[]): { period: string; pool: string; cost_usd: number }[] {
  const caps: { period: string; pool: string; cost_usd: number }[] = []
  const seen = new Set<string>()
  for (const rule of rules) {
    if (rule.cost_usd == null) throw new Error('请填写每条规则的整数美元')
    const key = `${rule.period}:${rule.pool}`
    if (seen.has(key)) throw new Error('同一周期和桶只能有一条规则')
    seen.add(key)
    caps.push({ period: rule.period, pool: rule.pool, cost_usd: rule.cost_usd })
  }
  return caps
}

function rulesFromPlanRules(rules: PlanRule[]): UsageCapRule[] {
  return rules.map((r) => ({
    period: r.period as UsageCapRule['period'],
    pool: r.pool as UsageCapRule['pool'],
    cost_usd: r.cost_usd ?? null,
  }))
}

async function loadSettings() {
  try {
    const data = await settingsStore.load()
    membershipRequired.value = Boolean(data.membership?.membership_required)
  } catch {
    membershipRequired.value = false
  }
}

async function loadPlans() {
  loading.value = true
  try {
    const res = await client.get('/api/v2/membership-plans', {
      params: { include_archived: includeArchived.value },
    })
    plans.value = res.data.items
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '加载套餐失败')
  } finally {
    loading.value = false
  }
}

async function loadMembers() {
  loading.value = true
  try {
    const params: Record<string, string | boolean> = {}
    if (memberQuery.value.trim()) params.q = memberQuery.value.trim()
    if (hasMembershipFilter.value !== undefined) {
      params.has_membership = hasMembershipFilter.value
    }
    const res = await client.get('/api/v2/memberships', { params })
    memberRows.value = res.data.items
    membershipRequired.value = res.data.membership_required ?? membershipRequired.value
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '加载成员失败')
  } finally {
    loading.value = false
  }
}

function openPlanDialog(plan?: PlanOut) {
  planEditing.value = plan ?? null
  if (plan) {
    planForm.value = {
      name: plan.name,
      rules: rulesFromPlanRules(plan.rules),
      credit_mode: plan.credit_mode,
      opening_credit_usd:
        plan.opening_credit_cents != null ? plan.opening_credit_cents / 100 : null,
    }
  } else {
    planForm.value = {
      name: '',
      rules: [],
      credit_mode: 'unlimited',
      opening_credit_usd: null,
    }
  }
  planDialogVisible.value = true
}

async function savePlan() {
  if (!planForm.value.name.trim()) {
    ElMessage.warning('请填写套餐名称')
    return
  }
  let rules: { period: string; pool: string; cost_usd: number }[]
  try {
    rules = rulesToApi(planForm.value.rules)
  } catch (e: any) {
    ElMessage.warning(e.message)
    return
  }
  planSaving.value = true
  try {
    const body: Record<string, unknown> = {
      name: planForm.value.name.trim(),
      rules,
      credit_mode: planForm.value.credit_mode,
    }
    if (planForm.value.credit_mode === 'prepaid') {
      body.opening_credit_usd = planForm.value.opening_credit_usd
    }
    if (planEditing.value) {
      await client.patch(`/api/v2/membership-plans/${planEditing.value.id}`, body)
    } else {
      await client.post('/api/v2/membership-plans', body)
    }
    ElMessage.success('已保存')
    planDialogVisible.value = false
    await loadPlans()
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '保存失败')
  } finally {
    planSaving.value = false
  }
}

async function archivePlan(plan: PlanOut) {
  try {
    await ElMessageBox.confirm(`归档套餐「${plan.name}」？已开通成员不受影响。`, '归档套餐', {
      type: 'warning',
    })
  } catch {
    return
  }
  try {
    await client.patch(`/api/v2/membership-plans/${plan.id}`, { status: 'archived' })
    ElMessage.success('已归档')
    await loadPlans()
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '归档失败')
  }
}

async function unarchivePlan(plan: PlanOut) {
  try {
    await client.patch(`/api/v2/membership-plans/${plan.id}`, { status: 'active' })
    ElMessage.success('已恢复')
    await loadPlans()
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '恢复失败')
  }
}

function resolveCreditModeOverride(
  planMode: string,
  override: CreditMode | null,
): CreditMode | null {
  if (planMode === 'custom') {
    return override ?? 'unlimited'
  }
  return override
}

function openMembershipDialog(row: MemberMembershipRow) {
  membershipTarget.value = row
  const m = row.membership
  const planMode = m?.plan_id ?? 'custom'
  lastPlanMode = planMode
  membershipForm.value = {
    planMode,
    rules_override: m?.rules_override ? rulesFromPlanRules(m.rules_override) : [],
    credit_mode_override: resolveCreditModeOverride(planMode, m?.credit_mode_override ?? null),
  }
  membershipDialogVisible.value = true
}

async function saveMembership() {
  if (!membershipTarget.value) return
  const form = membershipForm.value
  const body: Record<string, unknown> = {
    plan_id: form.planMode === 'custom' ? null : form.planMode,
    credit_mode_override: form.credit_mode_override,
    rules_override: null,
  }
  if (form.planMode === 'custom') {
    try {
      body.rules_override = rulesToApi(form.rules_override)
    } catch (e: any) {
      ElMessage.warning(e.message)
      return
    }
  }
  membershipSaving.value = true
  try {
    await client.put(`/api/v2/members/${membershipTarget.value.member_id}/membership`, body)
    ElMessage.success('已保存')
    membershipDialogVisible.value = false
    await loadMembers()
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '保存失败')
  } finally {
    membershipSaving.value = false
  }
}

async function cancelMembership(row: MemberMembershipRow) {
  try {
    await ElMessageBox.confirm(
      '取消会员后窗口规则不再生效，预付余额将保留，下次开通后可继续使用。',
      '取消会员',
      { type: 'warning' },
    )
  } catch {
    return
  }
  try {
    await client.post(`/api/v2/members/${row.member_id}/membership/cancel`)
    ElMessage.success('已取消')
    await loadMembers()
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '取消失败')
  }
}

function openGrantDialog(row: MemberMembershipRow) {
  grantTarget.value = row
  grantForm.value = { amount_usd: null, note: '' }
  grantDialogVisible.value = true
}

async function submitGrant() {
  if (!grantTarget.value || grantForm.value.amount_usd == null || grantForm.value.amount_usd <= 0) {
    ElMessage.warning('请输入正数金额')
    return
  }
  grantSaving.value = true
  try {
    await client.post(`/api/v2/members/${grantTarget.value.member_id}/credit/grants`, {
      amount_usd: grantForm.value.amount_usd,
      note: grantForm.value.note.trim() || null,
    })
    ElMessage.success('充值成功')
    grantDialogVisible.value = false
    await loadMembers()
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '充值失败')
  } finally {
    grantSaving.value = false
  }
}

function openAdjustDialog(row: MemberMembershipRow) {
  adjustTarget.value = row
  adjustForm.value = { amount_usd: null, note: '' }
  adjustDialogVisible.value = true
}

async function submitAdjust() {
  if (!adjustTarget.value || adjustForm.value.amount_usd == null) {
    ElMessage.warning('请输入调整金额')
    return
  }
  if (!adjustForm.value.note.trim()) {
    ElMessage.warning('请填写备注')
    return
  }
  adjustSaving.value = true
  try {
    await client.post(`/api/v2/members/${adjustTarget.value.member_id}/credit/adjustments`, {
      amount_usd: adjustForm.value.amount_usd,
      note: adjustForm.value.note.trim(),
    })
    ElMessage.success('已调整')
    adjustDialogVisible.value = false
    await loadMembers()
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '调整失败')
  } finally {
    adjustSaving.value = false
  }
}

function openStatement(row: MemberMembershipRow) {
  statementMemberId.value = row.member_id
  statementMemberName.value = row.member_name
  statementVisible.value = true
}

onMounted(async () => {
  await Promise.all([loadSettings(), loadPlans(), loadMembers()])
})
</script>

<style scoped>
.membership-page {
  width: 100%;
}

.required-banner {
  margin-bottom: 16px;
}

.tab-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  margin-bottom: 16px;
}

.field-hint,
.field-suffix {
  margin-left: 8px;
  font-size: var(--pulse-text-sm);
  color: var(--pulse-text-muted);
}

.balance-negative {
  color: var(--el-color-danger);
  font-weight: var(--pulse-font-medium);
}

.muted {
  color: var(--el-text-color-placeholder);
}

.plan-member-notice {
  margin-bottom: 16px;
}

.rule-chips {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 6px;
}

.rule-or {
  font-size: var(--pulse-text-xs);
  color: var(--el-text-color-placeholder);
}
</style>
