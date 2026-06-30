import { useMemo } from "react";
import type { JarvisEvent } from "../../types/mission";

const AGENTS = [
  "orchestrator",
  "info",
  "devops",
  "school",
  "productivity",
  "coach",
  "journal",
  "memory",
  "voice",
];

type AgentState = "idle" | "working" | "done" | "error";

function getAgentStates(events: JarvisEvent[]): Record<string, AgentState> {
  const states: Record<string, AgentState> = {};
  AGENTS.forEach((a) => (states[a] = "idle"));

  const recent = events.slice(-50);
  for (const evt of recent) {
    const agent = evt.agent || (evt.data && typeof evt.data === "object" && (evt.data as Record<string, unknown>).agent) || "";
    if (!agent || typeof agent !== "string") continue;
    const ag = agent.toLowerCase();

    if (evt.type === "agent.start") {
      if (AGENTS.includes(ag)) states[ag] = "working";
      else if (states[ag] === "idle") states[ag] = "working";
    } else if (evt.type === "agent.response") {
      if (AGENTS.includes(ag)) states[ag] = "done";
      else if (states[ag] === "working") states[ag] = "done";
    } else if (evt.type === "agent.error") {
      if (AGENTS.includes(ag)) states[ag] = "error";
      else states[ag] = "error";
    }
  }

  // Voice state from voice events
  const lastVoice = [...events].reverse().find((e) => e.type.startsWith("voice."));
  if (lastVoice?.type === "voice.listening") states["voice"] = "idle";
  else if (lastVoice?.type === "voice.speech_start") states["voice"] = "working";
  else if (lastVoice?.type === "voice.stt_result") states["voice"] = "done";

  return states;
}

const STATE_COLOR: Record<AgentState, string> = {
  idle: "#5a6a7a",
  working: "#a855f7",
  done: "#00ff88",
  error: "#ff6b2b",
};

export default function AgentBar({ events }: { events: JarvisEvent[] }) {
  const states = useMemo(() => getAgentStates(events), [events]);

  return (
    <footer className="agent-bar">
      {AGENTS.map((agent) => (
        <div key={agent} className="agent-pill" data-state={states[agent]}>
          <span
            className={`agent-dot ${states[agent] === "working" ? "dot-pulse" : ""}`}
            style={{ background: STATE_COLOR[states[agent]] }}
          />
          <span className="agent-name">{agent.toUpperCase()}</span>
        </div>
      ))}
    </footer>
  );
}
