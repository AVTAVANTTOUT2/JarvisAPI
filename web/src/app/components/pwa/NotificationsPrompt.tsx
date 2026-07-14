import { useEffect, useState } from 'react'
import { Bell, X } from 'lucide-react'
import { isPushSupported, subscribeToPush } from '@desktop/lib/push'

const DISMISSED_KEY = 'jarvis:notifications-prompt-dismissed'

/** Bannière discrète proposant d'activer les notifications push (une fois, si pertinent). */
export function NotificationsPrompt() {
  const [visible, setVisible] = useState(false)

  useEffect(() => {
    if (!isPushSupported()) return
    if (localStorage.getItem(DISMISSED_KEY) === '1') return
    if (Notification.permission !== 'default') return
    setVisible(true)
  }, [])

  const dismiss = () => {
    localStorage.setItem(DISMISSED_KEY, '1')
    setVisible(false)
  }

  const enable = async () => {
    await subscribeToPush()
    dismiss()
  }

  if (!visible) return null

  return (
    <div className="glass-panel fixed bottom-4 right-4 z-50 w-[calc(100%-2rem)] max-w-xs rounded-xl p-3 animate-slide-up">
      <div className="flex items-start gap-3">
        <div className="mt-0.5 shrink-0 rounded-lg bg-foreground/10 p-1.5">
          <Bell size={16} className="text-foreground" />
        </div>
        <div className="min-w-0 flex-1 text-xs">
          <div className="font-medium text-foreground">Activer les notifications</div>
          <div className="mt-0.5 text-muted-foreground">
            Reçois les alertes urgentes même app fermée.
          </div>
          <button
            onClick={enable}
            className="mt-2 rounded-md bg-foreground/90 px-2.5 py-1 text-[11px] font-medium text-background hover:bg-foreground"
          >
            Activer
          </button>
        </div>
        <button onClick={dismiss} className="shrink-0 text-muted-foreground hover:text-foreground">
          <X size={16} />
        </button>
      </div>
    </div>
  )
}
