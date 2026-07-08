import { useEffect, useState } from 'react';
import MetroMap from './components/MetroMap';
import LinesPanel from './components/LinesPanel';
import MicroTrackView from './components/MicroTrackView';
import ControlPanel from './components/ControlPanel';
import KPIPanel from './components/KPIPanel';
import SignalScreenPanel from './components/SignalScreenPanel';
import { useSimStore } from './store/useSimStore';
import { fetchAmapBeijingMetro, getCachedAmapData, getPartialAmapCache, cacheAmapData } from './data/amapMetroApi';
import { fetchBackendBundle } from './data/backendApi';

let globalFetching = false;
const PANEL_W = 320;

export default function App() {
  const setMetroLines = useSimStore((s) => s.setMetroLines);
  const setLinesLoading = useSimStore((s) => s.setLinesLoading);
  const setLinesError = useSimStore((s) => s.setLinesError);
  const setTrackMap = useSimStore((s) => s.setTrackMap);
  const setBackendStatus = useSimStore((s) => s.setBackendStatus);
  const showOnlyLines = useSimStore((s) => s.showOnlyLines);
  const metroLines = useSimStore((s) => s.metroLines);
  const linesLoading = useSimStore((s) => s.linesLoading);
  const backendStatus = useSimStore((s) => s.backendStatus);
  const trackMap = useSimStore((s) => s.trackMap);
  const viewMode = useSimStore((s) => s.viewMode);
  const setViewMode = useSimStore((s) => s.setViewMode);
  const [collapsed, setCollapsed] = useState(false);

  function loadAmapData(reason = '本地后端不可用') {
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
        setBackendStatus('fallback');
        setLinesLoading(false);
        globalFetching = false;
      })
      .catch(() => {
        const cached = getCachedAmapData();
        if (cached && cached.length > 0) {
          setMetroLines(cached);
          setLinesError(null);
          setBackendStatus('fallback');
          setLinesLoading(false);
          globalFetching = false;
          return;
        }

        const amapKey = import.meta.env.VITE_AMAP_KEY as string | undefined;
        if (!amapKey || amapKey === 'your_amap_key_here') {
          setLinesError(`${reason}; 请配置 VITE_AMAP_KEY`);
          setBackendStatus('error');
          setLinesLoading(false);
          globalFetching = false;
          return;
        }

        fetchAmapBeijingMetro(amapKey)
          .then((lines) => {
            cacheAmapData(lines);
            setMetroLines(lines);
            setLinesError(null);
            setBackendStatus('fallback');
          })
          .catch((err) => {
            const fallback = getPartialAmapCache();
            if (fallback?.length) {
              setMetroLines(fallback);
              setLinesError(`API受限, 仅显示已缓存 ${fallback.length} 条线路`);
              setBackendStatus('fallback');
            } else {
              const msg = err instanceof Error ? err.message : '未知错误';
              setLinesError(`加载失败: ${msg}`);
              setBackendStatus('error');
            }
          })
          .finally(() => {
            setLinesLoading(false);
            globalFetching = false;
          });
      });
  }

  useEffect(() => {
    if (globalFetching) return;
    globalFetching = true;
    setLinesLoading(true);
    fetchBackendBundle()
      .then(({ line, trackMap: nextTrackMap }) => {
        setMetroLines([line]);
        setTrackMap(nextTrackMap);
        setLinesError(null);
        setBackendStatus('connected');
      })
      .catch((err) => {
        const msg = err instanceof Error ? err.message : String(err);
        globalFetching = false;
        loadAmapData(`本地后端不可用: ${msg}`);
      })
      .finally(() => {
        setLinesLoading(false);
        globalFetching = false;
      });
  }, []);

  useEffect(() => {
    if (metroLines.length === 0) return;
    requestAnimationFrame(() => showOnlyLines(['9']));
  }, [metroLines.length, showOnlyLines]);

  const statusColor = backendStatus === 'connected'
    ? 'var(--green)'
    : backendStatus === 'error'
      ? 'var(--red)'
      : 'var(--amber)';

  return (
    <div
      className="h-screen w-screen flex flex-col"
      style={{ padding: 12, gap: 8, background: 'var(--bg)' }}
    >
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

          <div className="ml-3 flex items-center" style={{ border: '1px solid rgba(255,255,255,0.06)', borderRadius: 6 }}>
            <button
              type="button"
              onClick={() => setViewMode('macro')}
              className="px-3 py-1 text-[10px] cursor-pointer label"
              style={{
                color: viewMode === 'macro' ? 'var(--cyan)' : 'var(--text-muted)',
                background: viewMode === 'macro' ? 'rgba(100,210,255,0.08)' : 'transparent',
              }}
            >
              宏观线路
            </button>
            <button
              type="button"
              onClick={() => setViewMode('micro')}
              className="px-3 py-1 text-[10px] cursor-pointer label"
              style={{
                color: viewMode === 'micro' ? 'var(--l9)' : 'var(--text-muted)',
                background: viewMode === 'micro' ? 'rgba(168,214,74,0.08)' : 'transparent',
              }}
            >
              轨道级
            </button>
          </div>
        </div>

        <div className="flex items-center gap-3 text-[10px] board-num" style={{ color: 'var(--text-muted)' }}>
          <span className="led led-online" /> SYS ONLINE
          <span style={{ color: 'rgba(255,255,255,0.06)' }}>|</span>
          <span>API <span style={{ color: statusColor }}>{backendStatus.toUpperCase()}</span></span>
          <span style={{ color: 'rgba(255,255,255,0.06)' }}>|</span>
          <span>UTC+8</span>
          {linesLoading && <span style={{ color: 'var(--amber)' }}>LOADING</span>}
        </div>
      </header>

      <ControlPanel />

      <div className="flex-1 flex min-h-0" style={{ gap: 8 }}>
        <div className="flex-1 overflow-hidden relative min-w-0 map-frame">
          {viewMode === 'macro' ? <MetroMap /> : <MicroTrackView />}
        </div>

        {viewMode === 'macro' && (
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
                ? <path d="M5 1L1 5L5 9" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                : <path d="M1 1L5 5L1 9" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
              }
            </svg>
          </button>
        )}

        {viewMode === 'macro' && (
          <div
            className="shrink-0 overflow-y-auto"
            style={{
              width: collapsed ? '0px' : `${PANEL_W}px`,
              transition: 'width 320ms cubic-bezier(0.33, 1, 0.68, 1)',
            }}
          >
            <div className="flex flex-col" style={{ width: `${PANEL_W}px`, gap: 8 }}>
              <div className="shrink-0">
                <SignalScreenPanel />
              </div>
              <div className="shrink-0" style={{ height: 240 }}>
                <KPIPanel />
              </div>
              <div className="shrink-0" style={{ minHeight: 280 }}>
                <LinesPanel />
              </div>
            </div>
          </div>
        )}
      </div>

      <footer
        className="flex items-center justify-between px-5 shrink-0 board-num text-[9px]"
        style={{ color: 'var(--text-muted)', height: 18 }}
      >
        <div className="flex items-center gap-2">
          <span className="led" style={{ width: 4, height: 4, boxShadow: '0 0 4px rgba(48,209,88,0.3)' }} />
          <span>MAP: {backendStatus === 'connected' ? 'BACKEND' : 'AMAP/FALLBACK'}</span>
          <span style={{ color: 'rgba(255,255,255,0.06)' }}>|</span>
          <span>{trackMap ? `${trackMap.counts.segments} SEG` : 'NO TRACK MAP'}</span>
        </div>
        <span style={{ color: 'rgba(255,255,255,0.04)' }}>v0.2.0</span>
      </footer>
    </div>
  );
}
