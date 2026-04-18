import React, { useState, useRef, useCallback, useEffect } from 'react';
import { Play, Pause, RotateCcw, ChevronRight, Activity, Zap, Trophy, Users, Loader2 } from 'lucide-react';

// ── Types ─────────────────────────────────────────────────────────────────────

interface VehicleReplay {
  id: string;
  x: number;
  y: number;
  px?: number;
  py?: number;
  road: string;
  angle: number;
  color?: string;
}

interface TickFrame {
  tick: number;
  vehicles: VehicleReplay[];
  phase: 'NS' | 'EW';
  in_yellow: boolean;
  queues: { N: number; S: number; E: number; W: number };
  lights?: { N?: string; S?: string; E?: string; W?: string };
}

interface ScenarioResult {
  name: string;
  score: number;
  avg_wait: number;
  throughput: number;
  max_queue: number;
  replay_data: TickFrame[];
  error?: string;
}

interface LeaderEntry {
  rank: number;
  username: string;
  score: number;
  date: string;
}

interface EvalDetail {
  level: number;
  status: string;
  score: number;
  total_delay: number;
  max_queue_length: number;
  throughput: number;
  error?: string;
  error_log?: string;
  replay_data?: TickFrame[];
  ticks_completed?: number;
  level_label?: string;
  spawn_rate?: number;
  bus_ratio?: number;
}

const LEVEL_NAMES: Record<number, string> = {
  1: 'Low Traffic',
  2: 'Light Traffic',
  3: 'Balanced',
  4: 'Heavy Traffic',
  5: 'Rush Hour',
};

// ── Constants ─────────────────────────────────────────────────────────────────

const W = 520;
const H = 520;
const WORLD_HALF_M = 500;
const SCALE = 0.52;
const ARM_PX = WORLD_HALF_M * SCALE;
const ROAD_HALF_PX = 22;
const STOP_LINE_M = 28;

const DEFAULT_CODE = `# Traffic Light Controller
# ─────────────────────────────────────────────────────────
# Arguments:
#   queues       : dict  — {N, S, E, W}  vehicle counts waiting
#   current_phase: str   — 'NS' or 'EW'
#   phase_timer  : float — seconds elapsed in current phase
#
# Return: 'NS', 'EW', or 'yellow'
# ─────────────────────────────────────────────────────────

def control(queues, current_phase, phase_timer):
    if current_phase == 'NS':
        if phase_timer >= 30:
            return 'yellow'
    elif current_phase == 'EW':
        if phase_timer >= 20:
            return 'yellow'
    return current_phase`;

const MOCK_LEADERBOARD: LeaderEntry[] = [
  { rank: 1, username: 'adaptive_v3.py',      score: 94.2, date: '2026-03-28' },
  { rank: 2, username: 'demand_queue_opt.py', score: 88.7, date: '2026-03-30' },
  { rank: 3, username: 'greedy_ns.py',        score: 81.0, date: '2026-04-01' },
  { rank: 4, username: 'baseline_30_20.py',   score: 67.3, date: '2026-03-25' },
];

// ── Canvas Drawing ────────────────────────────────────────────────────────────

function drawRoads(ctx: CanvasRenderingContext2D, q: { N: number; S: number; E: number; W: number } = { N: 0, S: 0, E: 0, W: 0 }) {
  const Wc = ctx.canvas.width;
  const Hc = ctx.canvas.height;
  const cx = Wc / 2;
  const cy = Hc / 2;
  const arm = ARM_PX;
  const halfW = ROAD_HALF_PX;
  const lane = 10;

  // Background
  ctx.fillStyle = '#1a1f1a';
  ctx.fillRect(0, 0, Wc, Hc);

  // Road surfaces
  ctx.fillStyle = '#2a2a2a';
  ctx.fillRect(cx - halfW, 0,      2 * halfW, Hc);
  ctx.fillRect(0,           cy - halfW, Wc, 2 * halfW);

  // Queue tint overlays
  const qTint = (x: number, y: number, w: number, h: number, count: number) => {
    if (count < 2) return;
    const alpha = Math.min(0.35, count / 12);
    ctx.fillStyle = `rgba(255,69,69,${alpha})`;
    ctx.fillRect(x, y, w, h);
  };
  qTint(cx - halfW, 0,          2 * halfW, cy - halfW, q.N);
  qTint(cx - halfW, cy + halfW, 2 * halfW, Hc - cy - halfW, q.S);
  qTint(0,          cy - halfW, cx - halfW, 2 * halfW, q.W);
  qTint(cx + halfW, cy - halfW, Wc - cx - halfW, 2 * halfW, q.E);

  // Centre-line dashes
  ctx.strokeStyle = '#444';
  ctx.lineWidth = 1;
  ctx.setLineDash([10, 12]);
  // N arm
  ctx.beginPath(); ctx.moveTo(cx, 0);      ctx.lineTo(cx, cy - halfW); ctx.stroke();
  // S arm
  ctx.beginPath(); ctx.moveTo(cx, cy + halfW); ctx.lineTo(cx, Hc);    ctx.stroke();
  // W arm
  ctx.beginPath(); ctx.moveTo(0, cy);      ctx.lineTo(cx - halfW, cy); ctx.stroke();
  // E arm
  ctx.beginPath(); ctx.moveTo(cx + halfW, cy); ctx.lineTo(Wc, cy);    ctx.stroke();
  ctx.setLineDash([]);

  // Stop lines
  const stopPx = STOP_LINE_M * SCALE;
  ctx.strokeStyle = '#fff';
  ctx.lineWidth = 2;
  ctx.beginPath(); ctx.moveTo(cx - halfW, cy - stopPx); ctx.lineTo(cx + halfW, cy - stopPx); ctx.stroke();
  ctx.beginPath(); ctx.moveTo(cx - halfW, cy + stopPx); ctx.lineTo(cx + halfW, cy + stopPx); ctx.stroke();
  ctx.beginPath(); ctx.moveTo(cx - stopPx, cy - halfW); ctx.lineTo(cx - stopPx, cy + halfW); ctx.stroke();
  ctx.beginPath(); ctx.moveTo(cx + stopPx, cy - halfW); ctx.lineTo(cx + stopPx, cy + halfW); ctx.stroke();

  // Direction labels
  ctx.fillStyle = 'rgba(255,255,255,0.25)';
  ctx.font = '11px IBM Plex Mono, monospace';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText('N', cx + lane / 2, 16);
  ctx.fillText('S', cx - lane / 2, Hc - 8);
  ctx.fillText('W', 16,            cy - lane / 2);
  ctx.fillText('E', Wc - 16,      cy + lane / 2);
}

