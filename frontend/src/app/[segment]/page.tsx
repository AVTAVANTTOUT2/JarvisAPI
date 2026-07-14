import { UnifiedApp } from '@frontend/components/UnifiedApp'
import { UNIFIED_ROUTES } from '@frontend/lib/device'

export const dynamicParams = false

export function generateStaticParams() {
  return UNIFIED_ROUTES.map((segment) => ({ segment }))
}

export default function UnifiedRoutePage() {
  return <UnifiedApp />
}
