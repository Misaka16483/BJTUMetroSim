import { useEffect, useMemo, useRef } from 'react';
import maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import darkStyle from '../data/darkStyle';
import MAPTILER_KEY, { MAPTILER_STYLE } from '../data/maptilerKey';
import { useSimStore } from '../store/useSimStore';
import type { MetroLineData } from '../data/amapMetroApi';
import { computeStationTransfers } from '../data/transferUtils';

const hasMapTilerKey = Boolean(MAPTILER_KEY) && String(MAPTILER_KEY) !== 'YOUR_KEY_HERE';
const styleConfig: string | maplibregl.StyleSpecification = hasMapTilerKey
  ? MAPTILER_STYLE
  : darkStyle as maplibregl.StyleSpecification;

const registeredClickLayers = new Set<string>();
let popupRef: maplibregl.Popup | null = null;
let trainMarkerRef: maplibregl.Marker | null = null;

interface StationPopupProperties {
  name: string;
  lineId: string;
  code?: string;
  mileageM?: number;
  platformIds?: string;
  platformSegmentIds?: string;
}

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

  const transferCoordSet = useMemo(() => {
    const set = new Set<string>();
    if (metroLines.length === 0) return set;
    const visibleLines = metroLines.filter((line) => !hiddenLines.has(line.id));

    const nameLines = new Map<string, Set<string>>();
    for (const line of visibleLines) {
      for (const station of line.stations) {
        const norm = normalizeStationName(station.name);
        if (!nameLines.has(norm)) nameLines.set(norm, new Set());
        nameLines.get(norm)!.add(line.name);
      }
    }

    const transferNames = new Set<string>();
    for (const [name, lines] of nameLines) {
      if (lines.size >= 2) transferNames.add(name);
    }

    for (const line of visibleLines) {
      for (const station of line.stations) {
        if (transferNames.has(normalizeStationName(station.name))) {
          set.add(`${station.lat.toFixed(4)},${station.lng.toFixed(4)}`);
        }
      }
    }

    return set;
  }, [metroLines, hiddenLines]);

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
      const layers = map.getStyle()?.layers ?? [];
      for (const layer of layers) {
        const id = layer.id;
        if ((id.includes('poi') || id.includes('place')) && layer.type === 'symbol') {
          map.setLayoutProperty(id, 'visibility', 'none');
        }
      }

      const state = useSimStore.getState();
      if (state.metroLines.length > 0) {
        renderMetroLines(map, state.metroLines, state.hiddenLines, transferCoordSet);
      }
    });

    return () => {
      if (popupRef) {
        popupRef.remove();
        popupRef = null;
      }
      if (trainMarkerRef) {
        trainMarkerRef.remove();
        trainMarkerRef = null;
      }
      registeredClickLayers.clear();
      map.remove();
      mapRef.current = null;
      styleLoaded.current = false;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || metroLines.length === 0) return;

    if (styleLoaded.current) {
      renderMetroLines(map, metroLines, hiddenLines, transferCoordSet);
    } else {
      map.once('style.load', () => renderMetroLines(map, metroLines, hiddenLines, transferCoordSet));
    }

    const visibleLines = metroLines.filter((line) => !hiddenLines.has(line.id));
    if (visibleLines.length === 0) return;

    const bounds = new maplibregl.LngLatBounds();
    for (const line of visibleLines) {
      for (const seg of line.coordinates) {
        for (const [lat, lng] of seg) {
          if (lat > 39.0 && lat < 41.0 && lng > 115.0 && lng < 118.0) {
            bounds.extend([lng, lat]);
          }
        }
      }
    }
    if (bounds.isEmpty()) {
      bounds.extend([116.1, 39.7]);
      bounds.extend([116.7, 40.1]);
    }
    map.fitBounds(bounds, { padding: 60, maxZoom: 14, duration: 600 });
  }, [metroLines, hiddenLines, transferCoordSet]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !styleLoaded.current) return;

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

