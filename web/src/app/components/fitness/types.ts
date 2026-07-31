export type WorkoutType =
  | 'poussee'
  | 'tirage'
  | 'jambes'
  | 'full_body'
  | 'natation'
  | 'autre'

export type MealType = 'petit_dej' | 'dejeuner' | 'diner' | 'collation'

export interface TodaySummary {
  date: string
  workout_done: boolean
  workout_count: number
  meal_count: number
  calories_estimate: number
  water_ml: number
  wellbeing: {
    rating: number | null
    journal_text: string | null
  } | null
}

export function localTodayIso(): string {
  const now = new Date()
  const year = now.getFullYear()
  const month = String(now.getMonth() + 1).padStart(2, '0')
  const day = String(now.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}
