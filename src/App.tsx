import React, { useState, useRef, useCallback, useEffect } from 'react';
import { Play, Pause, RotateCcw, ChevronRight, Activity, Zap, Trophy, Users, Loader2 } from 'lucide-react';
import { motion, AnimatePresence } from 'motion/react';

// ── Types ────────────────────────────────────────────────────────────────────

interface VehicleReplay {
  id: string;
  x: number;
  y: number;
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
}

const LEVEL_NAMES: Record<number, string> = {
  1: 'Low Traffic',
  2: 'Balanced',
  3: 'N/S Heavy',
  4: 'E/W Heavy',
  5: 'Rush Hour',
};

// ── Constants ────────────────────────────────────────────────────────────────

const W = 520;
const H = 520;
const CX = W / 2;
const CY = H / 2;
const ROAD = 44;
const LANE_W = 16;
const SCALE = 0.52; // pixels per meter: 520px / 1000m = 0.52

const ROAD_COLORS: Record<string, string> = {
  road_N: '#5c7a9a',
  road_S: '#7a5c9a',
  road_E: '#9a7a5c',
  road_W: '#5c9a7a',
};

const DEFAULT_CODE = `# Traffic Light Controller
def control(queues, current_phase, phase_timer):
    if current_phase == 'NS':
        if phase_timer >= 30:
            return 'yellow'
    elif current_phase == 'EW':
        if phase_timer >= 20:
            return 'yellow'
    return current_phase`;

const MOCK_LEADERBOARD: LeaderEntry[] = [
  { rank: 1, username: 'traffic_wizard', score: 97.3, date: '2026-03-28' },
  { rank: 2, username: 'signal_master', score: 94.1, date: '2026-03-30' },
  { rank: 3, username: 'green_wave_dev', score: 91.8, date: '2026-04-01' },
  { rank: 4, username: 'flow_optimizer', score: 89.5, date: '2026-03-25' },
  { rank: 5, username: 'adaptive_ctrl', score: 87.2, date: '2026-04-02' },
  { rank: 6, username: 'queue_buster', score: 84.0, date: '2026-03-29' },
  { rank: 7, username: 'phase_hacker', score: 81.6, date: '2026-03-31' },
  { rank: 8, username: 'intersection_ai', score: 78.9, date: '2026-03-27' },
  { rank: 9, username: 'throughput_king', score: 75.4, date: '2026-04-01' },
  { rank: 10, username: 'fixed_timer_bot', score: 72.1, date: '2026-03-26' },
];

// ── Canvas Drawing ───────────────────────────────────────────────────────────

