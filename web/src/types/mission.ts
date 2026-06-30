export interface JarvisEvent {
  type: string
  agent?: string | null
  data?: Record<string, unknown> | null
  timestamp: number
}
