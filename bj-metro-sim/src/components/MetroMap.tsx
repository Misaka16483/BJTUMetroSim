import { useEffect, useRef, useMemo } from 'react';
import maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import darkStyle from '../data/darkStyle';
import MAPTILER_KEY, { MAPTILER_STYLE } from '../data/maptilerKey';
import { useSimStore } from '../store/useSimStore';
import type { MetroLineData } from '../data/amapMetroApi';
import { computeStationTransfers } from '../data/transferUtils';

// 判断是否有有效的 MapTiler Key
const hasMapTilerKey = Boolean(MAPTILER_KEY) && String(MAPTILER_KEY) !== 'YOUR_KEY_HERE';
const styleConfig: string | maplibregl.StyleSpecification = hasMapTilerKey
  ? MAPTILER_STYLE
  : darkStyle as maplibregl.StyleSpecification;

// ── 模块级：防重复注册 & 弹窗引用 ──
const registeredClickLayers = new Set<string>();
let popupRef: maplibregl.Popup | null = null;
let trainMarkerRef: maplibregl.Marker | null = null;

export default function MetroMap() {
  const mapContainer = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const styleLoaded = useRef(false);

  const metroLines = useSimStore((s) => s.metroLines);
  const hiddenLines = useSimStore((s) => s.hiddenLines);
  const linesLoading = useSimStore((s) => s.linesLoading);
  const linesError = useSimStore((s) => s.linesError);
  const trainLat = useSimStore((s) => s.trainLat);
  const trainLng = useSimStore((s) => s.trainLng);
  const isRunning = useSimStore((s) => s.isRunning);

  // ── 初始化地图 ──
  useEffect(() => {
    if (!mapContainer.current || mapRef.current) return;

    const map = new maplibregl.Map({
      container: mapContainer.current,
      style: styleConfig,
      center: [116.38, 39.91],
      zoom: 10.5,
      minZoom: 9,
      maxZoom: 16,
      attributionControl: false,
    });

    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'bottom-right');
    mapRef.current = map;

    map.on('style.load', () => {
      styleLoaded.current = true;

      // 隐藏底图 POI 符号 — 避免与地铁站点白色圆点混淆
      const layers = map.getStyle()?.layers ?? [];
      for (const layer of layers) {
        const id = layer.id;
        if (
          id.includes('poi') || id.includes('place') && layer.type === 'symbol'
        ) {
          map.setLayoutProperty(id, 'visibility', 'none');
        }
      }

      // 如果线路数据已经加载，立即绘制
      const state = useSimStore.getState();
      if (state.metroLines.length > 0) {
        renderMetroLines(map, state.metroLines, state.hiddenLines, transferCoordSet);
      }
    });

    return () => {
      map.remove();
      mapRef.current = null;
      styleLoaded.current = false;
    };
  }, []);

  // ── 线路数据更新时重绘 ──
  useEffect(() => {
    const map = mapRef.current;
    if (!map || metroLines.length === 0) return;

    if (styleLoaded.current) {
      renderMetroLines(map, metroLines, hiddenLines, transferCoordSet);
    } else {
      const handler = () => {
        renderMetroLines(map, metroLines, hiddenLines, transferCoordSet);
      };
      map.once('style.load', handler);
    }

    // 自动适配地图视野到可见线路
    const visibleLines = metroLines.filter((l) => !hiddenLines.has(l.id));
    if (visibleLines.length === 0) return;
    const bounds = new maplibregl.LngLatBounds();
    for (const line of visibleLines) {
      for (const seg of line.coordinates) {
        for (const [lat, lng] of seg) {
          // 过滤异常坐标（北京范围外的不参与 bounds）
          if (lat > 39.0 && lat < 41.0 && lng > 115.0 && lng < 118.0) {
            bounds.extend([lng, lat]);
          }
        }
      }
    }
    // 兜底：如果没收集到有效 bounds，使用北京默认范围
    if (bounds.isEmpty()) {
      bounds.extend([116.1, 39.7]);
      bounds.extend([116.7, 40.1]);
    }
    map.fitBounds(bounds, { padding: 60, maxZoom: 14, duration: 600 });
  }, [metroLines, hiddenLines]);

  // ── 换乘站坐标集（用于站点点色标记 — 按站名匹配, 基于网络拓扑） ──
  const transferCoordSet = useMemo(() => {
    const set = new Set<string>();
    if (metroLines.length === 0) return set;
    const visibleLines = metroLines.filter((l) => !hiddenLines.has(l.id));

    // 按归一化站名分组 → 同组 2 线以上 = 换乘
    const nameLines = new Map<string, Set<string>>();
    for (const l of visibleLines) {
      for (const s of l.stations) {
        const norm = s.name.replace(/站$/, '').trim();
        if (!nameLines.has(norm)) nameLines.set(norm, new Set());
        nameLines.get(norm)!.add(l.name);
      }
    }
    const transferNames = new Set<string>();
    for (const [norm, lines] of nameLines) {
      if (lines.size >= 2) transferNames.add(norm);
    }

    // 标记所有换乘站坐标
    for (const l of visibleLines) {
      for (const s of l.stations) {
        const norm = s.name.replace(/站$/, '').trim();
        if (transferNames.has(norm)) {
          set.add(`${s.lat.toFixed(4)},${s.lng.toFixed(4)}`);
        }
      }
    }

    // ── 批量验证：全量对比白点/红点 ──
    // 按 nameLines 的 key（归一化站名）字典序排列，逐一输出
    const sortedNames = [...nameLines.keys()].sort((a, b) => a.localeCompare(b, 'zh'));
    const redList: string[] = [];
    const whiteList: string[] = [];
    const detailRows: string[] = [];

    sortedNames.forEach((norm) => {
      const lineSet = nameLines.get(norm)!;
      const linesStr = [...lineSet].join(',');
      const isTransfer = transferNames.has(norm);
      const mark = isTransfer ? 'RED' : 'WHITE';
      detailRows.push(`  ${norm.padEnd(8)} | ${linesStr.padEnd(30)} | ${mark}`);
      if (isTransfer) {
        redList.push(`${norm}[${linesStr}]`);
      } else {
        whiteList.push(`${norm}[${linesStr}]`);
      }
    });

    console.log(
      `\n%c══════════════════════════════════════════════`,
      'color:#4a9eff'
    );
    console.log(
      `%c[Transfer] 全量站点对比 (共 ${sortedNames.length} 个不同站名)`,
      'color:#ffcc00;font-weight:bold'
    );
    console.log(
      `%c  RED   = 换乘站 (${redList.length} 个)\n  WHITE = 普通站 (${whiteList.length} 个)`,
      'color:#aaa'
    );
    console.log(
      `%c  ─────────────────────────────────────────`,
      'color:#555'
    );
    console.log(
      `%c  站名      | 所属线路                       | 颜色`,
      'color:#888'
    );
    console.log(
      `%c  ─────────────────────────────────────────`,
      'color:#555'
    );
    // 分批输出避免控制台截断
    const chunkSize = 80;
    for (let i = 0; i < detailRows.length; i += chunkSize) {
      console.log(detailRows.slice(i, i + chunkSize).join('\n'));
    }
    console.log(
      `%c  ─────────────────────────────────────────`,
      'color:#555'
    );
    console.log(
      `%c  RED 换乘站汇总 (${redList.length}):`,
      'color:#ff5533;font-weight:bold'
    );
    console.log(redList.join('\n'));
    console.log(
      `%c  WHITE 普通站汇总 (${whiteList.length}):`,
      'color:#ffffff'
    );
    console.log(whiteList.join('\n'));
    console.log(
      `%c══════════════════════════════════════════════\n`,
      'color:#4a9eff'
    );

    // ── 漏检测试 ──
    const multiLine = [...nameLines.keys()].filter((n) => nameLines.get(n)!.size >= 2);
    const missed = multiLine.filter((n) => !transferNames.has(n));
    if (missed.length > 0) {
      console.warn('[Transfer] ⚠ 漏检 (同名多线但标记为 WHITE):', missed.join(', '));
    } else {
      console.log('%c[Transfer] ✅ 验证通过 — 所有同名多线站点均标记为 RED', 'color:#4f8');
    }

    // ── 全量线路+站点清单 ──
    console.log(
      `\n%c══════════════════════════════════════════════`,
      'color:#4a9eff'
    );
    console.log(
      `%c[DataDump] 全量线路站点 (${visibleLines.length} 条线路)`,
      'color:#ffcc00;font-weight:bold'
    );
    for (const l of visibleLines) {
      const color = l.color || '#888';
      console.groupCollapsed(
        `%c● ${l.name} %c(${l.stations.length}站) %c${l.id} %ccolor=${l.color}`,
        `color:${color};font-weight:bold`,
        'color:#aaa',
        'color:#555;font-size:10px',
        'color:#888;font-size:9px'
      );
      l.stations.forEach((s, i) => {
        const norm = s.name.replace(/站$/, '').trim();
        const isT = transferNames.has(norm);
        console.log(
          `%c${String(i + 1).padStart(3, ' ')} %c${s.name.padEnd(8)} %c${isT ? 'RED' : 'WHITE'} %c${s.lat.toFixed(4)},${s.lng.toFixed(4)}`,
          'color:#666',
          isT ? 'color:#ff5533;font-weight:bold' : 'color:#ddd',
          isT ? 'color:#ff5533' : 'color:#888',
          'color:#444;font-size:10px'
        );
      });
      console.groupEnd();
    }
    console.log(
      `%c══════════════════════════════════════════════\n`,
      'color:#4a9eff'
    );

    return set;
  }, [metroLines, hiddenLines]);

  // ── 列车位置标记 ──
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !styleLoaded.current) return;

    // 清除旧标记
    if (trainMarkerRef) {
      trainMarkerRef.remove();
      trainMarkerRef = null;
    }

    if (!isRunning || trainLat == null || trainLng == null) return;

    const el = document.createElement('div');
    el.className = 'train-marker';
    el.innerHTML = `
      <div style="
        width: 18px; height: 18px;
        background: var(--l9, #8FC31F);
        border-radius: 50% 50% 50% 0;
        transform: rotate(-45deg);
        box-shadow: 0 0 12px rgba(168,214,74,0.5), 0 0 24px rgba(168,214,74,0.2);
        border: 2px solid rgba(255,255,255,0.5);
      "></div>
    `;

    trainMarkerRef = new maplibregl.Marker({ element: el, anchor: 'bottom' })
      .setLngLat([trainLng, trainLat])
      .addTo(map);
  }, [trainLat, trainLng, isRunning]);

  return (
    <div className="relative w-full h-full">
      {linesLoading && (
        <div className="absolute top-4 left-1/2 -translate-x-1/2 z-10 bg-[#1a1a2e] border border-[#333] text-[#8ab4f8] px-6 py-3 rounded-lg shadow-2xl text-sm font-medium animate-pulse">
          <span className="inline-block w-4 h-4 border-2 border-[#8ab4f8] border-t-transparent rounded-full animate-spin mr-2 align-middle" />
          正在加载北京地铁线路数据...
        </div>
      )}
      {linesError && (
        <div className="absolute top-4 left-1/2 -translate-x-1/2 z-10 bg-[#2a0a0a] border border-[#ff4444]/30 text-[#ff6b6b] px-6 py-3 rounded-lg shadow-2xl text-sm">
          {linesError}
        </div>
      )}

      <div ref={mapContainer} className="w-full h-full bg-[#0d1117]" />
    </div>
  );
}

