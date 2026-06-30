import { useMemo } from "react";
import {
  Mic,
  Brain,
  GitBranch,
  Terminal,
  Volume2,
  CheckCircle,
} from "lucide-react";
import type { JarvisEvent } from "../../types/mission";

interface PipelineNode {
  id: string;
  label: string;
  icon: typeof Mic;
  x: number;
  y: number;
}

const NODES: PipelineNode[] = [
  { id: "mic",     label: "MICRO",        icon: Mic,         x: 60,  y: 180 },
  { id: "stt",     label: "STT",          icon: Brain,       x: 240, y: 180 },
  { id: "route",   label: "ORCHESTRATOR", icon: GitBranch,   x: 420, y: 180 },
  { id: "agent",   label: "AGENT",        icon: Brain,       x: 600, y: 120 },
  { id: "action",  label: "ACTION",       icon: Terminal,    x: 600, y: 240 },
  { id: "tts",     label: "TTS",          icon: Volume2,     x: 780, y: 180 },
];

const EDGES: [string, string][] = [
  ["mic", "stt"],
  ["stt", "route"],
  ["route", "agent"],
  ["route", "action"],
  ["agent", "tts"],
  ["action", "agent"],
];

function getActiveNode(events: JarvisEvent[]): string | null {
  if (!events.length) return null;
  const last = events[events.length - 1];
  if (last.type.startsWith("voice.listening") || last.type.startsWith("voice.speech_start")) return "mic";
  if (last.type.startsWith("voice.stt")) return "stt";
  if (last.type.startsWith("orchestrator")) return "route";
  if (last.type.startsWith("agent.action")) return "action";
  if (last.type.startsWith("agent")) return "agent";
  if (last.type.startsWith("tts")) return "tts";
  if (last.type === "voice.speech_end") return "stt";
  return null;
}

export default function PipelineView({ events }: { events: JarvisEvent[] }) {
  const activeNode = getActiveNode(events);

  const lastResult = useMemo(() => {
    const resp = [...events].reverse().find((e) => e.type === "agent.response");
    return resp?.data && typeof resp.data === "object" && "content" in resp.data
      ? String(resp.data.content).slice(0, 300)
      : null;
  }, [events]);

  const lastAction = useMemo(() => {
    const act = [...events]
      .reverse()
      .find((e) => e.type === "agent.action_result");
    if (!act?.data || typeof act.data !== "object") return null;
    const d = act.data as Record<string, unknown>;
    return `${d.action_type || "?"}: ${String(d.result || "").slice(0, 100)}`;
  }, [events]);

  return (
    <div className="pipeline-panel">
      <div className="pipeline-header">
        <span>PIPELINE LIVE</span>
      </div>
      <div className="pipeline-canvas">
        <svg width="100%" height="100%" viewBox="0 0 900 360">
          {/* Edges */}
          {EDGES.map(([from, to]) => {
            const a = NODES.find((n) => n.id === from)!;
            const b = NODES.find((n) => n.id === to)!;
            const isActive = activeNode === from || activeNode === to;
            return (
              <line
                key={`${from}-${to}`}
                x1={a.x + 40}
                y1={a.y}
                x2={b.x - 40}
                y2={b.y}
                stroke={isActive ? "var(--hud-cyan)" : "var(--hud-border)"}
                strokeWidth={isActive ? 2 : 1}
                strokeDasharray={isActive ? "none" : "4 4"}
                className={isActive ? "edge-glow" : ""}
              />
            );
          })}

          {/* Nodes */}
          {NODES.map((node) => {
            const isActive = activeNode === node.id;
            const Icon = node.icon;
            return (
              <g key={node.id}>
                {isActive && (
                  <circle
                    cx={node.x}
                    cy={node.y}
                    r={38}
                    fill="none"
                    stroke="var(--hud-cyan)"
                    strokeWidth={1}
                    opacity={0.3}
                    className="node-pulse"
                  />
                )}
                <circle
                  cx={node.x}
                  cy={node.y}
                  r={28}
                  fill={isActive ? "var(--hud-cyan-dim)" : "rgba(10, 15, 30, 0.85)"}
                  stroke={isActive ? "var(--hud-cyan)" : "var(--hud-border)"}
                  strokeWidth={isActive ? 2 : 1}
                />
                <foreignObject
                  x={node.x - 12}
                  y={node.y - 12}
                  width={24}
                  height={24}
                >
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      width: "100%",
                      height: "100%",
                    }}
                  >
                    <Icon
                      size={18}
                      color={isActive ? "var(--hud-cyan)" : "#5a6a7a"}
                    />
                  </div>
                </foreignObject>
                <text
                  x={node.x}
                  y={node.y + 52}
                  textAnchor="middle"
                  fill={isActive ? "var(--hud-cyan)" : "var(--hud-text-dim)"}
                  fontSize={9}
                  fontFamily="'JetBrains Mono', monospace"
                  letterSpacing="0.08em"
                >
                  {node.label}
                </text>
              </g>
            );
          })}
        </svg>

        {/* Floating result */}
        {lastResult && (
          <div className="pipeline-result">
            <CheckCircle size={12} className="result-icon" />
            <span>{lastResult}</span>
          </div>
        )}
        {lastAction && (
          <div className="pipeline-action-result">
            <Terminal size={12} />
            <span>{lastAction}</span>
          </div>
        )}
      </div>
    </div>
  );
}
