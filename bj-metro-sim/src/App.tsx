import { useEffect, useState } from 'react';
import MetroMap from './components/MetroMap';
import KPIPanel from './components/KPIPanel';
import LinesPanel from './components/LinesPanel';
import DriverConsole from './components/DriverConsole';
import MicroTrackView from './components/MicroTrackView';
import StationInterlockingView from './components/StationInterlockingView';
import { getInterlockingData } from './data/stationInterlockingData';
import { useSimStore } from './store/useSimStore';
import type { MetroLineData } from './data/amapMetroApi';
import { fetchAmapBeijingMetro, getCachedAmapData, getPartialAmapCache, cacheAmapData } from './data/amapMetroApi';
import { fetchBackendBundle, fetchSimState } from './data/backendApi';

let globalFetching = false;
const PANEL_W = 320;

export default function App() {
  const setMetroLines = useSimStore((s) => s.setMetroLines);
  const setLinesLoading = useSimStore((s) => s.setLinesLoading);
  const setLinesError = useSimStore((s) => s.setLinesError);
  const setTrackMap = useSimStore((s) => s.setTrackMap);
  const setBackendStatus = useSimStore((s) => s.setBackendStatus);
  const viewMode = useSimStore((s) => s.viewMode);
  const setViewMode = useSimStore((s) => s.setViewMode);
  const backendStatus = useSimStore((s) => s.backendStatus);
  const trackMap = useSimStore((s) => s.trackMap);
  const showOnlyLines = useSimStore((s) => s.showOnlyLines);
  const metroLines = useSimStore((s) => s.metroLines);
  const linesLoading = useSimStore((s) => s.linesLoading);
  const updateFromBackend = useSimStore((s) => s.updateFromBackend);
  const engineClockState = useSimStore((s) => s.engineClockState);
  const selectedStationCode = useSimStore((s) => s.selectedStationCode);
  const [collapsed, setCollapsed] = useState(false);
  const interlockingData = getInterlockingData(selectedStationCode ?? 'BWR');
  const modeIndex = viewMode === 'macro' ? 0 : viewMode === 'micro' ? 1 : viewMode === 'interlocking' ? 2 : 3;

  // 首次加载: 先拉取全量路网(Amap) → 再并行尝试后端获取9号线富数据
  useEffect(() => {
    if (globalFetching) return;
    globalFetching = true;
    setLinesLoading(true);

    // Step 1 — 始终加载全量路网（保证全览模式有完整地铁图）
    const loadFullNetwork = (): Promise<void> =>
      fetch('/beijing_metro_lines.json')
        .then((resp) => {
          if (!resp.ok) throw new Error('no static file');
          return resp.json();
        })
        .then((lines: MetroLineData[]) => {
          setMetroLines(lines);
          setLinesError(null);
          setLinesLoading(false);
        })
        .catch(() => {
          const cached = getCachedAmapData();
          if (cached && cached.length > 0) {
            setMetroLines(cached);
            setLinesError(null);
            setLinesLoading(false);
            return;
          }
          const amapKey = import.meta.env.VITE_AMAP_KEY as string | undefined;
          if (!amapKey || amapKey === 'your_amap_key_here') {
            setLinesError('请配置 VITE_AMAP_KEY');
            setLinesLoading(false);
            return;
          }
          return fetchAmapBeijingMetro(amapKey)
            .then((lines) => { cacheAmapData(lines); setMetroLines(lines); setLinesError(null); })
            .catch((err) => {
              const fallback = getPartialAmapCache();
              if (fallback?.length) { setMetroLines(fallback); setLinesError(`API受限, ${fallback.length} 条线路`); }
              else setLinesError(err instanceof Error ? err.message : '未知错误');
            })
            .finally(() => { setLinesLoading(false); });
        });

    // Step 2 — 并行尝试后端（获取 9号线 trackMap, 仿真引擎等富数据）
    loadFullNetwork().finally(() => {
      fetchBackendBundle()
        .then(({ line: _line9, trackMap: nextTrackMap }) => {
          setTrackMap(nextTrackMap);
          setBackendStatus('connected');
        })
        .catch((err) => {
          const msg = err instanceof Error ? err.message : String(err);
          console.warn('[App] 后端不可用:', msg);
          setBackendStatus('fallback');
        })
        .finally(() => { globalFetching = false; });
    });
  }, []);

  // 只默认显示9号线
  useEffect(() => {
    if (metroLines.length === 0) return;
    requestAnimationFrame(() => showOnlyLines(['9']));
  }, [metroLines.length]);

  // 后端仿真引擎轮询 (200ms — 比后端 tick 快，确保控制响应及时、列车位移平滑)
  useEffect(() => {
    if (backendStatus !== 'connected') return;
    let active = true;
    const POLL_MS = 200;
    const poll = () => {
      if (!active) return;
      fetchSimState()
        .then((data) => { if (active) updateFromBackend(data); })
        .catch(() => { /* 静默忽略轮询错误 */ })
        .finally(() => { if (active) setTimeout(poll, POLL_MS); });
    };
    poll();
    return () => { active = false; };
  }, [backendStatus, updateFromBackend]);

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
            LINE 9
          </span>

          {/* ─── 宏观 / 轨道级 / 联锁图 切换 ─── */}
          <div
            className="flex items-center relative rounded-full select-none"
            style={{
              border: '1px solid rgba(255,255,255,0.10)',
              background: 'rgba(255,255,255,0.03)',
            }}
          >
            {/* sliding pill indicator */}
            <div
              className="absolute top-0 rounded-full"
              style={{
                left: `${modeIndex * 25}%`,
                width: '25%',
                bottom: 0,
                background: viewMode === 'macro'
                  ? 'rgba(74,158,255,0.35)'
                  : viewMode === 'micro'
                    ? 'rgba(143,195,31,0.38)'
                    : viewMode === 'interlocking'
                      ? 'rgba(255,69,58,0.32)'
                      : 'rgba(168,214,74,0.38)',
                transition: 'left 280ms cubic-bezier(0.33, 1, 0.68, 1), background 280ms ease',
              }}
            />
            <button
              type="button"
              onClick={() => setViewMode('macro')}
              className="relative z-10 py-1 w-14 text-[11px] font-medium cursor-pointer text-center"
              style={{
                color: viewMode === 'macro' ? '#fff' : 'var(--text-muted)',
                transition: 'color 250ms ease',
              }}
            >
              宏观
            </button>
            <button
              type="button"
              onClick={() => setViewMode('micro')}
              className="relative z-10 py-1 w-14 text-[11px] font-medium cursor-pointer text-center"
              style={{
                color: viewMode === 'micro' ? '#fff' : 'var(--text-muted)',
                transition: 'color 250ms ease',
              }}
            >
              轨道
            </button>
            <button
              type="button"
              onClick={() => setViewMode('interlocking')}
              className="relative z-10 py-1 w-14 text-[11px] font-medium cursor-pointer text-center"
              style={{
                color: viewMode === 'interlocking' ? '#fff' : 'var(--text-muted)',
                transition: 'color 250ms ease',
              }}
            >
              联锁
            </button>
            <button
              type="button"
              onClick={() => setViewMode('driver')}
              className="relative z-10 py-1 w-14 text-[11px] font-medium cursor-pointer text-center"
              style={{
                color: viewMode === 'driver' ? '#fff' : 'var(--text-muted)',
                transition: 'color 250ms ease',
              }}
            >
              驾驶
            </button>
          </div>
        </div>

        <div className="flex items-center gap-3 text-[10px] board-num" style={{ color: 'var(--text-muted)' }}>
          <span className="led led-online" /> SYS ONLINE
          <span style={{ color: 'rgba(255,255,255,0.06)' }}>|</span>
          <span>
            API{' '}
            <span style={{ color: backendStatus === 'connected' ? 'var(--cyan)' : 'var(--amber)' }}>
              {backendStatus.toUpperCase()}
            </span>
          </span>
          <span style={{ color: 'rgba(255,255,255,0.06)' }}>|</span>
          <span>UTC+8</span>
          {linesLoading && <span style={{ color: 'var(--amber)' }}>LOADING</span>}
          {backendStatus === 'connected' && (
            <>
              <span style={{ color: 'rgba(255,255,255,0.06)' }}>|</span>
              <span style={{ color: engineClockState === 'RUNNING' ? 'var(--green)' : 'var(--text-muted)' }}>
                {engineClockState}
              </span>
            </>
          )}
        </div>
      </header>

      {/* ═══════════════ body ═══════════════ */}
      <div className="flex-1 flex min-h-0 relative" style={{ gap: 8 }}>
        {/* map — 始终挂载（用 opacity 隐藏，避免地图实例销毁后 marker 丢失） */}
        <div className="flex-1 overflow-hidden relative min-w-0 map-frame">
          <div style={{
            position: 'absolute', inset: 0,
            opacity: viewMode === 'macro' ? 1 : 0,
            pointerEvents: viewMode === 'macro' ? 'auto' : 'none',
            transition: 'opacity 200ms ease-out',
          }}>
            <MetroMap />
            <FloatingLineFilter />
          </div>
          <div style={{
            position: 'absolute', inset: 0,
            opacity: viewMode !== 'macro' ? 1 : 0,
            pointerEvents: viewMode !== 'macro' ? 'auto' : 'none',
            transition: 'opacity 200ms ease-out',
          }}>
            {viewMode === 'driver'
              ? <DriverConsole fullPage />
              : viewMode === 'interlocking'
                ? <StationInterlockingView data={interlockingData} />
                : viewMode === 'micro'
                  ? <MicroTrackView />
                  : null}
          </div>

          {/* ─── right panel toggle ─── */}
          {viewMode === 'macro' && (
          <button
            onClick={() => setCollapsed((v) => !v)}
            className="absolute z-20 w-8 h-8 flex items-center justify-center cursor-pointer rounded-full"
            style={{
              top: '50%',
              transform: 'translateY(-50%)',
              right: collapsed ? 4 : `${PANEL_W + 4}px`,
              background: 'var(--glass)',
              backdropFilter: 'blur(28px)',
              border: '1px solid rgba(255,255,255,0.06)',
              color: 'var(--text-muted)',
              transition: 'right 320ms cubic-bezier(0.33, 1, 0.68, 1)',
            }}
            title={collapsed ? '展开面板' : '收起面板'}
          >
            <svg width="6" height="10" viewBox="0 0 6 10" fill="none"
              style={{ transform: collapsed ? 'rotate(0deg)' : 'rotate(180deg)' }}>
              <path d="M1 1L5 5L1 9" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
          </button>
          )}

          {/* ─── right panel overlay (macro only) ─── */}
          {viewMode === 'macro' && (
          <div
            className="absolute top-0 bottom-0 z-10 overflow-y-auto"
            style={{
              right: collapsed ? `-${PANEL_W}px` : 0,
              width: `${PANEL_W}px`,
              transition: 'right 320ms cubic-bezier(0.33, 1, 0.68, 1)',
            }}
          >
            <div className="flex flex-col" style={{ gap: 8 }}>
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
          )}
        </div>
      </div>

      {/* ═══════════════ footer ═══════════════ */}
      <footer className="flex items-center justify-between px-5 shrink-0 board-num text-[9px]" style={{ color: 'var(--text-muted)', height: 18 }}>
        <div className="flex items-center gap-2">
          <span className={backendStatus === 'connected' ? 'led led-online' : 'led'} />
          {backendStatus === 'connected' && trackMap
            ? `${trackMap.counts.segments} SEG · ${trackMap.counts.signals} SIG · ${trackMap.counts.routes} ROUTE`
            : backendStatus === 'fallback'
              ? 'MAP: AMAP'
              : 'API OFFLINE'}
          {backendStatus === 'connected' && (
            <>
              <span style={{ color: 'rgba(255,255,255,0.06)' }}>|</span>
              <span style={{ color: engineClockState === 'RUNNING' ? 'var(--green)' : 'var(--text-muted)' }}>
                SIM: {engineClockState}
              </span>
            </>
          )}
        </div>
        <span style={{ color: 'rgba(255,255,255,0.04)' }}>v0.2.0</span>
      </footer>
    </div>
  );
}

/* ═══════════════ 地图浮动线路过滤 ═══════════════ */
function FloatingLineFilter() {
  const showAllLines = useSimStore((s) => s.showAllLines);
  const showOnlyLines = useSimStore((s) => s.showOnlyLines);

  return (
    <div className="absolute top-3 left-3 z-10 flex items-center gap-1.5">
      <button
        onClick={showAllLines}
        className="text-[10px] font-medium cursor-pointer rounded-md px-2.5 py-1.5 transition-all duration-150"
        style={{
          color: 'rgba(255,255,255,0.7)',
          background: 'var(--glass)',
          backdropFilter: 'blur(20px)',
          border: '1px solid rgba(255,255,255,0.08)',
        }}
      >
        全览
      </button>
      <button
        onClick={() => showOnlyLines(['9'])}
        className="text-[10px] font-medium cursor-pointer rounded-md px-2.5 py-1.5 transition-all duration-150"
        style={{
          color: 'var(--l9)',
          background: 'rgba(168,214,74,0.08)',
          backdropFilter: 'blur(20px)',
          border: '1px solid rgba(168,214,74,0.16)',
        }}
      >
        9号线
      </button>
    </div>
  );
}