function drawRoads(ctx: CanvasRenderingContext2D, q: {N:number;S:number;E:number;W:number} = {N:0,S:0,E:0,W:0}) {
  const W = ctx.canvas.width;
  const H = ctx.canvas.height;
  const CX = W / 2;
  const CY = H / 2;
  const ROAD = 44;
  const LANE = 16;

  // Background & Surface
  ctx.fillStyle = '#1a1f1a'; ctx.fillRect(0,0,W,H);
  ctx.fillStyle = '#2a2a2a';
  ctx.fillRect(CX-ROAD, 0, ROAD*2, H);
  ctx.fillRect(0, CY-ROAD, W, ROAD*2);
  ctx.fillRect(CX-ROAD, CY-ROAD, ROAD*2, ROAD*2);

  // BUG 2 FIX: Correct Queue Tint mapping (Congestion Highlights)
  const qTint = (x: number, y: number, w: number, h: number, count: number) => {
    if(count < 2) return;
    const alpha = Math.min(0.35, count / 12);
    ctx.fillStyle = `rgba(255,69,69,${alpha})`;
    ctx.fillRect(x,y,w,h);
  };
  // N comes from BOTTOM -> tint bottom-left lane
  qTint(CX-ROAD, CY+ROAD, ROAD, H-CY-ROAD, q.N);
  // S comes from TOP -> tint top-right lane
  qTint(CX, 0, ROAD, CY-ROAD, q.S);
  // E comes from LEFT -> tint bottom-left lane (horizontal)
  qTint(0, CY, CX-ROAD, ROAD, q.E);
  // W comes from RIGHT -> tint top-right lane (horizontal)
  qTint(CX+ROAD, CY-ROAD, W-CX-ROAD, ROAD, q.W);

  // Lane dashes
  ctx.strokeStyle = '#444'; ctx.lineWidth = 1; ctx.setLineDash([10,12]);
  ctx.beginPath(); ctx.moveTo(CX,0); ctx.lineTo(CX,CY-ROAD); ctx.stroke();
  ctx.beginPath(); ctx.moveTo(CX,CY+ROAD); ctx.lineTo(CX,H); ctx.stroke();
  ctx.beginPath(); ctx.moveTo(0,CY); ctx.lineTo(CX-ROAD,CY); ctx.stroke();
  ctx.beginPath(); ctx.moveTo(CX+ROAD,CY); ctx.lineTo(W,CY); ctx.stroke();
  ctx.setLineDash([]);

  // BUG 4 FIX: Correct Stop Lines (Half-road alignment)
  ctx.strokeStyle = '#fff'; ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(CX-ROAD, CY+ROAD+2); ctx.lineTo(CX, CY+ROAD+2); ctx.stroke(); // N (bottom left half)

  ctx.beginPath();
  ctx.moveTo(CX, CY-ROAD-2); ctx.lineTo(CX+ROAD, CY-ROAD-2); ctx.stroke(); // S (top right half)

  ctx.beginPath();
  ctx.moveTo(CX-ROAD-2, CY); ctx.lineTo(CX-ROAD-2, CY+ROAD); ctx.stroke(); // E (left bottom half)

  ctx.beginPath();
  ctx.moveTo(CX+ROAD+2, CY-ROAD); ctx.lineTo(CX+ROAD+2, CY); ctx.stroke(); // W (right top half)

  // BUG 3 FIX: Correct Direction Labels
  ctx.fillStyle = 'rgba(255,255,255,0.2)';
  ctx.font = '11px IBM Plex Mono, monospace';
  ctx.textAlign='center';
  ctx.fillText('N', CX - LANE/2, H - 6);       // N comes from Bottom
  ctx.fillText('S', CX + LANE/2, 16);          // S comes from Top
  ctx.fillText('E', 16, CY + LANE/2 + 12);     // E comes from Left
  ctx.fillText('W', W - 16, CY - LANE/2 - 2);  // W comes from Right
}

function drawTrafficLights(ctx: CanvasRenderingContext2D, frame: TickFrame | null) {
  const W = ctx.canvas.width;
  const H = ctx.canvas.height;
  const CX = W / 2;
  const CY = H / 2;
  const ROAD = 44;

  const phase = frame?.phase ?? 'NS';
  const inYellow = frame?.in_yellow ?? false;
  const lights = frame?.lights;

  const drawLight = (offsetX: number, offsetY: number, state: string) => {
    ctx.save();
    ctx.beginPath();
    ctx.arc(CX + offsetX, CY + offsetY, 6, 0, Math.PI * 2);
    const s = state.toLowerCase();
    ctx.fillStyle = s === 'g' ? '#00FF00' : s === 'y' ? '#FFFF00' : '#FF0000';
    ctx.shadowColor = ctx.fillStyle;
    ctx.shadowBlur = 10;
    ctx.fill();
    ctx.restore();
  };

  if (lights) {
    drawLight(ROAD + 10, -ROAD - 10, lights.N || 'r');
    drawLight(-ROAD - 10, ROAD + 10, lights.S || 'r');
    drawLight(ROAD + 10, ROAD + 10, lights.E || 'r');
    drawLight(-ROAD - 10, -ROAD - 10, lights.W || 'r');
    return;
  }

  const positions = [
    { x: CX + ROAD + 8, y: CY - ROAD - 8, isNS: true },
    { x: CX - ROAD - 8, y: CY + ROAD + 8, isNS: true },
    { x: CX + ROAD + 8, y: CY + ROAD + 8, isNS: false },
    { x: CX - ROAD - 8, y: CY - ROAD - 8, isNS: false },
  ];

  for (const { x, y, isNS } of positions) {
    const green = isNS ? (phase === 'NS' && !inYellow) : (phase === 'EW' && !inYellow);
    const yellow = inYellow;
    const red = !green && !yellow;
    const s = 5;

    ctx.fillStyle = '#111';
    ctx.beginPath();
    ctx.roundRect(x - s, y - s * 4, s * 2, s * 9, 2);
    ctx.fill();

    ctx.fillStyle = red || yellow ? '#ff4545' : '#ff454530';
    ctx.beginPath(); ctx.arc(x, y - s * 2.5, s * 0.7, 0, Math.PI * 2); ctx.fill();

    ctx.fillStyle = yellow ? '#ffaa00' : '#ffaa0025';
    ctx.beginPath(); ctx.arc(x, y, s * 0.7, 0, Math.PI * 2); ctx.fill();

    ctx.fillStyle = green ? '#3ddc84' : '#3ddc8425';
    ctx.beginPath(); ctx.arc(x, y + s * 2.5, s * 0.7, 0, Math.PI * 2); ctx.fill();
  }
}

