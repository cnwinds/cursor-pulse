declare const __PULSE_VERSION__: string

/** Release version from repo `pyproject.toml`, injected at Vite build/dev startup. */
export const pulseVersion: string = __PULSE_VERSION__

export function pulseVersionLabel(version: string = pulseVersion): string {
  const raw = version.trim()
  if (!raw) return 'v0.0.0'
  return raw.startsWith('v') ? raw : `v${raw}`
}
