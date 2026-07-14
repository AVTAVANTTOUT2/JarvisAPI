'use client'

import dynamic from 'next/dynamic'
import { usePathname } from 'next/navigation'
import { useEffect, useState } from 'react'

import { routeSegment, shouldUseMobileLayout } from '@frontend/lib/device'

const DesktopApp = dynamic(() => import('@desktop/App'), { ssr: false })
const MobileApp = dynamic(
  () => import('@frontend/components/MobileApp').then((module) => module.MobileApp),
  { ssr: false },
)

export function UnifiedApp() {
  const pathname = usePathname() || '/'
  const [mobile, setMobile] = useState<boolean | null>(null)

  useEffect(() => {
    const query = window.matchMedia('(max-width: 767px)')
    const update = () => setMobile(shouldUseMobileLayout(navigator.userAgent, window.innerWidth))
    update()
    query.addEventListener('change', update)
    window.addEventListener('resize', update)
    return () => {
      query.removeEventListener('change', update)
      window.removeEventListener('resize', update)
    }
  }, [])

  useEffect(() => {
    if ('serviceWorker' in navigator) {
      void navigator.serviceWorker.register('/sw.js')
    }
  }, [])

  if (mobile === null) {
    return <div className="jarvis-loading">Chargement…</div>
  }
  if (mobile) {
    return <MobileApp segment={routeSegment(pathname)} />
  }
  return <DesktopApp />
}