function drawVehicles(ctx: CanvasRenderingContext2D, vehicles: VehicleReplay[]) {
  const CX = ctx.canvas.width / 2;
  const CY = ctx.canvas.height / 2;

  for (const v of vehicles) {
    if (v.x === undefined || v.y === undefined) continue;

    ctx.save();

    const screenX = CX + (v.x * SCALE);
    const screenY = CY - (v.y * SCALE);

    ctx.translate(screenX, screenY);
    ctx.rotate(v.angle || 0);

    ctx.fillStyle = v.color || 'white';
    ctx.fillRect(-4, -8, 8, 16);

    ctx.restore();
  }
}

function renderFrame(ctx: CanvasRenderingContext2D, frame: TickFrame | null) {
  const W = ctx.canvas.width;
  const H = ctx.canvas.height;
  const CX = W / 2;
  const CY = H / 2;

  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, W, H);

  drawRoads(ctx, frame?.queues ?? {N:0, S:0, E:0, W:0});

  if (!frame) {
    ctx.fillStyle = '#555';
    ctx.font = '14px "IBM Plex Mono", monospace';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText('Submit code and click a scenario to replay', CX, CY);
    return;
  }

  drawTrafficLights(ctx, frame);
  drawVehicles(ctx, frame.vehicles);

  ctx.fillStyle = '#c8ff00';
  ctx.font = '11px "IBM Plex Mono", monospace';
  ctx.textAlign = 'left';
  ctx.textBaseline = 'top';
  ctx.fillText(`Tick ${frame.tick} | ${frame.vehicles.length} vehicles | ${frame.phase}`, 8, 8);
}

// ── Component ────────────────────────────────────────────────────────────────