// ── 渲染/更新地铁线路 ──
function renderMetroLines(
  map: maplibregl.Map,
  lines: MetroLineData[],
  hiddenLines: Set<string>,
  transferCoordSet: Set<string>
) {
  for (const line of lines) {
    const sourceId = `metro-line-${line.id}`;
    const visible = !hiddenLines.has(line.id);

    // 清除旧图层
    removeMetroLayers(map, line.id);

    if (!visible) {
      // 隐藏的线路：移除 source
      if (map.getSource(sourceId)) {
        map.removeSource(sourceId);
      }
      continue;
    }

    // 构建 MultiLineString GeoJSON
    const geojson = {
      type: 'Feature',
      properties: { id: line.id, name: line.name, color: line.color },
      geometry: {
        type: 'MultiLineString',
        coordinates: line.coordinates.map((seg) =>
          seg.map(([lat, lng]) => [lng, lat])
        ),
      },
    } as maplibregl.GeoJSONSourceSpecification['data'];

    // 站点 Point GeoJSON
    const stationsGeojson = {
      type: 'FeatureCollection',
      features: line.stations.map((s) => ({
        type: 'Feature',
        properties: {
          name: s.name,
          lineId: line.id,
          code: s.code ?? '',
          mileageM: s.mileageM ?? null,
          platformIds: (s.platformIds ?? []).join(', '),
          platformSegmentIds: (s.platformSegmentIds ?? []).join(', '),
          isTransfer: transferCoordSet.has(`${s.lat.toFixed(4)},${s.lng.toFixed(4)}`),
        },
        geometry: { type: 'Point', coordinates: [s.lng, s.lat] },
      })),
    } as maplibregl.GeoJSONSourceSpecification['data'];

    const stationSourceId = `metro-station-${line.id}`;

    // 添加/更新线路 source
    if (map.getSource(sourceId)) {
      (map.getSource(sourceId) as maplibregl.GeoJSONSource).setData(geojson);
    } else {
      map.addSource(sourceId, { type: 'geojson', data: geojson });
    }

    // 添加/更新站点 source
    if (map.getSource(stationSourceId)) {
      (map.getSource(stationSourceId) as maplibregl.GeoJSONSource).setData(stationsGeojson);
    } else {
      map.addSource(stationSourceId, { type: 'geojson', data: stationsGeojson });
    }

    // ── 线路层：发光外层 ──
    addLayerIfNotExists(map, {
      id: `${sourceId}-glow`,
      type: 'line',
      source: sourceId,
      paint: {
        'line-color': line.color,
        'line-width': ['interpolate', ['linear'], ['zoom'], 9, 1.5, 12, 5, 16, 10],
        'line-opacity': 0.25,
        'line-blur': 4,
      },
      layout: { 'line-cap': 'round', 'line-join': 'round' },
    });

    // ── 线路层：主线 ──
    addLayerIfNotExists(map, {
      id: `${sourceId}-main`,
      type: 'line',
      source: sourceId,
      paint: {
        'line-color': line.color,
        'line-width': ['interpolate', ['linear'], ['zoom'], 9, 1, 12, 3, 16, 6],
        'line-opacity': 0.9,
      },
      layout: { 'line-cap': 'round', 'line-join': 'round' },
    });

    // ── 鼠标交互 — 悬停时变手型 ──
    map.on('mouseenter', `${sourceId}-main`, () => {
      map.getCanvas().style.cursor = 'pointer';
    });
    map.on('mouseleave', `${sourceId}-main`, () => {
      map.getCanvas().style.cursor = '';
    });

    // ── 站点层：发光外圈（白色柔光） ──
    addLayerIfNotExists(map, {
      id: `${stationSourceId}-glow`,
      type: 'circle',
      source: stationSourceId,
      minzoom: 10,
      paint: {
        'circle-color': '#ffffff',
        'circle-radius': ['interpolate', ['linear'], ['zoom'], 10, 4, 13, 8, 16, 14],
        'circle-opacity': 0.15,
        'circle-blur': 1.5,
      },
    });

    // ── 站点层：实心圆点（换乘站用暖色，普通站白色 + 线路色描边） ──
    addLayerIfNotExists(map, {
      id: `${stationSourceId}-dot`,
      type: 'circle',
      source: stationSourceId,
      minzoom: 10,
      paint: {
        'circle-color': ['case',
          ['get', 'isTransfer'], '#ff5533',
          '#ffffff',
        ],
        'circle-radius': ['interpolate', ['linear'], ['zoom'], 10, 3, 13, 6, 16, 10],
        'circle-stroke-color': ['case',
          ['get', 'isTransfer'], '#ffffff',
          line.color,
        ],
        'circle-stroke-width': ['interpolate', ['linear'], ['zoom'], 10, 1, 13, 2, 16, 3],
        'circle-stroke-opacity': ['case',
          ['get', 'isTransfer'], 0.9,
          1,
        ],
      },
    });

    // 站点悬停变手型
    map.on('mouseenter', `${stationSourceId}-dot`, () => {
      map.getCanvas().style.cursor = 'pointer';
    });
    map.on('mouseleave', `${stationSourceId}-dot`, () => {
      map.getCanvas().style.cursor = '';
    });

    // ── 站点点击弹窗（每层只注册一次） ──
    if (!registeredClickLayers.has(stationSourceId)) {
      registeredClickLayers.add(stationSourceId);
      map.on('click', `${stationSourceId}-dot`, (e) => {
        // 从 store 实时获取最新数据，避免 stale closure
        const { metroLines: latestLines } = useSimStore.getState();
        if (!e.features || e.features.length === 0) return;
        const { name, lineId } = e.features[0].properties as { name: string; lineId: string };
        if (!name) return;

        const allTransfers = computeStationTransfers(latestLines);
        const clickedNorm = name.replace(/站$/, '').trim();
        const stationEntries: { line: MetroLineData; index: number }[] = [];

        for (const st of allTransfers) {
          const stNorm = st.name.replace(/站$/, '').trim();
          if (stNorm !== clickedNorm) continue;
          const l = latestLines.find((ln) => ln.id === st.lineId);
          if (!l) continue;
          const idx = l.stations.findIndex(
            (s) => s.name.replace(/站$/, '').trim() === clickedNorm
          );
          if (idx !== -1 && !stationEntries.some((en) => en.line.id === l.id)) {
            stationEntries.push({ line: l, index: idx });
          }
        }

        if (stationEntries.length === 0) {
          const curLine = latestLines.find((l) => l.id === lineId);
          if (curLine) {
            const idx = curLine.stations.findIndex(
              (s) => s.name.replace(/站$/, '').trim() === clickedNorm
            );
            if (idx !== -1) stationEntries.push({ line: curLine, index: idx });
          }
        }

        if (popupRef) popupRef.remove();
        const html = buildPopupHtml(name, stationEntries);
        popupRef = new maplibregl.Popup({
          closeButton: false,
          closeOnClick: true,
          className: 'metro-station-popup',
          maxWidth: '240px',
          offset: [0, -8],
        })
          .setLngLat(e.lngLat)
          .setHTML(html)
          .addTo(map);
      });
    }

    // ── 站名标签（zoom >= 10 即可见，小字体） ──
    addLayerIfNotExists(map, {
      id: `${stationSourceId}-label`,
      type: 'symbol',
      source: stationSourceId,
      minzoom: 10,
      layout: {
        'text-field': ['get', 'name'],
        'text-font': ['Noto Sans Regular'],
        'text-size': ['interpolate', ['linear'], ['zoom'], 10, 8, 13, 10, 16, 13],
        'text-offset': [0, 1.5],
        'text-anchor': 'top',
        'text-allow-overlap': false,
        'text-optional': true,
      },
      paint: {
        'text-color': '#d0d8e8',
        'text-halo-color': '#040810',
        'text-halo-width': 1.5,
        'text-opacity': ['interpolate', ['linear'], ['zoom'], 10, 0.5, 12, 0.75, 14, 0.9],
      },
    });
  }
}