function drawSignalBoxes(ctx: CanvasRenderingContext2D, frame: TickFrame | null) {
  const cx = ctx.canvas.width / 2;
  const cy = ctx.canvas.height / 2;
  const halfW = ROAD_HALF_PX;
  const phase    = frame?.phase ?? 'NS';
  const inYellow = frame?.in_yellow ?? false;

  const corners = [
    { x: cx + halfW + 8, y: cy + halfW + 8, isNS: true  },  // SE corner
    { x: cx - halfW - 8, y: cy - halfW - 8, isNS: true  },  // NW corner
    { x: cx + halfW + 8, y: cy - halfW - 8, isNS: false },  // NE corner
    { x: cx - halfW - 8, y: cy + halfW + 8, isNS: false },  // SW corner
  ];

  for (const { x, y, isNS } of corners) {
    const green  = isNS ? (phase === 'NS' && !inYellow) : (phase === 'EW' && !inYellow);
    const yellow = inYellow;
    const red    = !green && !yellow;
    const s = 5;

    ctx.fillStyle = '#111';
    ctx.beginPath();
    ctx.roundRect(x - s, y - s * 4, s * 2, s * 9, 2);
    ctx.fill();

    ctx.fillStyle = (red || yellow) ? '#ff4545' : 'rgba(255,69,69,0.15)';
    ctx.beginPath(); ctx.arc(x, y - s * 2.5, s * 0.7, 0, Math.PI * 2); ctx.fill();

    ctx.fillStyle = yellow ? '#ffaa00' : 'rgba(255,170,0,0.15)';
    ctx.beginPath(); ctx.arc(x, y, s * 0.7, 0, Math.PI * 2); ctx.fill();

    ctx.fillStyle = green ? '#3ddc84' : 'rgba(61,220,132,0.15)';
    ctx.beginPath(); ctx.arc(x, y + s * 2.5, s * 0.7, 0, Math.PI * 2); ctx.fill();
  }
}

function drawVehicles(ctx: CanvasRenderingContext2D, vehicles: VehicleReplay[]) {
  const cw = ctx.canvas.width;
  const ch = ctx.canvas.height;
  const CENTER_X = cw / 2;
  const CENTER_Y = ch / 2;

  for (const v of vehicles) {
    ctx.save();

    let canvasX: number;
    let canvasY: number;
    const hasPx =
      typeof v.px === 'number' &&
      typeof v.py === 'number' &&
      Number.isFinite(v.px) &&
      Number.isFinite(v.py);

    if (hasPx) {
      canvasX = v.px!;
      canvasY = v.py!;
    } else {
      const x = Number(v.x);
      const y = Number(v.y);
      if (Number.isNaN(x) || Number.isNaN(y)) { ctx.restore(); continue; }
      canvasX = CENTER_X + x * SCALE;
      canvasY = CENTER_Y + y * SCALE;
    }

    ctx.translate(canvasX, canvasY);
    const ang = typeof v.angle === 'number' && !Number.isNaN(v.angle) ? v.angle : 0;
    ctx.rotate(ang);

    const bw = 8, bh = 14;
    ctx.fillStyle = v.color || '#f5d000';
    ctx.strokeStyle = 'rgba(0,0,0,0.4)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.roundRect(-bw / 2, -bh / 2, bw, bh, 2);
    ctx.fill();
    ctx.stroke();

    // Windshield
    ctx.fillStyle = 'rgba(200,230,255,0.5)';
    ctx.fillRect(-bw / 2 + 1, -bh / 2 + 2, bw - 2, 3);

    ctx.restore();
  }
}

