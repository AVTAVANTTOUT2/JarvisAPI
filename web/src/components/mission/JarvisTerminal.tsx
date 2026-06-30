import { useEffect, useRef } from "react";
import type { JarvisEvent } from "../../types/mission";

const TYPE_PREFIX: Record<string, { label: string; color: string }> = {
  "voice.listening":      { label: "MIC",     color: "var(--hud-text-dim)" },
  "voice.speech_start":   { label: "MIC",     color: "var(--hud-cyan)" },
  "voice.speech_end":     { label: "MIC",     color: "var(--hud-text-dim)" },
  "voice.stt_result":     { label: "STT",     color: "var(--hud-cyan)" },
  "voice.stt_error":      { label: "STT",     color: "var(--hud-orange)" },
  "orchestrator.classify":{ label: "ROUTE",   color: "var(--hud-purple)" },
  "orchestrator.route":   { label: "ROUTE",   color: "var(--hud-purple)" },
  "agent.start":          { label: "AGENT",   color: "var(--hud-purple)" },
  "agent.thinking":       { label: "THINK",   color: "var(--hud-purple)" },
  "agent.action":         { label: "ACTION",  color: "var(--hud-orange)" },
  "agent.action_result":  { label: "RESULT",  color: "var(--hud-green)" },
  "agent.response":       { label: "REPLY",   color: "var(--hud-green)" },
  "agent.error":          { label: "ERROR",   color: "var(--hud-orange)" },
  "tts.start":            { label: "TTS",     color: "var(--hud-cyan)" },
  "tts.playing":          { label: "TTS",     color: "var(--hud-cyan)" },
  "tts.done":             { label: "TTS",     color: "var(--hud-text-dim)" },
  "workflow.step_start":  { label: "FLOW",    color: "var(--hud-cyan)" },
  "workflow.step_done":   { label: "FLOW",    color: "var(--hud-green)" },
  "workflow.step_error":  { label: "FLOW",    color: "var(--hud-orange)" },
  "workflow.complete":    { label: "FLOW",    color: "var(--hud-green)" },
  "system.service_up":    { label: "SYS",     color: "var(--hud-green)" },
  "system.service_down":  { label: "SYS",     color: "var(--hud-orange)" },
  "system.error":         { label: "SYS",     color: "var(--hud-orange)" },
};

function formatTime(ts: number): string {
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString("fr-FR", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function formatEvent(evt: JarvisEvent): string {
  const d = evt.data as Record<string, unknown> | undefined || {};
  switch (evt.type) {
    case "voice.stt_result":
      return `\"${d.transcript || ""}\" [${d.latency_ms || 0}ms \u00b7 ${d.engine || "?"}]`;
    case "orchestrator.classify":
      return `\"${d.message || ""}\" \u2192 ${d.category || "?"} (${d.method || "?"}, ${d.latency_ms || 0}ms)`;
    case "orchestrator.route":
      return `\u2192 agent::${d.agent || "?"}`;
    case "agent.start":
      return `${evt.agent || "?"}::start [model: ${d.model || "?"}]`;
    case "agent.action":
      return `${evt.agent || "?"}::exec ${d.action_type || "?"}(${JSON.stringify(d.action_params || {}).slice(0, 80)})`;
    case "agent.action_result":
      return `${evt.agent || "?"}::result ${d.action_type || "?"} \u2192 ${String(d.result || "").slice(0, 120)} [${d.latency_ms || 0}ms]`;
    case "agent.response":
      return `${evt.agent || "?"}::reply \"${String(d.content || "").slice(0, 150)}\" [${d.tokens_in || 0}\u2192${d.tokens_out || 0} tok, $${Number(d.cost || 0).toFixed(4)}, ${d.latency_ms || 0}ms]`;
    case "agent.error":
      return `${evt.agent || "?"}::ERROR ${d.error || "?"}`;
    case "tts.start":
      return `${d.engine || "?"} (${d.text_length || 0} chars)`;
    case "voice.listening":
      return "en \u00e9coute...";
    case "voice.speech_start":
      return "parole d\u00e9tect\u00e9e";
    case "voice.speech_end":
      return "fin de parole";
    case "system.service_up":
      return `service up: ${d.service || "?"}`;
    case "system.service_down":
      return `service down: ${d.service || "?"}`;
    default:
      return JSON.stringify(d).slice(0, 150);
  }
}

export default function JarvisTerminal({ events }: { events: JarvisEvent[] }) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [events.length]);

  return (
    <div className="terminal-panel">
      <div className="terminal-header">
        <span className="terminal-title">JARVIS://terminal</span>
        <span className="terminal-count">{events.length} events</span>
      </div>
      <div className="terminal-body">
        {events.map((evt, i) => {
          const meta = TYPE_PREFIX[evt.type] || {
            label: "SYS",
            color: "var(--hud-text-dim)",
          };
          return (
            <div
              key={i}
              className="terminal-line"
              style={{ animationDelay: `${i * 0.01}s` }}
            >
              <span className="terminal-time">{formatTime(evt.timestamp)}</span>
              <span className="terminal-tag" style={{ color: meta.color }}>
                [{meta.label}]
              </span>
              <span className="terminal-msg">{formatEvent(evt)}</span>
            </div>
          );
        })}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}
