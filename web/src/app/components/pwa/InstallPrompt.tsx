import { useEffect, useState } from 'react'
import { Download, X } from 'lucide-react'

interface BeforeInstallPromptEvent extends Event {
  prompt(): Promise<void>
  userChoice: Promise<{ outcome: 'accepted' | 'dismissed' }>
}

const DISMISSED_KEY = 'jarvis:install-prompt-dismissed'

function isStandalone(): boolean {
  return (
    window.matchMedia?.('(display-mode: standalone)').matches ||
    (navigator as unknown as { standalone?: boolean }).standalone === true
  )
}

function isIOS(): boolean {
  return /iphone|ipad|ipod/i.test(navigator.userAgent)
}

/**
 * Bannière d'installation : Android/Chrome utilise `beforeinstallprompt`
 * (bouton natif) ; iOS/Safari n'expose pas cet évènement — on affiche
 * les instructions manuelles ("Partager" → "Sur l'écran d'accueil").
 */
export function InstallPrompt() {
  const [deferredPrompt, setDeferredPrompt] = useState<BeforeInstallPromptEvent | null>(null)
  const [showIosHint, setShowIosHint] = useState(false)
  const [dismissed, setDismissed] = useState(() => localStorage.getItem(DISMISSED_KEY) === '1')

  useEffect(() => {
    if (isStandalone() || dismissed) return

    const onBeforeInstall = (e: Event) => {
      e.preventDefault()
      setDeferredPrompt(e as BeforeInstallPromptEvent)
    }
    window.addEventListener('beforeinstallprompt', onBeforeInstall)

    if (isIOS()) setShowIosHint(true)

    return () => window.removeEventListener('beforeinstallprompt', onBeforeInstall)
  }, [dismissed])

  const dismiss = () => {
    localStorage.setItem(DISMISSED_KEY, '1')
    setDismissed(true)
  }

  const install = async () => {
    if (!deferredPrompt) return
    await deferredPrompt.prompt()
    await deferredPrompt.userChoice
    setDeferredPrompt(null)
    dismiss()
  }

  if (dismissed || (!deferredPrompt && !showIosHint)) return null

  return (
    <div className="glass-panel fixed bottom-4 left-1/2 z-50 w-[calc(100%-2rem)] max-w-sm -translate-x-1/2 rounded-xl p-3 animate-slide-up">
      <div className="flex items-start gap-3">
        <div className="mt-0.5 shrink-0 rounded-lg bg-foreground/10 p-1.5">
          <Download size={16} className="text-foreground" />
        </div>
        <div className="min-w-0 flex-1 text-xs">
          {deferredPrompt ? (
            <>
              <div className="font-medium text-foreground">Installer JARVIS</div>
              <div className="mt-0.5 text-muted-foreground">
                Accès rapide depuis l'écran d'accueil, fonctionne hors ligne.
              </div>
              <button
                onClick={install}
                className="mt-2 rounded-md bg-foreground/90 px-2.5 py-1 text-[11px] font-medium text-background hover:bg-foreground"
              >
                Installer
              </button>
            </>
          ) : (
            <>
              <div className="font-medium text-foreground">Installer sur iPhone</div>
              <div className="mt-0.5 text-muted-foreground">
                Bouton Partager puis « Sur l'écran d'accueil ».
              </div>
            </>
          )}
        </div>
        <button onClick={dismiss} className="shrink-0 text-muted-foreground hover:text-foreground">
          <X size={16} />
        </button>
      </div>
    </div>
  )
}
