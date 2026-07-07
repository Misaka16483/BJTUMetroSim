import { useEffect } from 'react';
import MetroMap from './components/MetroMap';
import LinesPanel from './components/LinesPanel';
import { useSimStore } from './store/useSimStore';
import { fetchBeijingMetro, getCachedMetroData, cacheMetroData } from './data/metroApi';

export default function App() {
  const setMetroLines = useSimStore((s) => s.setMetroLines);
  const setLinesLoading = useSimStore((s) => s.setLinesLoading);
  const setLinesError = useSimStore((s) => s.setLinesError);

  useEffect(() => {
    async function loadMetroData() {
      const cached = getCachedMetroData();
      if (cached && cached.length > 0) {
        setMetroLines(cached);
        return;
      }
      setLinesLoading(true);
      try {
        const lines = await fetchBeijingMetro();
        cacheMetroData(lines);
        setMetroLines(lines);
      } catch (err) {
        setLinesError(`线路数据加载失败: ${err instanceof Error ? err.message : '未知错误'}`);
      }
    }
    loadMetroData();
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
        </div>

        <div className="flex items-center gap-4 text-[10px] font-mono text-[#3a4a60]">
          <span>SYS <span className="text-[#00ff88]">ONLINE</span></span>
          <span className="text-[#1a2240]">|</span>
          <span>UTC+8</span>
        </div>
      </header>

      {/* ═══ 地图 + 面板 ═══ */}
      <div className="flex-1 flex gap-2 min-h-0">
        {/* 地图 — 发光边框 + 角标 */}
        <div className="flex-1 overflow-hidden relative min-w-0 map-frame">
          {/* 四角标记 */}
          <div className="pointer-events-none absolute top-0 left-0 w-4 h-4 border-t border-l border-[#4a9eff]/25 z-20" style={{ margin: '-1px' }} />
          <div className="pointer-events-none absolute top-0 right-0 w-4 h-4 border-t border-r border-[#4a9eff]/25 z-20" style={{ margin: '-1px' }} />
          <div className="pointer-events-none absolute bottom-0 left-0 w-4 h-4 border-b border-l border-[#4a9eff]/25 z-20" style={{ margin: '-1px' }} />
          <div className="pointer-events-none absolute bottom-0 right-0 w-4 h-4 border-b border-r border-[#4a9eff]/25 z-20" style={{ margin: '-1px' }} />
          <MetroMap />
        </div>

        <div className="w-[260px] shrink-0">
          <LinesPanel />
        </div>
      </div>

      {/* ═══ 底部状态条 ═══ */}
      <footer className="flex items-center justify-between px-4 py-1.5 mt-2 relative" style={{ borderTop: '1px solid rgba(74, 158, 255, 0.08)' }}>
        <div className="absolute top-0 left-0 right-0 h-px bg-gradient-to-r from-transparent via-[#4a9eff]/15 to-transparent" />
        <div className="flex items-center gap-3 text-[9px] font-mono" style={{ color: '#2a3040' }}>
          <span className="text-[#00ff88]">■</span>
          <span>RASTER / VECTOR · OSM</span>
        </div>
        <span className="text-[9px] font-mono" style={{ color: '#1a2240' }}>v0.1.0</span>
      </footer>
    </div>
  );
}
