/** Types partagés pour les vues BIG BROTHER — alignés sur les réponses API FastAPI. */

export interface ApiPerson {
  id?: number
  name: string
  relationship?: string | null
  personality_notes?: string | null
  dynamics?: string | null
  patterns?: string | null
  last_mentioned?: string | null
  message_count?: number | null
  ai_description?: string | null
}

export interface NotificationItem {
  id?: number
  title?: string | null
  content?: string | null
  source?: string | null
  priority?: string | null
  created_at?: string | null
}

export interface RelationshipProfile {
  relationship_profile?: Record<string, unknown> | null
}