export default function App() {
  const [activeTab, setActiveTab] = useState<'sim' | 'judge' | 'lb'>('sim');
  const [engineType, setEngineType] = useState<'python' | 'sumo'>('python');
  const [code, setCode] = useState(DEFAULT_CODE);
  const [isJudging, setIsJudging] = useState(false);
  const [overallScore, setOverallScore] = useState<number | null>(null);
  const [results, setResults] = useState<ScenarioResult[]>([]);
  const [evalDetails, setEvalDetails] = useState<EvalDetail[]>([]);
  const [selectedScenario, setSelectedScenario] = useState<number | null>(null);

  const [playing, setPlaying] = useState(false);
  const [playbackSpeed, setPlaybackSpeed] = useState(5);
  const [currentFrame, setCurrentFrame] = useState<TickFrame | null>(null);

  const canvasRef = useRef<HTMLCanvasElement>(null);
  const rafRef = useRef<number | null>(null);
  const framesRef = useRef<TickFrame[]>([]);
  const tickRef = useRef(0);
  const lastTsRef = useRef(0);
  const playingRef = useRef(false);
  const speedRef = useRef(5);

  useEffect(() => { playingRef.current = playing; }, [playing]);
  useEffect(() => { speedRef.current = playbackSpeed; }, [playbackSpeed]);

  // Draw directly - no state dependency
  const draw = useCallback((frame: TickFrame | null) => {
    try {
      const canvas = canvasRef.current;
      if (!canvas) return;
      // Ensure explicit internal resolution
      canvas.width = W;
      canvas.height = H;
      const ctx = canvas.getContext('2d');
      if (!ctx) return;
      renderFrame(ctx, frame);
    } catch (err) {
      console.error('Canvas draw error:', err);
    }
  }, []);

  // Ensure canvas is set up on mount
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    canvas.width = W;
    canvas.height = H;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    renderFrame(ctx, null);
  }, []);

  // Draw whenever currentFrame changes
  useEffect(() => {
    draw(currentFrame);
  }, [currentFrame, draw]);

  // Playback loop - draws directly inside rAF
  useEffect(() => {
    if (rafRef.current) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }

    if (!playing || framesRef.current.length === 0) return;

    // Ensure canvas has explicit internal resolution
    const canvas = canvasRef.current;
    if (canvas) {
      canvas.width = W;
      canvas.height = H;
    }

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
          setCurrentFrame(last);
          draw(last);
          setPlaying(false);
          return;
        }

        const frame = frames[Math.floor(tickRef.current)];
        setCurrentFrame(frame);
        draw(frame);

        rafRef.current = requestAnimationFrame(loop);
      } catch (err) {
        console.error('Playback loop error:', err);
        setPlaying(false);
      }
    };

    rafRef.current = requestAnimationFrame(loop);

    return () => {
      if (rafRef.current) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
    };
  }, [playing, draw]);

  // ── Judge ────────────────────────────────────────────────────────────────

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
    if (rafRef.current) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }

    try {
      const res = await fetch('http://localhost:8000/evaluate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code, engine_type: engineType }),
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
        setEvalDetails(data.details ?? []);
        setResults([]);
      } else {
        setOverallScore(data.overall_score);
        setResults(data.scenarios);
        setEvalDetails([]);

        const firstValid = data.scenarios.findIndex(
          (s: ScenarioResult) => s.replay_data && s.replay_data.length > 0
        );
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

    if (rafRef.current) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }

    setPlaying(false);
    tickRef.current = 0;
    framesRef.current = frames;
    setSelectedScenario(idx);
    setCurrentFrame(frames[0]);
    setActiveTab('sim');
  }, [results]);

  const togglePlay = useCallback(() => {
    if (!framesRef.current.length) return;
    setPlaying((p) => !p);
  }, []);

  const resetPlayback = useCallback(() => {
    if (rafRef.current) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
    tickRef.current = 0;
    setPlaying(false);
    if (framesRef.current.length > 0) {
      setCurrentFrame(framesRef.current[0]);
    }
  }, []);

  const queues = currentFrame?.queues ?? { N: 0, S: 0, E: 0, W: 0 };
  const DIRS = ['N', 'S', 'E', 'W'] as const;

  const leaderboard = overallScore !== null
    ? [
        ...MOCK_LEADERBOARD.filter(e => e.score > overallScore),
        { rank: 0, username: 'You (current)', score: overallScore, date: 'Now' },
        ...MOCK_LEADERBOARD.filter(e => e.score <= overallScore),
      ].map((e, i) => ({ ...e, rank: i + 1 }))
    : MOCK_LEADERBOARD;

  return (
    <div className="bg-zinc-950 text-zinc-100 h-screen overflow-hidden flex flex-col">
      <header className="h-10 bg-zinc-900 border-b border-zinc-800 flex items-center px-4 shrink-0">
        <div className="text-[11px] font-mono font-bold text-[#c8ff00] tracking-widest uppercase mr-8">
          TrafficJudge
        </div>
        <nav className="flex h-full">
          {(['sim', 'judge', 'lb'] as const).map((tab) => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`px-4 h-full flex items-center text-[11px] font-mono transition-all border-b-2 ${
                activeTab === tab
                  ? 'text-white border-[#c8ff00]'
                  : 'text-zinc-500 border-transparent hover:text-zinc-300'
              }`}
            >
              {tab === 'sim' ? 'SIMULATION' : tab === 'judge' ? 'SUBMIT' : 'LEADERBOARD'}
            </button>
          ))}
        </nav>
      </header>

      <main className="flex-1 overflow-hidden">
        <AnimatePresence mode="wait">
          {activeTab === 'sim' && (
            <motion.div
              key="sim"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="h-full flex"
            >
              <div className="flex-1 bg-black relative flex items-center justify-center overflow-hidden">
                <div
                  className="absolute inset-0 opacity-20 pointer-events-none"
                  style={{
                    backgroundImage: 'radial-gradient(circle, #444 1px, transparent 1px)',
                    backgroundSize: '24px 24px',
                  }}
                />
                <canvas
                  ref={canvasRef}
                  width={W}
                  height={H}
                  style={{ width: W, height: H }}
                  className="relative z-10"
                />
              </div>

              <aside className="w-64 bg-zinc-900 border-l border-zinc-800 flex flex-col overflow-y-auto">
                <div className="p-4 border-b border-zinc-800">
                  <div className="text-[9px] font-mono font-bold text-zinc-500 uppercase tracking-widest mb-3">
                    Signal State
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    {(['NS', 'EW'] as const).map((dir) => {
                      const isNS = dir === 'NS';
                      const green = isNS
                        ? (currentFrame?.phase === 'NS' && !currentFrame?.in_yellow)
                        : (currentFrame?.phase === 'EW' && !currentFrame?.in_yellow);
                      const yellow = currentFrame?.in_yellow ?? false;
                      const red = !green && !yellow;

                      return (
                        <div key={dir} className="flex flex-col items-center gap-1">
                          <div className="text-[9px] font-mono text-zinc-500">
                            {isNS ? 'N — S' : 'E — W'}
                          </div>
                          <div className="bg-black border border-zinc-800 rounded p-1.5 flex flex-col gap-1 items-center">
                            <div className={`w-2.5 h-2.5 rounded-full transition-all ${red ? 'bg-red-500 shadow-[0_0_8px_#ef4444]' : 'bg-red-950 opacity-20'}`} />
                            <div className={`w-2.5 h-2.5 rounded-full transition-all ${yellow ? 'bg-amber-500 shadow-[0_0_8px_#f59e0b]' : 'bg-amber-950 opacity-20'}`} />
                            <div className={`w-2.5 h-2.5 rounded-full transition-all ${green ? 'bg-emerald-500 shadow-[0_0_8px_#10b981]' : 'bg-emerald-950 opacity-20'}`} />
                          </div>
                        </div>
                      );
                    })}
                  </div>
                  <div className="flex justify-between items-baseline mt-4">
                    <span className={`text-[11px] font-mono ${currentFrame?.in_yellow ? 'text-amber-500' : 'text-emerald-500'}`}>
                      {currentFrame?.in_yellow ? 'YELLOW' : `${currentFrame?.phase ?? '—'} GREEN`}
                    </span>
                    <span className="text-lg font-mono font-bold text-[#c8ff00]">
                      {currentFrame ? Math.floor(currentFrame.tick) : '—'}
                    </span>
                  </div>
                </div>

                <div className="p-4 border-b border-zinc-800">
                  <div className="text-[9px] font-mono font-bold text-zinc-500 uppercase tracking-widest mb-3">
                    Queue Depth
                  </div>
                  {DIRS.map((dir) => (
                    <div key={dir} className="mb-2">
                      <div className="flex justify-between text-[10px] font-mono text-zinc-400 mb-1">
                        <span>{dir}</span>
                        <span>{queues[dir]}</span>
                      </div>
                      <div className="h-1 bg-zinc-800 rounded-full overflow-hidden">
                        <motion.div
                          className={`h-full rounded-full ${queues[dir] > 8 ? 'bg-red-500' : queues[dir] > 4 ? 'bg-amber-500' : 'bg-emerald-500'}`}
                          animate={{ width: `${Math.min(100, (queues[dir] / 15) * 100)}%` }}
                        />
                      </div>
                    </div>
                  ))}
                </div>

                <div className="p-4 border-b border-zinc-800">
                  <div className="text-[9px] font-mono font-bold text-zinc-500 uppercase tracking-widest mb-3">
                    Playback
                  </div>
                  <div className="flex gap-2 mb-4">
                    <button
                      onClick={togglePlay}
                      disabled={!currentFrame}
                      className={`flex-1 py-1.5 rounded text-[11px] font-mono flex items-center justify-center gap-2 transition-all disabled:opacity-30 ${
                        playing
                          ? 'bg-red-500/10 text-red-500 border border-red-500/50 hover:bg-red-500/20'
                          : 'bg-[#c8ff00]/10 text-[#c8ff00] border border-[#c8ff00]/50 hover:bg-[#c8ff00]/20'
                      }`}
                    >
                      {playing ? <Pause size={12} /> : <Play size={12} />}
                      {playing ? ' PAUSE' : ' PLAY'}
                    </button>
                    <button
                      onClick={resetPlayback}
                      disabled={!currentFrame}
                      className="p-1.5 bg-zinc-800 border border-zinc-700 rounded text-zinc-400 hover:text-white disabled:opacity-30"
                    >
                      <RotateCcw size={14} />
                    </button>
                  </div>
                  <div className="flex items-center gap-3">
                    <label className="text-[10px] font-mono text-zinc-500 w-10">Speed</label>
                    <input
                      type="range" min={1} max={20} value={playbackSpeed}
                      onChange={(e) => setPlaybackSpeed(parseInt(e.target.value))}
                      className="flex-1 accent-[#c8ff00] h-1 bg-zinc-800 rounded-full appearance-none cursor-pointer"
                    />
                    <span className="text-[10px] font-mono text-zinc-100 w-6 text-right">{playbackSpeed}x</span>
                  </div>
                </div>

                {results.length > 0 && (
                  <div className="p-4 flex-1">
                    <div className="text-[9px] font-mono font-bold text-zinc-500 uppercase tracking-widest mb-3">
                      Results — Click to Replay
                    </div>
                    <div className="space-y-2">
                      {results.map((r, i) => (
                        <button
                          key={i}
                          onClick={() => selectScenario(i)}
                          className={`w-full text-left p-2 rounded border transition-all ${
                            selectedScenario === i
                              ? 'border-[#c8ff00] bg-[#c8ff00]/5'
                              : 'border-zinc-800 hover:border-zinc-600'
                          }`}
                        >
                          <div className="flex justify-between items-center">
                            <span className="text-[10px] font-mono text-zinc-300">{r.name}</span>
                            <span className="text-[10px] font-mono text-[#c8ff00]">{r.score}</span>
                          </div>
                          {r.error && (
                            <div className="text-[9px] font-mono text-red-400 mt-1">{r.error}</div>
                          )}
                        </button>
                      ))}
                    </div>
                  </div>
                )}
              </aside>
            </motion.div>
          )}

          {activeTab === 'judge' && (
            <motion.div
              key="judge"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="h-full flex"
            >
              <div className="flex-1 flex flex-col border-r border-zinc-800">
                <div className="h-10 bg-zinc-900 border-b border-zinc-800 flex items-center px-4 justify-between">
                  <span className="text-[11px] font-mono text-zinc-400">controller.py</span>
                  <div className="flex items-center gap-2">
                    <select
                      value={engineType}
                      onChange={(e) => setEngineType(e.target.value as 'python' | 'sumo')}
                      className="text-[10px] font-mono px-2 py-1 bg-zinc-800 rounded border border-zinc-700 text-zinc-300"
                    >
                      <option value="python">Python Sim</option>
                      <option value="sumo">SUMO</option>
                    </select>
                    <button
                      onClick={() => setCode(DEFAULT_CODE)}
                      className="text-[10px] font-mono px-2 py-1 bg-zinc-800 rounded hover:bg-zinc-700"
                    >
                      Reset
                    </button>
                  </div>
                </div>
                <textarea
                  value={code}
                  onChange={(e) => setCode(e.target.value)}
                  className="flex-1 bg-black p-6 font-mono text-xs leading-relaxed outline-none resize-none text-zinc-300"
                  spellCheck={false}
                />
                <div className="p-4 bg-zinc-900 border-t border-zinc-800 flex items-center gap-4">
                  <button
                    onClick={runJudge}
                    disabled={isJudging}
                    className="px-6 py-2 bg-[#c8ff00] text-black font-mono font-bold text-[11px] rounded hover:bg-[#d4ff33] disabled:opacity-50 flex items-center gap-2"
                  >
                    {isJudging ? <Activity size={14} className="animate-pulse" /> : <Zap size={14} />}
                    RUN JUDGE (5 SCENARIOS)
                  </button>
                  <span className="text-[10px] font-mono text-zinc-500">
                    {isJudging
                      ? 'Running 5 simulations...'
                      : overallScore !== null
                      ? `Score: ${overallScore}/100`
                      : 'Ready for submission'}
                  </span>
                </div>
              </div>

              <aside className="w-80 bg-zinc-900 flex flex-col overflow-y-auto">
                {isJudging ? (
                  <div className="flex-1 flex flex-col items-center justify-center p-8">
                    <Loader2 size={32} className="text-[#c8ff00] animate-spin mb-4" />
                    <div className="text-[12px] font-mono text-zinc-300 text-center leading-relaxed">
                      Running simulations across 5 difficulty levels...
                    </div>
                    <div className="text-[10px] font-mono text-zinc-600 mt-2">Please wait</div>
                  </div>
                ) : (
                  <>
                    <div className="p-8 border-b border-zinc-800 text-center">
                      <div className={`text-5xl font-mono font-bold mb-1 ${
                        overallScore !== null
                          ? overallScore >= 80 ? 'text-emerald-400 drop-shadow-[0_0_20px_rgba(52,211,153,0.5)]'
                          : overallScore >= 60 ? 'text-amber-400 drop-shadow-[0_0_20px_rgba(251,191,36,0.5)]'
                          : 'text-red-400 drop-shadow-[0_0_20px_rgba(248,113,113,0.5)]'
                          : 'text-zinc-100'
                      }`}>
                        {overallScore !== null ? overallScore : '—'}
                      </div>
                      <div className="text-[11px] font-mono text-zinc-500">/ 100</div>
                      <div className="text-[9px] font-mono text-zinc-600 mt-4 uppercase tracking-widest">
                        {overallScore !== null ? 'EVALUATION COMPLETE' : 'SUBMIT TO SEE SCORE'}
                      </div>
                    </div>

                    <div className="p-4 border-b border-zinc-800">
                      <div className="text-[9px] font-mono font-bold text-zinc-500 uppercase tracking-widest mb-3">
                        Scenario Breakdown
                      </div>
                      {evalDetails.length === 0 ? (
                        results.length === 0 ? (
                          <div className="text-[10px] font-mono text-zinc-600">
                            No results yet. Submit code to evaluate.
                          </div>
                        ) : (
                          <div className="space-y-2">
                            {results.map((s, i) => (
                              <button
                                key={i}
                                onClick={() => selectScenario(i)}
                                className="w-full text-left flex items-center gap-3 p-2 bg-black/20 rounded border border-zinc-800/50 hover:border-zinc-600 transition-all"
                              >
                                <div className="flex-1">
                                  <div className="text-[10px] font-mono text-zinc-300 mb-1">{s.name}</div>
                                  <div className="h-0.5 bg-zinc-800 rounded-full overflow-hidden">
                                    <div
                                      className={`h-full transition-all duration-500 ${s.score >= 80 ? 'bg-emerald-500' : s.score >= 60 ? 'bg-amber-500' : 'bg-red-500'}`}
                                      style={{ width: `${s.score}%` }}
                                    />
                                  </div>
                                </div>
                                <div className="text-[10px] font-mono text-zinc-500 w-8 text-right">{s.score}</div>
                                <ChevronRight size={12} className="text-zinc-600" />
                              </button>
                            ))}
                          </div>
                        )
                      ) : (
                        <div className="space-y-2.5">
                          {evalDetails.map((d, i) => {
                            const name = LEVEL_NAMES[d.level] ?? `Level ${d.level}`;
                            const statusOk = d.status === 'OK';
                            const scoreColor = d.score >= 80 ? 'text-emerald-400' : d.score >= 60 ? 'text-amber-400' : 'text-red-400';
                            const barColor = d.score >= 80 ? 'bg-emerald-500' : d.score >= 60 ? 'bg-amber-500' : 'bg-red-500';

                            return (
                              <div
                                key={i}
                                className="bg-black/30 rounded-lg border border-zinc-800/60 p-3 hover:border-zinc-700 transition-all"
                              >
                                <div className="flex items-center justify-between mb-2">
                                  <div className="flex items-center gap-2">
                                    <span className="text-[10px] font-mono text-zinc-500">L{d.level}</span>
                                    <span className="text-[11px] font-mono font-semibold text-zinc-200">{name}</span>
                                  </div>
                                  <div className="flex items-center gap-2">
                                    <span className={`text-[9px] font-mono font-bold px-1.5 py-0.5 rounded ${
                                      statusOk
                                        ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/30'
                                        : 'bg-red-500/10 text-red-400 border border-red-500/30'
                                    }`}>
                                      {d.status}
                                    </span>
                                    <span className={`text-[11px] font-mono font-bold ${scoreColor}`}>
                                      {d.score ?? '—'}
                                    </span>
                                  </div>
                                </div>

                                <div className="h-1 bg-zinc-800 rounded-full overflow-hidden mb-2.5">
                                  <div
                                    className={`h-full rounded-full transition-all duration-700 ${barColor}`}
                                    style={{ width: `${Math.min(100, d.score ?? 0)}%` }}
                                  />
                                </div>

                                <div className="flex gap-1.5">
                                  <div className="flex-1 bg-zinc-800/50 rounded px-2 py-1 text-center">
                                    <div className="text-[8px] font-mono text-zinc-500 uppercase">Delay</div>
                                    <div className="text-[10px] font-mono text-zinc-300">{d.total_delay ?? '—'}</div>
                                  </div>
                                  <div className="flex-1 bg-zinc-800/50 rounded px-2 py-1 text-center">
                                    <div className="text-[8px] font-mono text-zinc-500 uppercase">Max Q</div>
                                    <div className="text-[10px] font-mono text-zinc-300">{d.max_queue_length ?? '—'}</div>
                                  </div>
                                  <div className="flex-1 bg-zinc-800/50 rounded px-2 py-1 text-center">
                                    <div className="text-[8px] font-mono text-zinc-500 uppercase">Through</div>
                                    <div className="text-[10px] font-mono text-zinc-300">{d.throughput ?? '—'}</div>
                                  </div>
                                </div>
                              </div>
                            );
                          })}
                        </div>
                      )}
                    </div>

                    <div className="p-4">
                      <div className="text-[9px] font-mono font-bold text-zinc-500 uppercase tracking-widest mb-3">
                        How It Works
                      </div>
                      <div className="text-[10px] font-mono text-zinc-500 leading-relaxed space-y-2">
                        <p>1. Write a <code className="text-[#c8ff00]">control()</code> function in Python.</p>
                        <p>2. Click RUN JUDGE to submit.</p>
                        <p>3. The backend runs a real traffic simulation for 5 scenarios.</p>
                        <p>4. Click any scenario to watch the replay.</p>
                      </div>
                    </div>
                  </>
                )}
              </aside>
            </motion.div>
          )}

          {activeTab === 'lb' && (
            <motion.div
              key="lb"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="h-full flex items-center justify-center"
            >
              <div className="w-full max-w-2xl bg-zinc-900 border border-zinc-800 rounded-lg overflow-hidden">
                <div className="p-6 border-b border-zinc-800 flex items-center gap-3">
                  <Trophy size={24} className="text-[#c8ff00]" />
                  <div>
                    <h2 className="text-lg font-mono font-bold text-zinc-100">Leaderboard</h2>
                    <p className="text-[10px] font-mono text-zinc-500">Top submissions across all scenarios</p>
                  </div>
                </div>

                <div className="divide-y divide-zinc-800/50">
                  {leaderboard.map((entry) => {
                    const isYou = entry.username === 'You (current)';
                    const rankColor = entry.rank === 1 ? 'text-yellow-400' : entry.rank === 2 ? 'text-zinc-300' : entry.rank === 3 ? 'text-amber-600' : 'text-zinc-500';

                    return (
                      <div
                        key={entry.username + entry.date}
                        className={`flex items-center gap-4 px-6 py-3 transition-all ${
                          isYou ? 'bg-[#c8ff00]/5 border-l-2 border-[#c8ff00]' : ''
                        }`}
                      >
                        <div className={`w-8 h-8 rounded-full flex items-center justify-center font-mono font-bold text-sm ${rankColor}`}>
                          {entry.rank <= 3 ? <Trophy size={14} /> : entry.rank}
                        </div>
                        <div className="flex-1">
                          <div className={`text-[13px] font-mono font-semibold ${isYou ? 'text-[#c8ff00]' : 'text-zinc-200'}`}>
                            {entry.username}
                          </div>
                          <div className="text-[9px] font-mono text-zinc-600">{entry.date}</div>
                        </div>
                        <div className="text-right">
                          <div className={`text-lg font-mono font-bold ${entry.score >= 90 ? 'text-emerald-400' : entry.score >= 70 ? 'text-amber-400' : 'text-zinc-400'}`}>
                            {entry.score}
                          </div>
                          <div className="text-[9px] font-mono text-zinc-600">/ 100</div>
                        </div>
                      </div>
                    );
                  })}
                </div>

                <div className="p-4 border-t border-zinc-800 bg-zinc-900/50">
                  <div className="flex items-center gap-2 text-[10px] font-mono text-zinc-600">
                    <Users size={12} />
                    <span>{leaderboard.length} total submissions</span>
                    {overallScore !== null && (
                      <span className="ml-auto text-[#c8ff00]">Your best: {overallScore}</span>
                    )}
                  </div>
                </div>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </main>
    </div>
  );
}