function renderFrame(ctx: CanvasRenderingContext2D, frame: TickFrame | null) {
  const canvas = ctx.canvas;
  if (!canvas) return;
  const cw = canvas.width, ch = canvas.height;
  if (cw <= 0 || ch <= 0) return;

  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, cw, ch);

  drawRoads(ctx, frame?.queues ?? { N: 0, S: 0, E: 0, W: 0 });

  if (!frame) {
    ctx.fillStyle = '#555';
    ctx.font = '13px IBM Plex Mono, monospace';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText('Submit code and run judge to see replay', cw / 2, ch / 2);
    return;
  }

  drawSignalBoxes(ctx, frame);
  drawVehicles(ctx, frame.vehicles);

  // HUD overlay
  ctx.fillStyle = 'rgba(200,255,0,0.85)';
  ctx.font = '10px IBM Plex Mono, monospace';
  ctx.textAlign = 'left';
  ctx.textBaseline = 'top';
  const phaseStr = frame.in_yellow ? 'YELLOW' : `${frame.phase} GREEN`;
  ctx.fillText(`Tick ${frame.tick} | ${frame.vehicles.length} veh | ${phaseStr}`, 10, 10);
}

// ── Default Code Examples ─────────────────────────────────────────────────────

const EXAMPLES: Record<string, string> = {
  fixed: `# Fixed-time controller (baseline)
def control(queues, current_phase, phase_timer):
    if current_phase == 'NS':
        if phase_timer >= 30:
            return 'yellow'
    elif current_phase == 'EW':
        if phase_timer >= 20:
            return 'yellow'
    return current_phase`,

  adaptive: `# Adaptive controller
def control(queues, current_phase, phase_timer):
    ns_total = queues['N'] + queues['S']
    ew_total = queues['E'] + queues['W']

    if current_phase == 'NS':
        green_time = 25 + min(15, ns_total * 2)
        if ew_total > 8 and phase_timer > 20:
            return 'yellow'
        if phase_timer >= green_time:
            return 'yellow'
    else:
        green_time = 18 + min(15, ew_total * 2)
        if ns_total > 8 and phase_timer > 15:
            return 'yellow'
        if phase_timer >= green_time:
            return 'yellow'
    return current_phase`,

  demand: `# Demand-based controller
def control(queues, current_phase, phase_timer):
    ns = queues['N'] + queues['S']
    ew = queues['E'] + queues['W']

    if phase_timer < 12:
        return current_phase

    if current_phase == 'NS':
        if ew > ns * 1.8 and phase_timer > 15:
            return 'yellow'
        if phase_timer >= 35:
            return 'yellow'
    else:
        if ns > ew * 1.8 and phase_timer > 12:
            return 'yellow'
        if phase_timer >= 28:
            return 'yellow'
    return current_phase`,
};

// ── Component ─────────────────────────────────────────────────────────────────

