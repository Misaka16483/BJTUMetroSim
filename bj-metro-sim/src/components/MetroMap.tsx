import { useEffect, useRef } from 'react';
import maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import darkStyle from '../data/darkStyle';
import MAPTILER_KEY, { MAPTILER_STYLE } from '../data/maptilerKey';
import { useSimStore } from '../store/useSimStore';
import type { MetroLineData } from '../data/metroApi';
import { computeTransferGroups } from '../data/transferUtils';

// 判断是否有有效的 MapTiler Key
const hasMapTilerKey = MAPTILER_KEY && MAPTILER_KEY !== 'YOUR_KEY_HERE';
const styleConfig = hasMapTilerKey ? MAPTILER_STYLE : darkStyle;

export default function MetroMap() {
  const mapContainer = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const styleLoaded = useRef(false);

  const metroLines = useSimStore((s) => s.metroLines);
  const hiddenLines = useSimStore((s) => s.hiddenLines);
  const linesLoading = useSimStore((s) => s.linesLoading);
  const linesError = useSimStore((s) => s.linesError);

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
      antialias: true,
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
        renderMetroLines(map, state.metroLines, state.hiddenLines);
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
      renderMetroLines(map, metroLines, hiddenLines);
    } else {
      const handler = () => {
        renderMetroLines(map, metroLines, hiddenLines);
      };
      map.once('style.load', handler);
    }
  }, [metroLines, hiddenLines]);

  // ── 换乘站呼吸闪烁动画 ──
  useEffect(() => {
    const map = mapRef.current;
    if (!map || metroLines.length === 0) return;

    let rafId: number;
    const start = performance.now();

    const pulse = (now: number) => {
      const t = (now - start) % 2400; // 2.4秒一个周期
      const phase = t / 2400;
      const wave = (Math.sin(phase * Math.PI * 2) + 1) / 2; // 0..1 正弦波

      if (map.getLayer('metro-transfer-glow')) {
        map.setPaintProperty('metro-transfer-glow', 'circle-opacity', 0.1 + 0.4 * wave);
      }
      if (map.getLayer('metro-transfer-core')) {
        map.setPaintProperty('metro-transfer-core', 'circle-opacity', 0.65 + 0.35 * wave);
      }

      rafId = requestAnimationFrame(pulse);
    };

    rafId = requestAnimationFrame(pulse);
    return () => cancelAnimationFrame(rafId);
  }, [metroLines]);

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
  hiddenLines: Set<string>
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
    const geojson: GeoJSON.Feature<GeoJSON.MultiLineString> = {
      type: 'Feature',
      properties: { id: line.id, name: line.name, color: line.color },
      geometry: {
        type: 'MultiLineString',
        coordinates: line.coordinates.map((seg) =>
          seg.map(([lat, lng]) => [lng, lat])
        ),
      },
    };

    // 站点 Point GeoJSON
    const stationsGeojson: GeoJSON.FeatureCollection<GeoJSON.Point> = {
      type: 'FeatureCollection',
      features: line.stations.map((s) => ({
        type: 'Feature',
        properties: { name: s.name, lineId: line.id },
        geometry: { type: 'Point', coordinates: [s.lng, s.lat] },
      })),
    };

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

    // ── 站点层：实心白色圆点 ──
    addLayerIfNotExists(map, {
      id: `${stationSourceId}-dot`,
      type: 'circle',
      source: stationSourceId,
      minzoom: 10,
      paint: {
        'circle-color': '#ffffff',
        'circle-radius': ['interpolate', ['linear'], ['zoom'], 10, 3, 13, 6, 16, 10],
        'circle-stroke-color': line.color,
        'circle-stroke-width': ['interpolate', ['linear'], ['zoom'], 10, 1, 13, 2, 16, 3],
      },
    });

    // 站点悬停变手型
    map.on('mouseenter', `${stationSourceId}-dot`, () => {
      map.getCanvas().style.cursor = 'pointer';
    });
    map.on('mouseleave', `${stationSourceId}-dot`, () => {
      map.getCanvas().style.cursor = '';
    });

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

  // ── 换乘站：红色圆环标记（跨线路去重） ──
  renderTransferStations(map, lines, hiddenLines);
}

// ── 辅助：安全添加图层 ──
function addLayerIfNotExists(map: maplibregl.Map, layer: maplibregl.LayerSpecification) {
  if (map.getLayer(layer.id)) return;
  map.addLayer(layer);
}

// ── 换乘站检测与渲染 — 按坐标距离匹配（300m内视为同站） ──
function renderTransferStations(
  map: maplibregl.Map,
  lines: MetroLineData[],
  hiddenLines: Set<string>
) {
  const sourceId = 'metro-transfer-stations';
  const visibleLines = lines.filter((l) => !hiddenLines.has(l.id));
  const transferGroups = computeTransferGroups(visibleLines);

  // 移除旧层
  for (const id of ['metro-transfer-glow', 'metro-transfer-ring', 'metro-transfer-core']) {
    if (map.getLayer(id)) map.removeLayer(id);
  }
  if (map.getSource(sourceId)) map.removeSource(sourceId);
  if (transferGroups.length === 0) return;

  const features: GeoJSON.Feature<GeoJSON.Point>[] = transferGroups.map((g) => ({
    type: 'Feature',
    properties: { lineCount: g.lineIds.length },
    geometry: { type: 'Point', coordinates: [g.lng, g.lat] },
  }));

  map.addSource(sourceId, {
    type: 'geojson',
    data: { type: 'FeatureCollection', features },
  });

  // 换乘站标记：尺寸匹配普通站，通过缓慢闪烁区分
  // 第1层：柔和红色光晕（略大于圆点，用于呼吸动画）
  map.addLayer({
    id: 'metro-transfer-glow',
    type: 'circle',
    source: sourceId,
    minzoom: 10,
    paint: {
      'circle-color': '#ff4444',
      'circle-radius': ['interpolate', ['linear'], ['zoom'], 10, 5, 13, 10, 16, 16],
      'circle-opacity': 0.4,
      'circle-blur': 2,
    },
  });

  // 第2层：白色细环
  map.addLayer({
    id: 'metro-transfer-ring',
    type: 'circle',
    source: sourceId,
    minzoom: 10,
    paint: {
      'circle-radius': ['interpolate', ['linear'], ['zoom'], 10, 3, 13, 6, 16, 10],
      'circle-color': 'transparent',
      'circle-stroke-color': '#ffffff',
      'circle-stroke-width': ['interpolate', ['linear'], ['zoom'], 10, 0.6, 13, 1, 16, 1.5],
      'circle-stroke-opacity': 0.6,
    },
  });

  // 第3层：红色实心圆点（大小与普通站一致）
  map.addLayer({
    id: 'metro-transfer-core',
    type: 'circle',
    source: sourceId,
    minzoom: 10,
    paint: {
      'circle-color': '#ff3333',
      'circle-radius': ['interpolate', ['linear'], ['zoom'], 10, 3, 13, 6, 16, 10],
    },
  });
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
