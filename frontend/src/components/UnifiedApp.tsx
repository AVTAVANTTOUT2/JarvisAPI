'use client'

import dynamic from 'next/dynamic'
import { useEffect } from 'react'

const DesktopApp = dynamic(() => import('@desktop/App'), { ssr: false })

/**
 * Application bureau.
 *
 * Le layout mobile a été retiré : les téléphones sont redirigés côté serveur
 * vers `/mobile/` (api/web_mobile.py), une interface autonome en HTML/CSS/JS
 * vanilla. Ce composant n'a donc plus de branche à choisir, et un téléphone
 * ne télécharge plus jamais ce bundle.
 */
export function UnifiedApp() {
  useEffect(() => {
    if ('serviceWorker' in navigator) {
      void navigator.serviceWorker.register('/sw.js')
    }
  }, [])

  return <DesktopApp />
}