export default function App() {
  const [activeTab, setActiveTab] = useState<'sim' | 'judge' | 'lb'>('sim');
  const [code, setCode]           = useState(DEFAULT_CODE);
  const [isJudging, setIsJudging] = useState(false);
  const [overallScore, setOverallScore] = useState<number | null>(null);
  const [results, setResults]       = useState<ScenarioResult[]>([]);
  const [evalDetails, setEvalDetails] = useState<EvalDetail[]>([]);
  const [selectedScenario, setSelectedScenario] = useState<number | null>(null);

  const [playing, setPlaying]         = useState(false);
  const [playbackSpeed, setPlaybackSpeed] = useState(5);
  const [currentFrame, setCurrentFrame] = useState<TickFrame | null>(null);

  const canvasRef   = useRef<HTMLCanvasElement>(null);
  const rafRef      = useRef<number | null>(null);
  const framesRef   = useRef<TickFrame[]>([]);
  const tickRef     = useRef(0);
  const lastTsRef   = useRef(0);
  const playingRef  = useRef(false);
  const speedRef    = useRef(5);

  useEffect(() => { playingRef.current = playing; }, [playing]);
  useEffect(() => { speedRef.current = playbackSpeed; }, [playbackSpeed]);

  const draw = useCallback((frame: TickFrame | null) => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    renderFrame(ctx, frame);
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    canvas.width  = W;
    canvas.height = H;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    renderFrame(ctx, null);
  }, []);

  useEffect(() => { draw(currentFrame); }, [currentFrame, draw]);

  // Playback loop
  useEffect(() => {
    if (rafRef.current) { cancelAnimationFrame(rafRef.current); rafRef.current = null; }
    if (!playing || framesRef.current.length === 0) return;

    lastTsRef.current = 0;
    const loop = (ts: number) => {
      try {
        if (!playingRef.current) return;
        if (!lastTsRef.current) lastTsRef.current = ts;
        const dt = Math.min((ts - lastTsRef.current) / 1000, 0.1);
        lastTsRef.current = ts;

        const frames = framesRef.current;
        if (frames.length === 0) return;

        tickRef.current += dt * speedRef.current;
        if (tickRef.current >= frames.length - 1) {
          tickRef.current = frames.length - 1;
          const last = frames[frames.length - 1];
          setCurrentFrame(last); draw(last); setPlaying(false); return;
        }

        const frame = frames[Math.floor(tickRef.current)];
        setCurrentFrame(frame); draw(frame);
        rafRef.current = requestAnimationFrame(loop);
      } catch (err) {
        console.error('Playback error:', err);
        setPlaying(false);
      }
    };
    rafRef.current = requestAnimationFrame(loop);
    return () => { if (rafRef.current) { cancelAnimationFrame(rafRef.current); rafRef.current = null; } };
  }, [playing, draw]);

  // ── Judge ─────────────────────────────────────────────────────────────────

  const runJudge = async () => {
    setIsJudging(true);
    setOverallScore(null);
    setResults([]);
    setEvalDetails([]);
    setSelectedScenario(null);
    setCurrentFrame(null);
    setPlaying(false);
    framesRef.current = [];
    tickRef.current = 0;
    if (rafRef.current) { cancelAnimationFrame(rafRef.current); rafRef.current = null; }

    try {
      const res = await fetch('http://localhost:8000/evaluate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ error: 'Server error' }));
        throw new Error(err.error || err.detail || `Server error ${res.status}`);
      }
      const data = await res.json();
      if (data.error) throw new Error(data.error);

      const hasNewFormat = 'final_score' in data && 'details' in data;
      if (hasNewFormat) {
        setOverallScore(data.final_score);
        const details: EvalDetail[] = data.details ?? [];
        setEvalDetails(details);
        setResults([]);
        const firstReplay = details.find((d) => d.replay_data && d.replay_data.length > 0);
        if (firstReplay?.replay_data) {
          if (rafRef.current) { cancelAnimationFrame(rafRef.current); rafRef.current = null; }
          setPlaying(false);
          tickRef.current = 0;
          framesRef.current = firstReplay.replay_data;
          setSelectedScenario(null);
          setCurrentFrame(firstReplay.replay_data[0]);
          setActiveTab('sim');
        }
      } else {
        setOverallScore(data.overall_score);
        setResults(data.scenarios);
        setEvalDetails([]);
        const firstValid = data.scenarios.findIndex((s: ScenarioResult) => s.replay_data && s.replay_data.length > 0);
        if (firstValid >= 0) {
          const frames = data.scenarios[firstValid].replay_data;
          framesRef.current = frames;
          tickRef.current = 0;
          setSelectedScenario(firstValid);
          setCurrentFrame(frames[0]);
          setActiveTab('sim');
          setPlaying(true);
        }
      }
    } catch (err: any) {
      alert(`Error: ${err.message}`);
    } finally {
      setIsJudging(false);
    }
  };

  const selectScenario = useCallback((idx: number) => {
    const frames = results[idx]?.replay_data;
    if (!frames || frames.length === 0) return;
    if (rafRef.current) { cancelAnimationFrame(rafRef.current); rafRef.current = null; }
    setPlaying(false);
    tickRef.current = 0;
    framesRef.current = frames;
    setSelectedScenario(idx);
    setCurrentFrame(frames[0]);
    setActiveTab('sim');
  }, [results]);

  const selectEvalReplay = useCallback((d: EvalDetail) => {
    const frames = d.replay_data;
    if (!frames || frames.length === 0) return;
    if (rafRef.current) { cancelAnimationFrame(rafRef.current); rafRef.current = null; }
    setPlaying(false);
    tickRef.current = 0;
    framesRef.current = frames;
    setSelectedScenario(null);
    setCurrentFrame(frames[0]);
    setActiveTab('sim');
  }, []);

  const togglePlay = useCallback(() => {
    if (!framesRef.current.length) return;
    setPlaying((p) => !p);
  }, []);

  const resetPlayback = useCallback(() => {
    if (rafRef.current) { cancelAnimationFrame(rafRef.current); rafRef.current = null; }
    tickRef.current = 0;
    setPlaying(false);
    if (framesRef.current.length > 0) setCurrentFrame(framesRef.current[0]);
  }, []);

  const queues = currentFrame?.queues ?? { N: 0, S: 0, E: 0, W: 0 };
  const DIRS = ['N', 'S', 'E', 'W'] as const;

  // Signal state derived from frame
  const phase     = currentFrame?.phase ?? null;
  const inYellow  = currentFrame?.in_yellow ?? false;
  const nsGreen   = phase === 'NS' && !inYellow;
  const ewGreen   = phase === 'EW' && !inYellow;
  const nsRed     = !nsGreen && !inYellow;
  const ewRed     = !ewGreen && !inYellow;

  const leaderboard = overallScore !== null
    ? [
        ...MOCK_LEADERBOARD.filter(e => e.score > overallScore),
        { rank: 0, username: 'your_solution.py', score: overallScore, date: 'Now' },
        ...MOCK_LEADERBOARD.filter(e => e.score <= overallScore),
      ].map((e, i) => ({ ...e, rank: i + 1 }))
    : MOCK_LEADERBOARD;

  // ── CSS-in-JS style tokens matching original HTML ──────────────────────────
  const vars = {
    bg0: '#0f0f0f', bg1: '#161616', bg2: '#1e1e1e', bg3: '#272727',
    border: '#2e2e2e', borderHi: '#444',
    text0: '#e8e8e8', text1: '#a8a8a8', text2: '#666',
    accent: '#c8ff00', accentDim: 'rgba(200,255,0,0.08)',
    red: '#ff4545', redDim: 'rgba(255,69,69,0.1)',
    amber: '#ffaa00', green: '#3ddc84', greenDim: 'rgba(61,220,132,0.1)',
    blue: '#4c9fff',
  };

  return (
    <div style={{ display: 'grid', gridTemplateRows: '40px 1fr', height: '100vh', background: vars.bg0, color: vars.text0, fontFamily: "'IBM Plex Sans', sans-serif", fontSize: 13 }}>

      {/* TOPBAR */}
      <div style={{ display: 'flex', alignItems: 'center', background: vars.bg1, borderBottom: `1px solid ${vars.border}`, padding: '0 16px' }}>
        <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 11, fontWeight: 600, color: vars.accent, letterSpacing: '0.12em', textTransform: 'uppercase', marginRight: 24 }}>
          TrafficJudge
        </div>
        {(['sim', 'judge', 'lb'] as const).map((tab) => (
          <div
            key={tab}
            onClick={() => setActiveTab(tab)}
            style={{
              padding: '0 14px', height: 40, display: 'flex', alignItems: 'center',
              fontFamily: "'IBM Plex Mono', monospace", fontSize: 11, letterSpacing: '0.04em',
              color: activeTab === tab ? vars.text0 : vars.text2,
              borderBottom: `2px solid ${activeTab === tab ? vars.accent : 'transparent'}`,
              cursor: 'pointer', transition: 'color .15s, border-color .15s',
            }}
          >
            {tab === 'sim' ? 'Simulation' : tab === 'judge' ? 'Submit' : 'Leaderboard'}
          </div>
        ))}
        <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 12, fontFamily: "'IBM Plex Mono', monospace", fontSize: 10, color: vars.text2 }}>
          <span style={{ color: vars.text2 }}>Engine: SUMO / TraCI</span>
        </div>
      </div>

      {/* PANELS */}
      <div style={{ overflow: 'hidden', height: '100%' }}>

        {/* ── SIMULATION ── */}
        {activeTab === 'sim' && (
          <div style={{ display: 'flex', height: '100%' }}>
            {/* Canvas area */}
            <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', background: vars.bg0, position: 'relative', overflow: 'hidden' }}>
              <div style={{ position: 'absolute', inset: 0, backgroundImage: 'radial-gradient(circle, #2e2e2e 1px, transparent 1px)', backgroundSize: '24px 24px', opacity: 0.4, pointerEvents: 'none' }} />
              <canvas ref={canvasRef} width={W} height={H} style={{ position: 'relative', zIndex: 1, display: 'block' }} />
            </div>

            {/* Sidebar */}
            <div style={{ width: 240, background: vars.bg1, borderLeft: `1px solid ${vars.border}`, display: 'flex', flexDirection: 'column', overflowY: 'auto' }}>

              {/* Signal state */}
              <div style={{ borderBottom: `1px solid ${vars.border}`, padding: '12px 14px' }}>
                <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 9, fontWeight: 600, color: vars.text2, letterSpacing: '0.12em', textTransform: 'uppercase', marginBottom: 10 }}>Signal State</div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                  {([['N — S', nsGreen, nsRed], ['E — W', ewGreen, ewRed]] as [string, boolean, boolean][]).map(([label, green, red]) => (
                    <div key={label} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4 }}>
                      <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 9, color: vars.text2 }}>{label}</div>
                      <div style={{ background: '#111', border: `1px solid ${vars.border}`, borderRadius: 4, padding: '6px 8px', display: 'flex', flexDirection: 'column', gap: 3, alignItems: 'center' }}>
                        {[
                          { on: red || inYellow, cls: '#ff4545', glow: '#ff4545aa' },
                          { on: inYellow, cls: '#ffaa00', glow: '#ffaa00aa' },
                          { on: green,    cls: '#3ddc84', glow: '#3ddc84aa' },
                        ].map((dot, i) => (
                          <div key={i} style={{ width: 10, height: 10, borderRadius: '50%', background: dot.on ? dot.cls : 'transparent', border: dot.on ? 'none' : `1px solid ${vars.border}`, opacity: dot.on ? 1 : 0.2, boxShadow: dot.on ? `0 0 6px ${dot.glow}` : 'none', transition: 'opacity .25s, box-shadow .25s' }} />
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginTop: 8 }}>
                  <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 11, color: inYellow ? vars.amber : vars.green }}>
                    {phase ? (inYellow ? 'Yellow' : `${phase} Green`) : '—'}
                  </span>
                  <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 18, fontWeight: 600, color: vars.accent }}>
                    {currentFrame ? currentFrame.tick : '—'}
                  </span>
                </div>
              </div>

              {/* Queue depth */}
              <div style={{ borderBottom: `1px solid ${vars.border}`, padding: '12px 14px' }}>
                <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 9, fontWeight: 600, color: vars.text2, letterSpacing: '0.12em', textTransform: 'uppercase', marginBottom: 10 }}>Queue Depth</div>
                {DIRS.map((dir) => {
                  const count = queues[dir];
                  const pct = Math.min(100, (count / 15) * 100);
                  const barColor = count >= 8 ? vars.red : count >= 4 ? vars.amber : vars.green;
                  return (
                    <div key={dir} style={{ marginBottom: 4 }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', fontFamily: "'IBM Plex Mono', monospace", fontSize: 10, color: vars.text1, marginBottom: 2 }}>
                        <span>{dir}</span><span>{count}</span>
                      </div>
                      <div style={{ height: 4, background: vars.bg3, borderRadius: 2, overflow: 'hidden' }}>
                        <div style={{ height: '100%', width: `${pct}%`, background: barColor, borderRadius: 2, transition: 'width .3s, background .3s' }} />
                      </div>
                    </div>
                  );
                })}
              </div>

              {/* Playback */}
              <div style={{ borderBottom: `1px solid ${vars.border}`, padding: '12px 14px' }}>
                <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 9, fontWeight: 600, color: vars.text2, letterSpacing: '0.12em', textTransform: 'uppercase', marginBottom: 10 }}>Playback</div>
                <div style={{ display: 'flex', gap: 6, marginBottom: 8 }}>
                  <button
                    onClick={togglePlay}
                    disabled={!currentFrame}
                    style={{
                      flex: 1, fontFamily: "'IBM Plex Mono', monospace", fontSize: 11, padding: '6px 12px',
                      border: `1px solid ${playing ? vars.red : vars.accent}`,
                      background: playing ? vars.redDim : vars.accentDim,
                      color: playing ? vars.red : vars.accent,
                      cursor: 'pointer', borderRadius: 3, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
                      opacity: !currentFrame ? 0.4 : 1,
                    }}
                  >
                    {playing ? <Pause size={11} /> : <Play size={11} />}
                    {playing ? 'Pause' : 'Play'}
                  </button>
                  <button
                    onClick={resetPlayback}
                    disabled={!currentFrame}
                    style={{
                      fontFamily: "'IBM Plex Mono', monospace", fontSize: 11, padding: '6px 10px',
                      border: `1px solid ${vars.borderHi}`, background: vars.bg3, color: vars.text0,
                      cursor: 'pointer', borderRadius: 3, opacity: !currentFrame ? 0.4 : 1,
                    }}
                  >
                    <RotateCcw size={12} />
                  </button>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <label style={{ fontSize: 10, color: vars.text1, width: 40, fontFamily: "'IBM Plex Mono', monospace" }}>Speed</label>
                  <input
                    type="range" min={1} max={20} value={playbackSpeed}
                    onChange={(e) => setPlaybackSpeed(parseInt(e.target.value))}
                    style={{ flex: 1, accentColor: vars.accent }}
                  />
                  <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 10, color: vars.text0, width: 28, textAlign: 'right' }}>{playbackSpeed}x</span>
                </div>
              </div>

              {/* Scenario list (after eval) */}
              {(results.length > 0 || evalDetails.length > 0) && (
                <div style={{ padding: '12px 14px', flex: 1 }}>
                  <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 9, fontWeight: 600, color: vars.text2, letterSpacing: '0.12em', textTransform: 'uppercase', marginBottom: 10 }}>Results — Click to Replay</div>
                  {evalDetails.length > 0 ? evalDetails.map((d, i) => {
                    const name = d.level_label ?? LEVEL_NAMES[d.level] ?? `Level ${d.level}`;
                    const can = Boolean(d.replay_data && d.replay_data.length > 0);
                    const sc  = d.score ?? 0;
                    return (
                      <button key={i} onClick={() => can && selectEvalReplay(d)}
                        style={{ width: '100%', textAlign: 'left', padding: '6px 8px', background: 'transparent', border: `1px solid ${vars.border}`, borderRadius: 3, cursor: can ? 'pointer' : 'default', marginBottom: 4, color: vars.text0 }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', fontFamily: "'IBM Plex Mono', monospace", fontSize: 10 }}>
                          <span style={{ color: vars.text1 }}>L{d.level} {name}</span>
                          <span style={{ color: sc >= 75 ? vars.green : sc >= 50 ? vars.amber : vars.red }}>{sc}</span>
                        </div>
                      </button>
                    );
                  }) : results.map((r, i) => (
                    <button key={i} onClick={() => selectScenario(i)}
                      style={{ width: '100%', textAlign: 'left', padding: '6px 8px', background: selectedScenario === i ? vars.accentDim : 'transparent', border: `1px solid ${selectedScenario === i ? vars.accent : vars.border}`, borderRadius: 3, cursor: 'pointer', marginBottom: 4, color: vars.text0 }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', fontFamily: "'IBM Plex Mono', monospace", fontSize: 10 }}>
                        <span style={{ color: vars.text1 }}>{r.name}</span>
                        <span style={{ color: vars.accent }}>{r.score}</span>
                      </div>
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}

        {/* ── SUBMIT ── */}
        {activeTab === 'judge' && (
          <div style={{ display: 'flex', height: '100%' }}>
            {/* Editor */}
            <div style={{ flex: 1, display: 'flex', flexDirection: 'column', borderRight: `1px solid ${vars.border}` }}>
              <div style={{ padding: '10px 16px', background: vars.bg1, borderBottom: `1px solid ${vars.border}`, display: 'flex', alignItems: 'center', gap: 10 }}>
                <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 11, color: vars.text1, flex: 1 }}>controller.py — traffic light controller</span>
                {Object.keys(EXAMPLES).map((key) => (
                  <button key={key} onClick={() => setCode(EXAMPLES[key])}
                    style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 10, padding: '4px 10px', border: `1px solid ${vars.borderHi}`, background: vars.bg3, color: vars.text1, cursor: 'pointer', borderRadius: 2 }}>
                    {key.charAt(0).toUpperCase() + key.slice(1)}
                  </button>
                ))}
              </div>
              <textarea
                value={code}
                onChange={(e) => setCode(e.target.value)}
                spellCheck={false}
                style={{ flex: 1, padding: 16, fontFamily: "'IBM Plex Mono', monospace", fontSize: 12, lineHeight: 1.65, background: vars.bg0, border: 'none', color: vars.text0, resize: 'none', outline: 'none', tabSize: 4 }}
              />
              <div style={{ padding: '8px 16px', background: vars.bg1, borderTop: `1px solid ${vars.border}`, display: 'flex', gap: 8, alignItems: 'center' }}>
                <button
                  onClick={runJudge}
                  disabled={isJudging}
                  style={{
                    fontFamily: "'IBM Plex Mono', monospace", fontSize: 11, padding: '6px 20px',
                    border: `1px solid ${vars.accent}`, background: vars.accentDim, color: vars.accent,
                    cursor: isJudging ? 'not-allowed' : 'pointer', borderRadius: 3,
                    display: 'flex', alignItems: 'center', gap: 6, opacity: isJudging ? 0.6 : 1,
                  }}
                >
                  {isJudging ? <><Loader2 size={12} className="animate-spin" /> Running...</> : <><Zap size={12} /> Run Judge (5 scenarios)</>}
                </button>
                <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 10, color: vars.text2, marginLeft: 'auto' }}>
                  {isJudging ? 'Evaluating via SUMO / TraCI...' : overallScore !== null ? `Score: ${overallScore}/100` : 'Not submitted'}
                </span>
              </div>
            </div>

            {/* Results sidebar */}
            <div style={{ width: 280, background: vars.bg1, display: 'flex', flexDirection: 'column', overflowY: 'auto' }}>
              {isJudging ? (
                <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', padding: 32 }}>
                  <Loader2 size={32} style={{ color: vars.accent, marginBottom: 16 }} className="animate-spin" />
                  <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 12, color: vars.text1, textAlign: 'center', lineHeight: 1.8 }}>Running simulations across 5 difficulty levels...</div>
                </div>
              ) : (
                <>
                  {/* Score hero */}
                  <div style={{ padding: '20px 16px', borderBottom: `1px solid ${vars.border}`, textAlign: 'center' }}>
                    <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 48, fontWeight: 600, lineHeight: 1, color: overallScore !== null ? (overallScore >= 75 ? vars.green : overallScore >= 50 ? vars.amber : vars.red) : vars.text0 }}>
                      {overallScore !== null ? overallScore : '—'}
                    </div>
                    <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 14, color: vars.text2 }}>/ 100</div>
                    <div style={{ fontSize: 11, color: vars.text2, marginTop: 6, fontFamily: "'IBM Plex Mono', monospace" }}>
                      {overallScore !== null ? 'evaluation complete' : 'submit to see score'}
                    </div>
                  </div>

                  {/* Scenario list */}
                  <div style={{ padding: '12px 14px', borderBottom: `1px solid ${vars.border}` }}>
                    <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 9, fontWeight: 600, color: vars.text2, letterSpacing: '0.12em', textTransform: 'uppercase', marginBottom: 10 }}>Scenarios</div>
                    {evalDetails.length > 0 ? evalDetails.map((d, i) => {
                      const name = d.level_label ?? LEVEL_NAMES[d.level] ?? `Level ${d.level}`;
                      const sc   = d.score ?? 0;
                      const barColor = sc >= 75 ? vars.green : sc >= 50 ? vars.amber : vars.red;
                      const can  = Boolean(d.replay_data && d.replay_data.length > 0);
                      return (
                        <div key={i} onClick={() => can && selectEvalReplay(d)} style={{ marginBottom: 8, cursor: can ? 'pointer' : 'default' }}>
                          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 3 }}>
                            <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 10, color: vars.text1 }}>L{d.level} {name}</span>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                              <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 10, color: vars.text2 }}>{sc}</span>
                              <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 9, padding: '1px 5px', borderRadius: 2, background: d.status === 'OK' ? vars.greenDim : vars.redDim, color: d.status === 'OK' ? vars.green : vars.red, border: `1px solid ${d.status === 'OK' ? vars.green : vars.red}` }}>
                                {d.status}
                              </span>
                            </div>
                          </div>
                          <div style={{ height: 3, background: vars.bg3, borderRadius: 2, overflow: 'hidden' }}>
                            <div style={{ height: '100%', width: `${Math.min(100, sc)}%`, background: barColor, borderRadius: 2, transition: 'width .4s' }} />
                          </div>
                          {d.error && d.status !== 'OK' && (
                            <div style={{ marginTop: 4, fontSize: 9, fontFamily: "'IBM Plex Mono', monospace", color: vars.red }}>{d.error}</div>
                          )}
                        </div>
                      );
                    }) : results.length > 0 ? results.map((s, i) => {
                      const sc = typeof s.score === 'number' ? s.score : 0;
                      const barColor = sc >= 75 ? vars.green : sc >= 50 ? vars.amber : vars.red;
                      return (
                        <div key={i} onClick={() => selectScenario(i)} style={{ marginBottom: 8, cursor: 'pointer' }}>
                          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 3 }}>
                            <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 10, color: vars.text1 }}>{s.name}</span>
                            <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 10, color: vars.text2 }}>{sc}</span>
                          </div>
                          <div style={{ height: 3, background: vars.bg3, borderRadius: 2, overflow: 'hidden' }}>
                            <div style={{ height: '100%', width: `${sc}%`, background: barColor, borderRadius: 2 }} />
                          </div>
                        </div>
                      );
                    }) : (
                      <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 10, color: vars.text2 }}>Submit code to evaluate.</div>
                    )}
                  </div>

                  {/* How it works */}
                  <div style={{ padding: '12px 14px', flex: 1 }}>
                    <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 9, fontWeight: 600, color: vars.text2, letterSpacing: '0.12em', textTransform: 'uppercase', marginBottom: 10 }}>How It Works</div>
                    <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 10, color: vars.text2, lineHeight: 1.8 }}>
                      <div>1. Write a <span style={{ color: vars.accent }}>control()</span> function.</div>
                      <div>2. Click Run Judge to submit.</div>
                      <div>3. Backend runs real SUMO simulation via TraCI across 5 scenarios.</div>
                      <div>4. Click any scenario to watch the replay.</div>
                    </div>
                  </div>
                </>
              )}
            </div>
          </div>
        )}

        {/* ── LEADERBOARD ── */}
        {activeTab === 'lb' && (
          <div style={{ padding: 24, overflowY: 'auto', height: '100%', display: 'flex', gap: 24, flexWrap: 'wrap' }}>
            <div style={{ flex: 1, minWidth: 400 }}>
              <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 9, fontWeight: 600, color: vars.text2, letterSpacing: '0.12em', textTransform: 'uppercase', marginBottom: 12 }}>Global Rankings</div>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontFamily: "'IBM Plex Mono', monospace" }}>
                <thead>
                  <tr>
                    {['#', 'submission', 'score', 'date'].map((h) => (
                      <th key={h} style={{ fontSize: 9, fontWeight: 600, color: vars.text2, textTransform: 'uppercase', letterSpacing: '0.1em', textAlign: 'left', padding: '8px 12px', borderBottom: `1px solid ${vars.border}` }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {leaderboard.map((e) => {
                    const isYou = e.username === 'your_solution.py';
                    return (
                      <tr key={e.username + e.date} style={{ background: isYou ? 'rgba(200,255,0,0.04)' : 'transparent' }}>
                        <td style={{ padding: '10px 12px', borderBottom: `1px solid ${vars.border}`, fontSize: 12, color: isYou ? vars.accent : vars.text2 }}>{e.rank}</td>
                        <td style={{ padding: '10px 12px', borderBottom: `1px solid ${vars.border}`, fontSize: 12, color: isYou ? vars.accent : vars.text0 }}>{e.username}</td>
                        <td style={{ padding: '10px 12px', borderBottom: `1px solid ${vars.border}`, fontSize: 12, color: vars.accent, fontWeight: 500 }}>{e.score}</td>
                        <td style={{ padding: '10px 12px', borderBottom: `1px solid ${vars.border}`, fontSize: 12, color: vars.text1 }}>{e.date}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <div style={{ width: 220 }}>
              <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 9, fontWeight: 600, color: vars.text2, letterSpacing: '0.12em', textTransform: 'uppercase', marginBottom: 12 }}>Your Stats</div>
              {[['Best score', overallScore ?? '—'], ['Submissions', overallScore !== null ? 1 : 0]].map(([k, v]) => (
                <div key={String(k)} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 5 }}>
                  <span style={{ fontSize: 11, color: vars.text1 }}>{k}</span>
                  <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 12, color: vars.text0 }}>{String(v)}</span>
                </div>
              ))}
              <div style={{ marginTop: 20, padding: 12, background: vars.bg2, border: `1px solid ${vars.border}`, borderRadius: 4 }}>
                <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 9, fontWeight: 600, color: vars.text2, letterSpacing: '0.12em', textTransform: 'uppercase', marginBottom: 8 }}>Hint</div>
                <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 10, color: vars.text2, lineHeight: 1.8 }}>
                  Compare queue sums:<br />
                  if queues['N'] + queues['S']<br />
                  &gt; queues['E'] + queues['W']:<br />
                  &nbsp;&nbsp;bias toward NS phase
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
