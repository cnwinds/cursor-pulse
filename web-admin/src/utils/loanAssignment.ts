/** 借用列表「分配方式」展示（与后台发放选项对齐，而非 delivery_mode 字面量）。 */

export interface LoanAssignmentFields {
  delivery_mode?: string | null
  lender_mode?: string | null
  routing_mode?: string | null
}

export function loanAssignmentLabel(row: LoanAssignmentFields): string {
  if (row.delivery_mode !== 'proxy_alias') {
    return 'Cursor Key'
  }
  if (row.routing_mode === 'pool') {
    return '自动分配'
  }
  if (row.lender_mode === 'auto') {
    return '自动分配'
  }
  return '指定账号'
}

export function loanAssignmentTagType(
  row: LoanAssignmentFields,
): 'info' | 'success' | 'warning' {
  const label = loanAssignmentLabel(row)
  if (label === 'Cursor Key') return 'info'
  if (label === '指定账号') return 'success'
  return 'warning'
}