// ── 辅助：安全添加图层 ──
function addLayerIfNotExists(map: maplibregl.Map, layer: maplibregl.LayerSpecification) {
  if (map.getLayer(layer.id)) return;
  map.addLayer(layer);
}

function buildPopupHtml(
  name: string,
  entries: { line: MetroLineData; index: number }[],
  properties: {
    code?: string;
    mileageM?: number;
    platformIds?: string;
    platformSegmentIds?: string;
  }
): string {
  const isTransfer = entries.length > 1;
  const rows = entries
    .map(
      (e) => `
      <div class="popup-row">
        <span class="popup-row-color" style="background:${e.line.color}"></span>
        <span class="popup-row-label">${e.line.name}</span>
        <span class="popup-row-num">#${e.index + 1}</span>
      </div>`
    )
    .join('');
  const transferBadge = isTransfer
    ? '<span class="popup-transfer-badge">换乘站</span>'
    : '';
  return `
    <div class="station-popup">
      <div class="popup-name">${name}${transferBadge}</div>
      <div class="popup-rows">${rows}</div>
      ${properties.code ? `
        <div class="popup-meta">
          <div><span>站码</span><b>${properties.code}</b></div>
          <div><span>里程</span><b>K${((properties.mileageM ?? 0) / 1000).toFixed(3)}</b></div>
          <div><span>站台</span><b>${properties.platformIds || '-'}</b></div>
          <div><span>Seg</span><b>${properties.platformSegmentIds || '-'}</b></div>
        </div>
      ` : ''}
    </div>
  `;
}

// ── 辅助：移除线路相关图层和 source ──
function removeMetroLayers(map: maplibregl.Map, lineId: string) {
  const layerIds = [
    `metro-line-${lineId}-glow`,
    `metro-line-${lineId}-main`,
    `metro-station-${lineId}-glow`,
    `metro-station-${lineId}-dot`,
    `metro-station-${lineId}-label`,
  ];
  for (const id of layerIds) {
    if (map.getLayer(id)) map.removeLayer(id);
  }
}