function renderMetroLines(
  map: maplibregl.Map,
  lines: MetroLineData[],
  hiddenLines: Set<string>,
  transferCoordSet: Set<string>,
) {
  for (const line of lines) {
    const sourceId = `metro-line-${line.id}`;
    const stationSourceId = `metro-station-${line.id}`;
    const visible = !hiddenLines.has(line.id);

    removeMetroLayers(map, line.id);

    if (!visible) {
      if (map.getSource(sourceId)) map.removeSource(sourceId);
      if (map.getSource(stationSourceId)) map.removeSource(stationSourceId);
      continue;
    }

    const lineGeojson = {
      type: 'Feature',
      properties: { id: line.id, name: line.name, color: line.color },
      geometry: {
        type: 'MultiLineString',
        coordinates: line.coordinates.map((seg) => seg.map(([lat, lng]) => [lng, lat])),
      },
    } as maplibregl.GeoJSONSourceSpecification['data'];

    const stationsGeojson = {
      type: 'FeatureCollection',
      features: line.stations.map((station) => ({
        type: 'Feature',
        properties: {
          name: station.name,
          lineId: line.id,
          code: station.code ?? '',
          mileageM: station.mileageM ?? null,
          platformIds: (station.platformIds ?? []).join(', '),
          platformSegmentIds: (station.platformSegmentIds ?? []).join(', '),
          isTransfer: transferCoordSet.has(`${station.lat.toFixed(4)},${station.lng.toFixed(4)}`),
        },
        geometry: { type: 'Point', coordinates: [station.lng, station.lat] },
      })),
    } as maplibregl.GeoJSONSourceSpecification['data'];

    if (map.getSource(sourceId)) {
      (map.getSource(sourceId) as maplibregl.GeoJSONSource).setData(lineGeojson);
    } else {
      map.addSource(sourceId, { type: 'geojson', data: lineGeojson });
    }

    if (map.getSource(stationSourceId)) {
      (map.getSource(stationSourceId) as maplibregl.GeoJSONSource).setData(stationsGeojson);
    } else {
      map.addSource(stationSourceId, { type: 'geojson', data: stationsGeojson });
    }

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

    map.on('mouseenter', `${sourceId}-main`, () => {
      map.getCanvas().style.cursor = 'pointer';
    });
    map.on('mouseleave', `${sourceId}-main`, () => {
      map.getCanvas().style.cursor = '';
    });

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

    addLayerIfNotExists(map, {
      id: `${stationSourceId}-dot`,
      type: 'circle',
      source: stationSourceId,
      minzoom: 10,
      paint: {
        'circle-color': ['case', ['get', 'isTransfer'], '#ff5533', '#ffffff'],
        'circle-radius': ['interpolate', ['linear'], ['zoom'], 10, 3, 13, 6, 16, 10],
        'circle-stroke-color': ['case', ['get', 'isTransfer'], '#ffffff', line.color],
        'circle-stroke-width': ['interpolate', ['linear'], ['zoom'], 10, 1, 13, 2, 16, 3],
        'circle-stroke-opacity': ['case', ['get', 'isTransfer'], 0.9, 1],
      },
    });

    map.on('mouseenter', `${stationSourceId}-dot`, () => {
      map.getCanvas().style.cursor = 'pointer';
    });
    map.on('mouseleave', `${stationSourceId}-dot`, () => {
      map.getCanvas().style.cursor = '';
    });

    if (!registeredClickLayers.has(stationSourceId)) {
      registeredClickLayers.add(stationSourceId);
      map.on('click', `${stationSourceId}-dot`, (event) => {
        const { metroLines: latestLines } = useSimStore.getState();
        if (!event.features || event.features.length === 0) return;

        const properties = event.features[0].properties as StationPopupProperties;
        const { name, lineId } = properties;
        if (!name) return;

        const clickedNorm = normalizeStationName(name);
        const allTransfers = computeStationTransfers(latestLines);
        const stationEntries: { line: MetroLineData; index: number }[] = [];

        for (const station of allTransfers) {
          if (normalizeStationName(station.name) !== clickedNorm) continue;
          const matchedLine = latestLines.find((line) => line.id === station.lineId);
          if (!matchedLine) continue;
          const idx = matchedLine.stations.findIndex((item) => normalizeStationName(item.name) === clickedNorm);
          if (idx !== -1 && !stationEntries.some((entry) => entry.line.id === matchedLine.id)) {
            stationEntries.push({ line: matchedLine, index: idx });
          }
        }

        if (stationEntries.length === 0) {
          const currentLine = latestLines.find((line) => line.id === lineId);
          if (currentLine) {
            const idx = currentLine.stations.findIndex((item) => normalizeStationName(item.name) === clickedNorm);
            if (idx !== -1) stationEntries.push({ line: currentLine, index: idx });
          }
        }

        if (popupRef) popupRef.remove();
        popupRef = new maplibregl.Popup({
          closeButton: false,
          closeOnClick: true,
          className: 'metro-station-popup',
          maxWidth: '260px',
          offset: [0, -8],
        })
          .setLngLat(event.lngLat)
          .setHTML(buildPopupHtml(name, stationEntries, properties))
          .addTo(map);
      });
    }

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

function addLayerIfNotExists(map: maplibregl.Map, layer: maplibregl.LayerSpecification) {
  if (map.getLayer(layer.id)) return;
  map.addLayer(layer);
}

function buildPopupHtml(
  name: string,
  entries: { line: MetroLineData; index: number }[],
  properties: StationPopupProperties,
): string {
  const isTransfer = entries.length > 1;
  const rows = entries
    .map(
      (entry) => `
      <div class="popup-row">
        <span class="popup-row-color" style="background:${entry.line.color}"></span>
        <span class="popup-row-label">${entry.line.name}</span>
        <span class="popup-row-num">#${entry.index + 1}</span>
      </div>`,
    )
    .join('');
  const transferBadge = isTransfer
    ? '<span class="popup-transfer-badge">换乘站</span>'
    : '';
  const mileage = typeof properties.mileageM === 'number'
    ? `K${(properties.mileageM / 1000).toFixed(3)}`
    : '-';
  return `
    <div class="station-popup">
      <div class="popup-name">${name}${transferBadge}</div>
      <div class="popup-rows">${rows}</div>
      ${properties.code ? `
        <div class="popup-meta">
          <div><span>站码</span><b>${properties.code}</b></div>
          <div><span>里程</span><b>${mileage}</b></div>
          <div><span>站台</span><b>${properties.platformIds || '-'}</b></div>
          <div><span>Seg</span><b>${properties.platformSegmentIds || '-'}</b></div>
        </div>
      ` : ''}
    </div>
  `;
}

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

function normalizeStationName(name: string): string {
  return name.replace(/站$/, '').trim();
}
