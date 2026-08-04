interface ImportMetaEnv {
  readonly DEV: boolean
  readonly VITE_MAP_STYLE_URL?: string
  readonly VITE_WS_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
