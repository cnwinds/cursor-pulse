/// <reference types="vite/client" />

declare const __APP_BUILD__: {
  version: string
  channel: 'release' | 'dev'
  commit: string
  dirty: boolean
  describe: string
  builtAt: string
}
