import { execFileSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import { fileURLToPath, URL } from 'node:url'

export interface BuildInfo {
  version: string
  channel: 'release' | 'dev'
  commit: string
  dirty: boolean
  describe: string
  builtAt: string
}

function git(args: string[]): string {
  try {
    return execFileSync('git', args, { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim()
  } catch {
    return ''
  }
}

function readProjectVersion(): string {
  try {
    const toml = readFileSync(fileURLToPath(new URL('../pyproject.toml', import.meta.url)), 'utf8')
    return toml.match(/^version\s*=\s*"([^"]+)"/m)?.[1] ?? '0.0.0'
  } catch {
    return '0.0.0'
  }
}

/**
 * Docker builds have no `.git`; pass PULSE_BUILD_DESCRIBE / PULSE_BUILD_COMMIT as build args.
 * A build is "release" only when HEAD is exactly the clean `v<version>` tag.
 */
export function resolveBuildInfo(isDevServer: boolean): BuildInfo {
  const version = readProjectVersion()
  const describe =
    process.env.PULSE_BUILD_DESCRIBE?.trim() || git(['describe', '--tags', '--match', 'v*', '--dirty', '--always'])
  const commit = (process.env.PULSE_BUILD_COMMIT?.trim() || git(['rev-parse', 'HEAD'])).slice(0, 7)
  const dirty = describe.endsWith('-dirty')
  const onReleaseTag = describe === `v${version}`
  const channel = !isDevServer && (onReleaseTag || (!describe && !commit)) ? 'release' : 'dev'
  return { version, channel, commit, dirty, describe, builtAt: new Date().toISOString() }
}
