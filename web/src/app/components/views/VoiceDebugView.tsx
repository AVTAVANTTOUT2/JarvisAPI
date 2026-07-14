/**
 * VoiceDebugView — Page de diagnostic du pipeline vocal en temps reel.
 *
 * Affiche :
 *  1. La transcription STT exacte (ce que JARVIS a entendu)
 *  2. Le prompt systeme complet envoye au LLM
 *  3. Les messages (historique) envoyes au LLM
 *  4. La reponse brute du LLM (avant extraction emotion/action)
 *  5. Les metriques : duree audio, latence STT, latence LLM, latence TTS
 *
 * La page recoit les events WebSocket en temps reel (voice_debug_stt,
 * voice_debug_trace, voice_debug_tts) et les assemble pour afficher
 * le pipeline complet d'une interaction.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { RefreshCw, Mic, Bot, Volume2, Clock, ChevronDown, ChevronUp, Bug } from 'lucide-react'
import { api, type VoiceDebugTrace } from '@unified/lib/api'
import { ws } from '@desktop/services/websocket'

// ── Types locaux ─────────────────────────────────────────────────────────────

interface LiveSttData {
  timestamp: string
  transcript: string
  audio_duration_ms: number
  stt_latency_ms: number
  stt_engine: string
  audio_bytes: number
}

interface LiveTraceData {
  timestamp: string
  input_text: string
  system_prompt: string
  messages_sent: Array<{ role: string; content: string }>
  raw_response: string
  response_clean: string
  emotion: string
  action_detected: Record<string, unknown> | null
  action_result: Record<string, unknown> | null
  pass2_prompt: string | null
  pass2_response: string | null
  latency_llm_pass1_ms: number
  latency_llm_pass2_ms: number
  latency_total_ms: number
  model: string
  tokens_in: number
  tokens_out: number
  cost: number
  error: string | null
  stt_engine?: string
  tts_engine?: string
  audio_duration_ms?: number
  latency_stt_ms?: number
  latency_tts_ms?: number
}

interface LiveTtsData {
  timestamp: string
  text: string
  tts_engine: string
  tts_latency_ms: number
}

// ── Fonctions utilitaires ────────────────────────────────────────────────────

function getLatencyColor(ms: number): string {
  if (ms < 1000) return 'latency-fast'
  if (ms < 3000) return 'latency-ok'
  return 'latency-slow'
}

function formatLatency(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

function formatCost(cost: number): string {
  if (cost < 0.001) return `$${(cost * 1000000).toFixed(0)}u`
  return `$${cost.toFixed(4)}`
}

// ── Composant ────────────────────────────────────────────────────────────────

export default function VoiceDebugView() {
  const [logs, setLogs] = useState<VoiceDebugTrace[]>([])
  const [liveStt, setLiveStt] = useState<LiveSttData | null>(null)
  const [liveTrace, setLiveTrace] = useState<LiveTraceData | null>(null)
  const [liveTts, setLiveTts] = useState<LiveTtsData | null>(null)
  const [expanded, setExpanded] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)
  const bottomRef = useRef<HTMLDivElement>(null)

  const fetchLogs = useCallback(async () => {
    try {
      const data = await api.getVoiceDebugLogs(50)
      setLogs(data.logs)
    } catch (e) {
      console.error('[VoiceDebug] fetchLogs', e)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchLogs()

    const offs = [
      ws.on('voice_debug_stt', (d: unknown) => {
        const data = d as Record<string, unknown>
        setLiveStt({
          timestamp: String(data.timestamp || ''),
          transcript: String(data.transcript || ''),
          audio_duration_ms: Number(data.audio_duration_ms || 0),
          stt_latency_ms: Number(data.stt_latency_ms || 0),
          stt_engine: String(data.stt_engine || ''),
          audio_bytes: Number(data.audio_bytes || 0),
        })
        setLiveTrace(null)
        setLiveTts(null)
      }),
      ws.on('voice_debug_trace', (d: unknown) => {
        const data = d as Record<string, unknown>
        setLiveTrace({
          timestamp: String(data.timestamp || ''),
          input_text: String(data.input_text || ''),
          system_prompt: String(data.system_prompt || ''),
          messages_sent: (data.messages_sent as Array<{ role: string; content: string }>) || [],
          raw_response: String(data.raw_response || ''),
          response_clean: String(data.response_clean || ''),
          emotion: String(data.emotion || ''),
          action_detected: (data.action_detected as Record<string, unknown>) || null,
          action_result: (data.action_result as Record<string, unknown>) || null,
          pass2_prompt: data.pass2_prompt ? String(data.pass2_prompt) : null,
          pass2_response: data.pass2_response ? String(data.pass2_response) : null,
          latency_llm_pass1_ms: Number(data.latency_llm_pass1_ms || 0),
          latency_llm_pass2_ms: Number(data.latency_llm_pass2_ms || 0),
          latency_total_ms: Number(data.latency_total_ms || 0),
          model: String(data.model || ''),
          tokens_in: Number(data.tokens_in || 0),
          tokens_out: Number(data.tokens_out || 0),
          cost: Number(data.cost || 0),
          error: data.error ? String(data.error) : null,
        })
        // Refresh les logs DB apres un delai
        setTimeout(fetchLogs, 1500)
      }),
      ws.on('voice_debug_tts', (d: unknown) => {
        const data = d as Record<string, unknown>
        setLiveTts({
          timestamp: String(data.timestamp || ''),
          text: String(data.text || ''),
          tts_engine: String(data.tts_engine || ''),
          tts_latency_ms: Number(data.tts_latency_ms || 0),
        })
      }),
    ]

    return () => offs.forEach((off) => off())
  }, [fetchLogs])

  // Auto-scroll vers le bas quand le live change
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [liveTrace, liveTts])

  const toggleExpand = (id: number) => {
    setExpanded((prev) => (prev === id ? null : id))
  }

  // ── Rendu ─────────────────────────────────────────────────────────────────

  return (
    <div className="h-full overflow-y-auto p-4 space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-mono tracking-tight flex items-center gap-2">
            <Bug size={18} />
            Voice Debug
          </h1>
          <p className="text-xs text-muted-foreground mt-0.5">
            Pipeline vocal temps reel — STT, prompts LLM, metriques
          </p>
        </div>
        <button
          onClick={fetchLogs}
          className="inline-flex items-center gap-1.5 rounded-lg border border-white/15 px-3 py-1.5 text-xs font-mono hover:border-white/30 transition-colors"
        >
          <RefreshCw size={12} />
          Refresh
        </button>
      </div>

      {/* ── LIVE (WebSocket) ────────────────────────────────────────────── */}
      {(liveStt || liveTrace || liveTts) && (
        <section>
          <h2 className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground mb-2 px-1">
            LIVE
          </h2>
          <div className="rounded-xl border border-white/10 bg-white/[0.02] p-4 space-y-3 font-mono text-xs">
            {/* STT */}
            {liveStt && (
              <div className="flex items-start gap-3">
                <Mic size={14} className="mt-0.5 text-muted-foreground shrink-0" />
                <div className="flex-1 min-w-0">
                  <div className="text-[10px] text-muted-foreground uppercase tracking-wider mb-0.5">
                    {liveStt.timestamp} — STT
                  </div>
                  <div className="text-foreground break-words">
                    &ldquo;{liveStt.transcript}&rdquo;
                  </div>
                  <div className="flex flex-wrap gap-x-3 gap-y-0.5 mt-1 text-[10px] text-muted-foreground">
                    <span>Audio: {formatLatency(liveStt.audio_duration_ms)}</span>
                    <span>STT: {formatLatency(liveStt.stt_latency_ms)} ({liveStt.stt_engine})</span>
                    <span>{liveStt.audio_bytes} octets</span>
                  </div>
                </div>
              </div>
            )}

            {/* LLM Pass 1 */}
            {liveTrace && (
              <div className="flex items-start gap-3">
                <Bot size={14} className="mt-0.5 text-muted-foreground shrink-0" />
                <div className="flex-1 min-w-0">
                  <div className="text-[10px] text-muted-foreground uppercase tracking-wider mb-0.5">
                    {liveTrace.timestamp} — LLM Pass 1
                  </div>
                  <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-[10px] text-muted-foreground">
                    <span>{formatLatency(liveTrace.latency_llm_pass1_ms)}</span>
                    <span>{liveTrace.model}</span>
                    <span>
                      {liveTrace.tokens_in} in / {liveTrace.tokens_out} out
                    </span>
                    <span>{formatCost(liveTrace.cost)}</span>
                  </div>

                  {/* Brut */}
                  <div className="mt-1.5">
                    <span className="text-[10px] text-muted-foreground">Brut:</span>
                    <div className="debug-prompt mt-0.5 max-h-[80px]">
                      {liveTrace.raw_response || <span className="italic text-muted-foreground">(vide)</span>}
                    </div>
                  </div>

                  {/* Clean */}
                  <div className="mt-1.5">
                    <span className="text-[10px] text-muted-foreground">Clean:</span>
                    <span className="text-foreground ml-1">{liveTrace.response_clean}</span>
                  </div>

                  {/* Emotion */}
                  <div className="mt-1">
                    <span className="text-[10px] text-muted-foreground">Emotion:</span>
                    <span className="ml-1 px-1.5 py-0.5 rounded text-[10px] bg-white/5 border border-white/10">
                      {liveTrace.emotion || 'neutral'}
                    </span>
                  </div>

                  {/* Action */}
                  {liveTrace.action_detected && (
                    <div className="mt-1">
                      <span className="text-[10px] text-muted-foreground">Action:</span>
                      <div className="debug-prompt mt-0.5 max-h-[60px]">
                        {JSON.stringify(liveTrace.action_detected, null, 2)}
                      </div>
                    </div>
                  )}

                  {/* Pass 2 */}
                  {liveTrace.latency_llm_pass2_ms > 0 && (
                    <div className="mt-1.5 pt-1.5 border-t border-white/5">
                      <span className="text-[10px] text-muted-foreground">Pass 2: {formatLatency(liveTrace.latency_llm_pass2_ms)}</span>
                      {liveTrace.pass2_response && (
                        <div className="debug-prompt mt-0.5 max-h-[60px]">
                          {liveTrace.pass2_response}
                        </div>
                      )}
                    </div>
                  )}

                  {/* Erreur */}
                  {liveTrace.error && (
                    <div className="mt-1 text-red-400 text-[10px]">
                      Erreur: {liveTrace.error}
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* TTS */}
            {liveTts && (
              <div className="flex items-start gap-3">
                <Volume2 size={14} className="mt-0.5 text-muted-foreground shrink-0" />
                <div className="flex-1 min-w-0">
                  <div className="text-[10px] text-muted-foreground uppercase tracking-wider mb-0.5">
                    {liveTts.timestamp} — TTS
                  </div>
                  <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-[10px] text-muted-foreground">
                    <span>{formatLatency(liveTts.tts_latency_ms)} ({liveTts.tts_engine})</span>
                  </div>
                  <div className="text-foreground mt-0.5 break-words">
                    &ldquo;{liveTts.text}&rdquo;
                  </div>
                </div>
              </div>
            )}

            {/* Latence totale */}
            {liveTrace && (
              <div className="flex items-start gap-3 pt-1.5 border-t border-white/5">
                <Clock size={14} className="mt-0.5 text-muted-foreground shrink-0" />
                <div className="flex-1">
                  <span className="text-[10px] text-muted-foreground uppercase tracking-wider">
                    TOTAL: {formatLatency(liveTrace.latency_total_ms)}
                  </span>
                  {liveTrace.latency_total_ms > 0 && (
                    <div
                      className={`latency-bar mt-1 ${getLatencyColor(liveTrace.latency_total_ms)}`}
                      style={{ width: `${Math.min(100, (liveTrace.latency_total_ms / 50))}%` }}
                    />
                  )}
                </div>
              </div>
            )}
          </div>
        </section>
      )}

      {/* ── HISTORIQUE ──────────────────────────────────────────────────── */}
      <section>
        <h2 className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground mb-2 px-1">
          Historique
        </h2>

        {loading ? (
          <div className="text-xs text-muted-foreground p-4">Chargement...</div>
        ) : logs.length === 0 ? (
          <div className="text-xs text-muted-foreground p-4 border border-dashed border-white/10 rounded-xl text-center">
            Aucune trace. Lance une conversation vocale pour voir les diagnostics.
          </div>
        ) : (
          <div className="space-y-2">
            {logs.map((log) => (
              <div key={log.id}>
                <button
                  onClick={() => toggleExpand(log.id)}
                  className="w-full rounded-xl border border-white/10 bg-white/[0.01] hover:bg-white/[0.03] transition-colors p-3 text-left"
                >
                  <div className="flex items-center gap-2">
                    {expanded === log.id ? (
                      <ChevronDown size={14} className="text-muted-foreground shrink-0" />
                    ) : (
                      <ChevronUp size={14} className="text-muted-foreground shrink-0" />
                    )}
                    <span className="text-[10px] text-muted-foreground font-mono w-16 shrink-0">
                      {log.created_at?.slice(11, 19) || '--:--:--'}
                    </span>
                    <span className="text-xs text-foreground truncate flex-1">
                      &ldquo;{log.input_text?.slice(0, 60) || '(pas de transcription)'}&rdquo;
                    </span>

                    {/* Badges */}
                    <span className="text-[10px] text-muted-foreground font-mono">
                      {log.latency_total_ms > 0 ? formatLatency(log.latency_total_ms) : '--'}
                    </span>
                    <span className="text-[10px] text-muted-foreground font-mono px-1.5 py-0.5 rounded bg-white/5 border border-white/10">
                      {log.stt_engine || '--'}
                    </span>
                    <span className="text-[10px] text-muted-foreground font-mono px-1.5 py-0.5 rounded bg-white/5 border border-white/10">
                      {log.emotion || 'neutral'}
                    </span>
                  </div>
                </button>

                {/* Panneau detail expand/collapse */}
                {expanded === log.id && (
                  <div className="ml-4 mt-1 mb-2 space-y-3 p-3 border-l-2 border-white/10">
                    {/* Metriques rapides */}
                    <div className="flex flex-wrap gap-x-4 gap-y-1 text-[10px] text-muted-foreground font-mono">
                      {log.audio_duration_ms > 0 && (
                        <span>Audio: {formatLatency(log.audio_duration_ms)}</span>
                      )}
                      {log.latency_stt_ms > 0 && (
                        <span>STT: {formatLatency(log.latency_stt_ms)} ({log.stt_engine})</span>
                      )}
                      {log.latency_llm1_ms > 0 && (
                        <span>LLM1: {formatLatency(log.latency_llm1_ms)}</span>
                      )}
                      {log.latency_llm2_ms > 0 && (
                        <span>LLM2: {formatLatency(log.latency_llm2_ms)}</span>
                      )}
                      {log.latency_tts_ms > 0 && (
                        <span>TTS: {formatLatency(log.latency_tts_ms)} ({log.tts_engine})</span>
                      )}
                      {log.latency_total_ms > 0 && (
                        <span>TOTAL: {formatLatency(log.latency_total_ms)}</span>
                      )}
                    </div>

                    {/* Modele + tokens */}
                    {log.model && (
                      <div className="text-[10px] text-muted-foreground font-mono">
                        {log.model} — {log.tokens_in} in / {log.tokens_out} out — {formatCost(log.cost)}
                      </div>
                    )}

                    {/* System prompt */}
                    {log.system_prompt && (
                      <div>
                        <div className="text-[10px] text-muted-foreground uppercase tracking-wider mb-1">
                          System Prompt
                        </div>
                        <div className="debug-prompt max-h-[300px]">{log.system_prompt}</div>
                      </div>
                    )}

                    {/* Messages envoyes */}
                    {log.messages_json && (() => {
                      try {
                        const msgs = JSON.parse(log.messages_json) as Array<{ role: string; content: string }>
                        if (msgs.length === 0) return null
                        return (
                          <div>
                            <div className="text-[10px] text-muted-foreground uppercase tracking-wider mb-1">
                              Messages envoyes
                            </div>
                            <div className="debug-prompt max-h-[200px]">
                              {msgs.map((m, i) => (
                                <div key={i} className="mb-1">
                                  <span className="text-[10px] opacity-50">
                                    {m.role}:
                                  </span>{' '}
                                  {m.content}
                                </div>
                              ))}
                            </div>
                          </div>
                        )
                      } catch {
                        return null
                      }
                    })()}

                    {/* Reponse brute */}
                    {log.raw_response && (
                      <div>
                        <div className="text-[10px] text-muted-foreground uppercase tracking-wider mb-1">
                          Reponse brute LLM
                        </div>
                        <div className="debug-prompt max-h-[200px]">{log.raw_response}</div>
                      </div>
                    )}

                    {/* Reponse clean */}
                    {log.response_clean && log.response_clean !== log.raw_response && (
                      <div>
                        <div className="text-[10px] text-muted-foreground uppercase tracking-wider mb-1">
                          Reponse clean (apres extraction emotion)
                        </div>
                        <div className="debug-prompt">{log.response_clean}</div>
                      </div>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </section>

      <div ref={bottomRef} />
    </div>
  )
}
