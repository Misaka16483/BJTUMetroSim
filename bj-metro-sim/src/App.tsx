import { useEffect, useState } from 'react';
import MetroMap from './components/MetroMap';
import LinesPanel from './components/LinesPanel';
import ControlPanel from './components/ControlPanel';
import KPIPanel from './components/KPIPanel';
import SignalScreenPanel from './components/SignalScreenPanel';
import { useSimStore } from './store/useSimStore';
import { fetchAmapBeijingMetro, getCachedAmapData, getPartialAmapCache, cacheAmapData } from './data/amapMetroApi';

let globalFetching = false;
const PANEL_W = 320;

export default function App() {
  const setMetroLines = useSimStore((s) => s.setMetroLines);
  const setLinesLoading = useSimStore((s) => s.setLinesLoading);
  const setLinesError = useSimStore((s) => s.setLinesError);
  const showOnlyLines = useSimStore((s) => s.showOnlyLines);
  const metroLines = useSimStore((s) => s.metroLines);
  const linesLoading = useSimStore((s) => s.linesLoading);
  const [collapsed, setCollapsed] = useState(false);

  function loadAmapData() {
    if (globalFetching) return;
    globalFetching = true;
    setLinesLoading(true);
    fetch('/beijing_metro_lines.json')
      .then((resp) => {
        if (!resp.ok) throw new Error('no static file');
        return resp.json();
      })
      .then((lines) => {
        setMetroLines(lines);
        setLinesError(null);
        setLinesLoading(false);
        globalFetching = false;
      })
      .catch(() => {
        const cached = getCachedAmapData();
        if (cached && cached.length > 0) {
          setMetroLines(cached);
          setLinesError(null);
          setLinesLoading(false);
          globalFetching = false;
          return;
        }
        const amapKey = import.meta.env.VITE_AMAP_KEY as string | undefined;
        if (!amapKey || amapKey === 'your_amap_key_here') {
          setLinesError('请配置 VITE_AMAP_KEY');
          setLinesLoading(false);
          globalFetching = false;
          return;
        }
        fetchAmapBeijingMetro(amapKey)
          .then((lines) => { cacheAmapData(lines); setMetroLines(lines); setLinesError(null); })
          .catch((err) => {
            const fallback = getPartialAmapCache();
            if (fallback?.length) {
              setMetroLines(fallback);
              setLinesError(`API受限, ${fallback.length} 条线路`);
            } else {
              setLinesError(err instanceof Error ? err.message : '未知错误');
            }
          })
          .finally(() => { setLinesLoading(false); globalFetching = false; });
      });
  }

  useEffect(() => {
    if (metroLines.length === 0) return;
    requestAnimationFrame(() => showOnlyLines(['9']));
  }, [metroLines.length]);

  useEffect(() => { loadAmapData(); }, []);

  return (
    <div
      className="h-screen w-screen flex flex-col"
      style={{ padding: 12, gap: 8, background: 'var(--bg)' }}
    >
      {/* ═══════════════ header ═══════════════ */}
      <header className="glass shrink-0 flex items-center justify-between px-5 h-12">
        <div className="flex items-center gap-3">
          <span className="led led-online" />
          <span className="text-[14px] font-semibold tracking-tight" style={{ color: 'var(--text)' }}>
            BJTUMetro<span style={{ color: 'var(--cyan)' }}>Sim</span>
          </span>
          <span
            className="chip"
            style={{ color: 'var(--text-muted)', border: '1px solid rgba(255,255,255,0.06)' }}
          >
            DISPATCH
          </span>
          <span
            className="chip"
            style={{
              color: 'var(--l9)',
              border: '1px solid rgba(168,214,74,0.15)',
              background: 'rgba(168,214,74,0.06)',
            }}
          >
            LINE 9 GGZ→GTG
          </span>
        </div>
        <div className="flex items-center gap-3 text-[10px] board-num" style={{ color: 'var(--text-muted)' }}>
          <span className="led led-online" /> SYS ONLINE
          <span style={{ color: 'rgba(255,255,255,0.06)' }}>|</span>
          <span>AMAP</span>
          <span style={{ color: 'rgba(255,255,255,0.06)' }}>|</span>
          <span>UTC+8</span>
          {linesLoading && <span style={{ color: 'var(--amber)' }}>LOADING</span>}
        </div>
      </header>

      {/* ═══════════════ control ═══════════════ */}
      <ControlPanel />

      {/* ═══════════════ body ═══════════════ */}
      <div className="flex-1 flex min-h-0" style={{ gap: 8 }}>
        {/* map */}
        <div className="flex-1 overflow-hidden relative min-w-0 map-frame">
          <MetroMap />
        </div>

        {/* collapse toggle */}
        <button
          onClick={() => setCollapsed((v) => !v)}
          className="absolute top-1/2 -translate-y-1/2 w-8 h-8 flex items-center justify-center z-20 cursor-pointer rounded-full"
          style={{
            right: collapsed ? 4 : `${PANEL_W + 4}px`,
            background: 'var(--glass)',
            backdropFilter: 'blur(28px)',
            border: '1px solid rgba(255,255,255,0.06)',
            color: 'var(--text-muted)',
            transition: 'right 320ms cubic-bezier(0.33, 1, 0.68, 1)',
          }}
          title={collapsed ? '展开' : '收起'}
        >
          <svg width="6" height="10" viewBox="0 0 6 10" fill="none">
            {collapsed
              ? <path d="M5 1L1 5L5 9" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
              : <path d="M1 1L5 5L1 9" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
            }
          </svg>
        </button>

        {/* right panels */}
        <div
          className="shrink-0 overflow-y-auto"
          style={{
            width: collapsed ? '0px' : `${PANEL_W}px`,
            transition: 'width 320ms cubic-bezier(0.33, 1, 0.68, 1)',
          }}
        >
          <div className="flex flex-col" style={{ width: `${PANEL_W}px`, gap: 8 }}>
            {/* signal screen */}
            <div className="shrink-0">
              <SignalScreenPanel />
            </div>
            {/* kpi */}
            <div className="shrink-0" style={{ height: 240 }}>
              <KPIPanel />
            </div>
            {/* lines */}
            <div className="shrink-0" style={{ minHeight: 280 }}>
              <LinesPanel />
            </div>
          </div>
        </div>
      </div>

      {/* ═══════════════ footer ═══════════════ */}
      <footer className="flex items-center justify-between px-5 shrink-0 board-num text-[9px]" style={{ color: 'var(--text-muted)', height: 18 }}>
        <div className="flex items-center gap-2">
          <span className="led" style={{ width: 4, height: 4, boxShadow: '0 0 4px rgba(48,209,88,0.3)' }} />
          MAP: AMAP
        </div>
        <span style={{ color: 'rgba(255,255,255,0.04)' }}>v0.2.0</span>
      </footer>
    </div>
  );
}
