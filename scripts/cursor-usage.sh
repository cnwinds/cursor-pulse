#!/usr/bin/env bash
# Fetch Cursor account usage from API key or local auth.json session.
# API keys (crsr_...) are exchanged for session tokens via /auth/exchange_user_api_key.

set -euo pipefail

API_BASE="${CURSOR_API_BASE:-https://api2.cursor.sh}"
AUTH_FILE="${CURSOR_AUTH_FILE:-$HOME/.config/cursor/auth.json}"
API_KEY="${CURSOR_API_KEY:-}"
OUTPUT_JSON=0

usage() {
  cat <<'EOF'
Usage: cursor-usage [options]

Show Cursor subscription usage for the authenticated account.

Authentication (first match wins):
  1. --api-key / CURSOR_API_KEY
  2. --auth-file / CURSOR_AUTH_FILE (reads accessToken or apiKey from auth.json)
  3. ~/.config/cursor/auth.json

Options:
  -k, --api-key <key>      Cursor user API key (crsr_...)
  -a, --auth-file <path>   Path to auth.json
  -j, --json               Print raw usage API response as JSON
  -h, --help               Show this help

Environment:
  CURSOR_API_KEY     Same as --api-key
  CURSOR_AUTH_FILE   Same as --auth-file (default: ~/.config/cursor/auth.json)
  CURSOR_API_BASE    API origin (default: https://api2.cursor.sh)

Examples:
  cursor-usage
  cursor-usage --api-key crsr_...
  CURSOR_API_KEY=crsr_... cursor-usage --json
  cursor-usage --auth-file /path/to/auth.json
EOF
}

die() {
  echo "cursor-usage: $*" >&2
  exit 1
}

format_usd_cents() {
  local cents="$1"
  printf '$%.2f' "$(echo "scale=2; $cents / 100" | bc)"
}

format_pct() {
  local value="$1"
  printf '%.0f%%' "$(echo "scale=0; $value + 0.5/1" | bc)"
}

format_date_ms() {
  local ms="$1"
  date -u -d "@$((ms / 1000))" '+%Y-%m-%d' 2>/dev/null \
    || date -u -r "$((ms / 1000))" '+%Y-%m-%d'
}

exchange_api_key() {
  local key="$1"
  local response http_code body

  response="$(
    curl -sS -w '\n%{http_code}' -X POST "$API_BASE/auth/exchange_user_api_key" \
      -H "Content-Type: application/json" \
      -H "Authorization: Bearer $key" \
      -d '{}'
  )"
  http_code="${response##*$'\n'}"
  body="${response%$'\n'*}"

  if [[ "$http_code" != "200" ]]; then
    local message
    message="$(echo "$body" | jq -r '.message // .error // empty' 2>/dev/null || true)"
    [[ -n "$message" ]] || message="HTTP $http_code"
    die "API key exchange failed: $message"
  fi

  local access refresh
  access="$(echo "$body" | jq -r '.accessToken // empty')"
  refresh="$(echo "$body" | jq -r '.refreshToken // empty')"
  [[ -n "$access" && "$access" != "null" ]] || die "API key exchange returned no accessToken"
  [[ -n "$refresh" && "$refresh" != "null" ]] || die "API key exchange returned no refreshToken"

  printf '%s' "$access"
}

resolve_token() {
  local token key

  if [[ -n "$API_KEY" ]]; then
    exchange_api_key "$API_KEY"
    return
  fi

  if [[ -f "$AUTH_FILE" ]]; then
    token="$(jq -r '.accessToken // empty' "$AUTH_FILE")"
    if [[ -n "$token" && "$token" != "null" ]]; then
      printf '%s' "$token"
      return
    fi

    key="$(jq -r '.apiKey // empty' "$AUTH_FILE")"
    if [[ -n "$key" && "$key" != "null" ]]; then
      exchange_api_key "$key"
      return
    fi
  fi

  die "no credentials found (use --api-key, set CURSOR_API_KEY, or run: agent login)"
}

