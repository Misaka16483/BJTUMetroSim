import { useEffect, useState } from 'react';
import MetroMap from './components/MetroMap';
import LinesPanel from './components/LinesPanel';
import MicroTrackView from './components/MicroTrackView';
import { useSimStore } from './store/useSimStore';
import { fetchAmapBeijingMetro, getCachedAmapData, getPartialAmapCache, cacheAmapData } from './data/amapMetroApi';
import { fetchBackendBundle } from './data/backendApi';

// 模块级标记, 防止 StrictMode 重挂载触发双重请求
let globalFetching = false;

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

  const [collapsed, setCollapsed] = useState(false);

  function loadAmapData(reason: string) {
    if (globalFetching) return;

    globalFetching = true;
    setLinesLoading(true);

    // 1) 后端不可用时，优先加载仓库内置静态 JSON。
    fetch('/beijing_metro_lines.json')
      .then((resp) => {
        if (!resp.ok) throw new Error('no static file');
        return resp.json();
      })
      .then((lines) => {
        console.log(`[MetroData] 静态JSON命中: ${lines.length} 条线路`);
        setMetroLines(lines);
        setLinesError(null);
        setBackendStatus('fallback');
        setLinesLoading(false);
        globalFetching = false;
      })
      .catch(() => {
        // 2) 静态文件不存在，降级到 localStorage 缓存。
        const cached = getCachedAmapData();
        if (cached && cached.length > 0) {
          console.log(`[MetroData] localStorage缓存命中: ${cached.length} 条线路`);
          setMetroLines(cached);
          setLinesError(null);
          setBackendStatus('fallback');
          setLinesLoading(false);
          globalFetching = false;
          return;
        }

        // 3) 最后走 AMAP API。
        const amapKey = import.meta.env.VITE_AMAP_KEY as string | undefined;
        const hasAmapKey = amapKey && amapKey !== 'your_amap_key_here';
        if (!hasAmapKey) {
          setLinesError(`${reason}; 且未配置 VITE_AMAP_KEY`);
          setBackendStatus('error');
          setLinesLoading(false);
          globalFetching = false;
          return;
        }

        fetchAmapBeijingMetro(amapKey)
          .then((lines) => {
            console.log(`[MetroData] 高德返回: ${lines.length} 条线路`);
            cacheAmapData(lines);
            setMetroLines(lines);
            setLinesError(null);
            setBackendStatus('fallback');
          })
          .catch((err) => {
            console.error('[MetroData] 加载失败:', err);
            const fallback = getPartialAmapCache();
            if (fallback && fallback.length > 0) {
              console.warn(`[MetroData] 降级使用缓存: ${fallback.length} 条 (不完整)`);
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
        console.warn('[MetroData] 后端不可用, 尝试高德兜底:', msg);
        globalFetching = false;
        loadAmapData(`本地后端不可用: ${msg}`);
      })
      .finally(() => {
        setLinesLoading(false);
        globalFetching = false;
      });
  }, []);

  return (
    <div className="h-screen w-screen bg-[#020408] flex flex-col p-3" style={{ fontFamily: "'Inter', -apple-system, sans-serif" }}>
      {/* ═══ 标题栏 ═══ */}
      <header className="flex items-center justify-between px-4 py-3 mb-2 relative" style={{ borderBottom: '1px solid rgba(74, 158, 255, 0.12)' }}>
        {/* 底边发光 */}
        <div className="absolute bottom-0 left-0 right-0 h-px bg-gradient-to-r from-transparent via-[#4a9eff]/30 to-transparent" />

        <div className="flex items-center gap-3">
          {/* 脉冲状态 LED */}
          <div className="relative flex items-center justify-center">
            <span className="animated-pulse block w-2 h-2 bg-[#00ff88] relative z-10" style={{ boxShadow: '0 0 8px #00ff88, 0 0 16px rgba(0,255,136,0.4)' }} />
            <span className="absolute w-4 h-4 rounded-full bg-[#00ff88]/10 animate-ping" />
          </div>

          <span className="text-[13px] font-semibold tracking-tight text-[#c8d8f0]">
            BJTUMetroSim
          </span>

          <span className="text-[10px] tracking-[0.15em] uppercase text-[#4a5568] px-2 py-0.5 border border-[#1a2240]/60">
            Dispatch Console
          </span>

          <div className="ml-3 flex items-center border border-[#1a2240]/70">
            <button
              type="button"
              onClick={() => setViewMode('macro')}
              className="px-3 py-1 text-[10px] cursor-pointer"
              style={{
                color: viewMode === 'macro' ? '#dce8f8' : '#52647b',
                background: viewMode === 'macro' ? 'rgba(74,158,255,0.12)' : 'transparent',
              }}
            >
              宏观线路
            </button>
            <button
              type="button"
              onClick={() => setViewMode('micro')}
              className="px-3 py-1 text-[10px] cursor-pointer"
              style={{
                color: viewMode === 'micro' ? '#dce8f8' : '#52647b',
                background: viewMode === 'micro' ? 'rgba(143,195,31,0.14)' : 'transparent',
              }}
            >
              轨道级
            </button>
          </div>
        </div>

        <div className="flex items-center gap-4 text-[10px] font-mono text-[#3a4a60]">
          <span>SYS <span className="text-[#00ff88]">ONLINE</span></span>
          <span className="text-[#1a2240]">|</span>
          <span>
            API <span className={backendStatus === 'connected' ? 'text-[#00ff88]' : 'text-[#d29922]'}>
              {backendStatus.toUpperCase()}
            </span>
          </span>
          <span className="text-[#1a2240]">|</span>
          <span>LINE 9</span>
        </div>
      </header>

      {/* ═══ 地图 + 面板 ═══ */}
      <div className="flex-1 flex min-h-0 relative">
        {/* 地图 — 发光边框 + 角标 */}
        <div className="flex-1 overflow-hidden relative min-w-0 map-frame">
          {/* 四角标记 */}
          <div className="pointer-events-none absolute top-0 left-0 w-4 h-4 border-t border-l border-[#4a9eff]/25 z-20" style={{ margin: '-1px' }} />
          <div className="pointer-events-none absolute top-0 right-0 w-4 h-4 border-t border-r border-[#4a9eff]/25 z-20" style={{ margin: '-1px' }} />
          <div className="pointer-events-none absolute bottom-0 left-0 w-4 h-4 border-b border-l border-[#4a9eff]/25 z-20" style={{ margin: '-1px' }} />
          <div className="pointer-events-none absolute bottom-0 right-0 w-4 h-4 border-b border-r border-[#4a9eff]/25 z-20" style={{ margin: '-1px' }} />
          {viewMode === 'macro' ? <MetroMap /> : <MicroTrackView />}
        </div>

        {/* 折叠按钮 */}
        {viewMode === 'macro' && (
        <button
          onClick={() => setCollapsed((v) => !v)}
          className="absolute top-1/2 -translate-y-1/2 w-7 h-7 flex items-center justify-center z-20 cursor-pointer group rounded-l-full"
          style={{
            right: collapsed ? '0px' : '260px',
            background: 'linear-gradient(180deg, #0e1526 0%, #090d14 100%)',
            border: '1px solid rgba(74, 158, 255, 0.22)',
            borderRight: 'none',
            color: '#556278',
            boxShadow: collapsed
              ? 'inset 0 1px 0 rgba(255,255,255,0.04), -2px 0 6px rgba(0,0,0,0.5)'
              : 'inset 0 1px 0 rgba(255,255,255,0.04)',
            transition: 'right 300ms cubic-bezier(0.4,0,0.2,1), color 200ms, border-color 200ms, box-shadow 200ms',
          }}
          title={collapsed ? '展开面板' : '收起面板'}
        >
          <svg width="5" height="8" viewBox="0 0 5 8" fill="none"
            className="transition-all duration-200 group-hover:text-[#4a9eff] group-hover:drop-shadow-[0_0_5px_rgba(74,158,255,0.7)]"
          >
            {collapsed ? (
              <path d="M4.5 1L1 4L4.5 7" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
            ) : (
              <path d="M0.5 1L4 4L0.5 7" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
            )}
          </svg>
        </button>
        )}

        {/* 面板容器 */}
        {viewMode === 'macro' && (
        <div
          className="shrink-0 overflow-hidden"
          style={{ width: collapsed ? '0px' : '260px', transition: 'width 300ms ease' }}
        >
          <div style={{ width: '260px' }} className="h-full relative">
            <div className="absolute top-0 left-0 bottom-0 w-px bg-gradient-to-b from-transparent via-[#4a9eff]/30 to-transparent" />
            <LinesPanel />
          </div>
        </div>
        )}
      </div>

      {/* ═══ 底部状态条 ═══ */}
      <footer className="flex items-center justify-between px-4 py-1.5 mt-2 relative" style={{ borderTop: '1px solid rgba(74, 158, 255, 0.08)' }}>
        <div className="absolute top-0 left-0 right-0 h-px bg-gradient-to-r from-transparent via-[#4a9eff]/15 to-transparent" />
        <div className="flex items-center gap-3 text-[9px] font-mono" style={{ color: '#2a3040' }}>
          <span className="text-[#00ff88]">■</span>
          <span>PHASE0 API · {trackMap ? `${trackMap.counts.segments} SEG` : 'NO TRACK MAP'}</span>
        </div>
        <span className="text-[9px] font-mono" style={{ color: '#1a2240' }}>v0.1.0</span>
      </footer>
    </div>
  );
}
