/** Format integer cents as USD, e.g. $12.34 or -$0.12 */
export function formatUsdCents(cents: number): string {
  const usd = cents / 100
  const abs = Math.abs(usd).toFixed(2)
  return cents < 0 ? `-$${abs}` : `$${abs}`
}