fetch_period_usage() {
  local token="$1"
  curl -fsS -X POST "$API_BASE/aiserver.v1.DashboardService/GetCurrentPeriodUsage" \
    -H "Authorization: Bearer $token" \
    -H "Content-Type: application/json" \
    -H "Connect-Protocol-Version: 1" \
    -d '{}'
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -k|--api-key)
      [[ $# -ge 2 ]] || die "missing value for $1"
      API_KEY="$2"
      shift 2
      ;;
    -a|--auth-file)
      [[ $# -ge 2 ]] || die "missing value for $1"
      AUTH_FILE="$2"
      shift 2
      ;;
    -j|--json) OUTPUT_JSON=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1 (try --help)" ;;
  esac
done

command -v jq >/dev/null 2>&1 || die "jq is required"
command -v curl >/dev/null 2>&1 || die "curl is required"
command -v bc >/dev/null 2>&1 || die "bc is required"

TOKEN="$(resolve_token)"

PERIOD_JSON="$(fetch_period_usage "$TOKEN")" \
  || die "failed to fetch usage from $API_BASE"

if [[ "$OUTPUT_JSON" -eq 1 ]]; then
  echo "$PERIOD_JSON" | jq .
  exit 0
fi

LEGACY_JSON="$(
  curl -fsS -H "Authorization: Bearer $TOKEN" "$API_BASE/auth/usage" 2>/dev/null || echo '{}'
)"

EMAIL="$(jq -r '.email // empty' "$AUTH_FILE" 2>/dev/null || true)"
if [[ -z "$EMAIL" ]]; then
  EMAIL="$(jq -r '.authInfo.email // empty' "$HOME/.cursor/cli-config.json" 2>/dev/null || true)"
fi

PLAN_USAGE="$(echo "$PERIOD_JSON" | jq '.planUsage // empty')"
if [[ -z "$PLAN_USAGE" || "$PLAN_USAGE" == "null" ]]; then
  die "unexpected API response (no planUsage field)"
fi

TOTAL_SPEND="$(echo "$PLAN_USAGE" | jq -r '.totalSpend')"
REMAINING="$(echo "$PLAN_USAGE" | jq -r '.remaining')"
LIMIT="$(echo "$PLAN_USAGE" | jq -r '.limit')"
AUTO_PCT="$(echo "$PLAN_USAGE" | jq -r '.autoPercentUsed // 0')"
API_PCT="$(echo "$PLAN_USAGE" | jq -r '.apiPercentUsed // 0')"

CYCLE_START="$(echo "$PERIOD_JSON" | jq -r '.billingCycleStart')"
CYCLE_END="$(echo "$PERIOD_JSON" | jq -r '.billingCycleEnd')"
DISPLAY_MSG="$(echo "$PERIOD_JSON" | jq -r '.displayMessage // empty')"
AUTO_MSG="$(echo "$PERIOD_JSON" | jq -r '.autoModelSelectedDisplayMessage // empty')"
API_MSG="$(echo "$PERIOD_JSON" | jq -r '.namedModelSelectedDisplayMessage // empty')"

USED_PCT="$(format_pct "$(echo "scale=0; ($TOTAL_SPEND * 100) / $LIMIT" | bc)")"

echo "Cursor Usage"
echo "============"
[[ -n "$EMAIL" ]] && echo "Account:       $EMAIL"
echo "Billing cycle: $(format_date_ms "$CYCLE_START") → $(format_date_ms "$CYCLE_END")"
echo
echo "Included usage"
printf "  Used:          %s (%s)\n" "$(format_usd_cents "$TOTAL_SPEND")" "$USED_PCT"
printf "  Remaining:     %s\n" "$(format_usd_cents "$REMAINING")"
printf "  Limit:         %s\n" "$(format_usd_cents "$LIMIT")"
echo
echo "Breakdown"
[[ -n "$DISPLAY_MSG" ]] && echo "  $DISPLAY_MSG"
[[ -n "$AUTO_MSG" ]] && echo "  Auto models:  $(format_pct "$AUTO_PCT") of included total"
[[ -n "$API_MSG" ]] && echo "  Named models: $(format_pct "$API_PCT") of included API usage"

LEGACY_REQUESTS="$(echo "$LEGACY_JSON" | jq -r '."gpt-4".numRequests // empty')"
LEGACY_MAX="$(echo "$LEGACY_JSON" | jq -r '."gpt-4".maxRequestUsage // empty')"
if [[ -n "$LEGACY_MAX" && "$LEGACY_MAX" != "null" ]]; then
  echo
  echo "Legacy request quota (gpt-4 bucket)"
  echo "  Requests: $LEGACY_REQUESTS / $LEGACY_MAX"
fi

echo
echo "Dashboard: https://cursor.com/dashboard/usage"