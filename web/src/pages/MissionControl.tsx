import { useEffect, useRef, useState, useCallback } from "react";
import { Mic, Send, Zap, Activity } from "lucide-react";
import JarvisTerminal from "../components/mission/JarvisTerminal";
import PipelineView from "../components/mission/PipelineView";
import AgentBar from "../components/mission/AgentBar";
import type { JarvisEvent } from "../types/mission";
import { jarvisFetch } from "@unified/lib/api";
import "./mission-control.css";

export default function MissionControl() {
  const [events, setEvents] = useState<JarvisEvent[]>([]);
  const [prompt, setPrompt] = useState("");
  const [sending, setSending] = useState(false);
  const esRef = useRef<EventSource | null>(null);

  // SSE — flux temps reel
  useEffect(() => {
    const es = new EventSource("/api/events/stream");
    esRef.current = es;
    es.onmessage = (msg) => {
      const evt: JarvisEvent = JSON.parse(msg.data);
      setEvents((prev) => [...prev.slice(-500), evt]);
    };
    es.onerror = () => {
      // SSE reconnecte automatiquement — rien a faire
    };
    return () => es.close();
  }, []);

  const handleSend = useCallback(async () => {
    if (!prompt.trim() || sending) return;
    setSending(true);
    try {
      await jarvisFetch("/api/mission/prompt", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: prompt }),
      });
    } catch {
      // Silencieux — les evenements arriveront via SSE
    }
    setPrompt("");
    setSending(false);
  }, [prompt, sending]);

  return (
    <div className="mission-root">
      {/* Scanlines overlay */}
      <div className="scanlines" />

      {/* Header — prompt bar */}
      <header className="mission-header">
        <div className="mission-status">
          <Activity size={14} className="status-pulse" />
          <span>JARVIS MISSION CONTROL</span>
        </div>
        <div className="mission-prompt-bar">
          <Zap size={14} className="prompt-icon" />
          <input
            className="mission-input"
            placeholder="Commande ou instruction..."
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleSend()}
            disabled={sending}
          />
          <button
            onClick={handleSend}
            disabled={sending || !prompt.trim()}
            className="mission-btn"
            title="Envoyer"
          >
            <Send size={14} />
          </button>
          <button className="mission-btn mic-btn" title="Micro (page /voice)" disabled>
            <Mic size={14} />
          </button>
        </div>
      </header>

      {/* Body — terminal + pipeline */}
      <div className="mission-body">
        <JarvisTerminal events={events} />
        <PipelineView events={events} />
      </div>

      {/* Footer — agents */}
      <AgentBar events={events} />
    </div>
  );
}
