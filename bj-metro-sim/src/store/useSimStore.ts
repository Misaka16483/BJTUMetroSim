import { create } from 'zustand';
import type { MetroLineData } from '../data/amapMetroApi';
import type { TrackMapData } from '../data/backendApi';

type ViewMode = 'macro' | 'micro';

/** 从 Amap 9号线数据中提取站名列表（去"站"后缀） */
export function deriveStations9(line9: MetroLineData | undefined): string[] {
  if (!line9) return [];
  return line9.stations.map((s) => s.name.replace(/站$/, ''));
}

interface SimState {
  // 仿真状态
  isRunning: boolean;
  speed: number;
  simTime: string;
  dayType: 'weekday' | 'friday' | 'saturday' | 'sunday';

  // 地铁线路数据
  metroLines: MetroLineData[];
  linesLoading: boolean;
  linesError: string | null;
  backendStatus: 'idle' | 'connected' | 'fallback' | 'error';
  hiddenLines: Set<string>;
  line9Stations: string[];

  // KPI
  punctuality: number;
  avgWaitTime: number;
  avgLoadRate: number;
  totalPassengers: number;
  totalBoarded: number;

  // 选中的列车ID
  selectedTrainId: string | null;

  // 信号屏 / MMI 字段
  driveMode: string;
  currentStation: string;
  nextStation: string;
  endStation: string;
  distanceToNextStationM: number;
  targetDistanceM: number;
  currentSpeedMps: number;
  permittedSpeedMps: number;
  targetSpeedMps: number;
  runDirection: 'UP' | 'DOWN';
  stationIndex: number;

  // 列车在地图上的位置
  trainLat: number | null;
  trainLng: number | null;

  // 当前区间进度 0~1
  segmentProgress: number;

  // 动作
  toggleRunning: () => void;
  setSpeed: (speed: number) => void;
  setDayType: (dayType: 'weekday' | 'friday' | 'saturday' | 'sunday') => void;
  selectTrain: (id: string | null) => void;
  tick: () => void;

  // 线路管理
  setMetroLines: (lines: MetroLineData[]) => void;
  setLinesLoading: (loading: boolean) => void;
  setLinesError: (error: string | null) => void;
  setBackendStatus: (status: 'idle' | 'connected' | 'fallback' | 'error') => void;
  setTrackMap: (trackMap: TrackMapData | null) => void;
  setViewMode: (viewMode: ViewMode) => void;
  toggleLineVisibility: (lineId: string) => void;
  showAllLines: () => void;
  hideAllLines: () => void;
  showOnlyLines: (ids: string[]) => void;
}

let tickCount = 0;
let simSecAccum = 7 * 3600; // 07:00:00 起点
let currentRunDirection: 'UP' | 'DOWN' = 'DOWN';

// ── 9号线 polyline 缓存（用于列车地图位置插值）──
let cachedPolyline: [number, number][] | null = null;
let cachedStationPolyIdx: number[] | null = null; // stationIndex → polylineIndex

/** 构建 polyline 缓存：扁平化坐标 + 每个站点在 polyline 上的最近点索引 */
function buildPolylineCache(line9: MetroLineData) {
  const flat: [number, number][] = [];
  for (const seg of line9.coordinates) {
    for (const pt of seg) {
      flat.push([pt[0], pt[1]]);
    }
  }
  cachedPolyline = flat;

  // 为每个站点找 polyline 上最近的点
  const indices: number[] = [];
  for (const stn of line9.stations) {
    let bestIdx = 0;
    let bestDist = Infinity;
    for (let i = 0; i < flat.length; i++) {
      const d = (flat[i][0] - stn.lat) ** 2 + (flat[i][1] - stn.lng) ** 2;
      if (d < bestDist) { bestDist = d; bestIdx = i; }
    }
    indices.push(bestIdx);
  }
  cachedStationPolyIdx = indices;
}

/** 沿 polyline 在两个站点之间插值，progress: 0=fromStation, 1=toStation */
function interpolateOnPolyline(
  fromStationIdx: number,
  toStationIdx: number,
  progress: number,
): [number, number] | null {
  if (!cachedPolyline || !cachedStationPolyIdx) return null;
  const poly = cachedPolyline;

  let fromIdx = cachedStationPolyIdx[fromStationIdx];
  let toIdx = cachedStationPolyIdx[toStationIdx];

  // 确保 fromIdx < toIdx（polyline 本身是下行方向）
  if (fromIdx === undefined || toIdx === undefined) return null;
  const reversed = fromIdx > toIdx;
  if (reversed) {
    [fromIdx, toIdx] = [toIdx, fromIdx];
    progress = 1 - progress;
  }

  if (fromIdx === toIdx) return poly[fromIdx];

  // 取子段并计算累计距离
  const sub = poly.slice(fromIdx, toIdx + 1);
  const dists: number[] = [0];
  for (let i = 1; i < sub.length; i++) {
    const d = Math.sqrt((sub[i][0] - sub[i - 1][0]) ** 2 + (sub[i][1] - sub[i - 1][1]) ** 2);
    dists.push(dists[i - 1] + d);
  }

  const totalDist = dists[dists.length - 1];
  if (totalDist === 0) return sub[0];

  const target = progress * totalDist;
  for (let i = 1; i < dists.length; i++) {
    if (dists[i] >= target) {
      const segLen = dists[i] - dists[i - 1];
      const t = segLen > 0 ? (target - dists[i - 1]) / segLen : 0;
      return [
        sub[i - 1][0] + (sub[i][0] - sub[i - 1][0]) * t,
        sub[i - 1][1] + (sub[i][1] - sub[i - 1][1]) * t,
      ];
    }
  }

  return sub[sub.length - 1];
}

export const useSimStore = create<SimState>((set, get) => ({
  isRunning: false,
  speed: 1,
  simTime: '07:00:00',
  dayType: 'weekday',
  metroLines: [],
  linesLoading: false,
  linesError: null,
  backendStatus: 'idle',
  hiddenLines: new Set<string>(),
  line9Stations: [],
  viewMode: 'macro' as ViewMode,
  trackMap: null as TrackMapData | null,
  punctuality: 98.5,
  avgWaitTime: 145,
  avgLoadRate: 68,
  totalPassengers: 0,
  totalBoarded: 0,
  selectedTrainId: null,

  // 信号屏默认值
  driveMode: 'AM',
  currentStation: '郭公庄',
  nextStation: '丰台科技园',
  endStation: '国家图书馆',
  distanceToNextStationM: 1347,
  targetDistanceM: 1400,
  currentSpeedMps: 0,
  permittedSpeedMps: 22.22,
  targetSpeedMps: 22.22,
  runDirection: 'DOWN',
  stationIndex: 0,
  trainLat: null,
  trainLng: null,
  segmentProgress: 0,

  toggleRunning: () => {
    const next = !get().isRunning;
    if (!next) { tickCount = 0; simSecAccum = 7 * 3600; currentRunDirection = 'DOWN'; }
    set({ isRunning: next, trainLat: null, trainLng: null });
  },
  setSpeed: (speed: number) => set({ speed }),
  setDayType: (dayType) => set({ dayType }),
  selectTrain: (id: string | null) => set({ selectedTrainId: id }),

  setMetroLines: (lines) => {
    const line9 = lines.find((l) => l.id === '9');
    if (line9) buildPolylineCache(line9);
    set({ metroLines: lines, linesLoading: false, line9Stations: deriveStations9(line9) });
  },
  setLinesLoading: (loading) => set({ linesLoading: loading }),
  setLinesError: (error) => set({ linesError: error, linesLoading: false }),
  setBackendStatus: (status) => set({ backendStatus: status }),
  setTrackMap: (trackMap) => set({ trackMap }),
  setViewMode: (viewMode) => set({ viewMode }),

  toggleLineVisibility: (lineId) => set((s) => {
    const next = new Set(s.hiddenLines);
    if (next.has(lineId)) next.delete(lineId);
    else next.add(lineId);
    return { hiddenLines: next };
  }),

  showAllLines: () => set({ hiddenLines: new Set() }),

  hideAllLines: () => set((s) => {
    const all = new Set(s.metroLines.map((l) => l.id));
    return { hiddenLines: all };
  }),

  showOnlyLines: (lineIds) => set((s) => {
    const all = new Set(s.metroLines.map((l) => l.id));
    lineIds.forEach((id) => all.delete(id));
    return { hiddenLines: all };
  }),

  tick: () => {
    const state = get();
    if (!state.isRunning) return;

    const stations = state.line9Stations;
    if (stations.length === 0) return;

    tickCount++;
    const speedMult = state.speed;
    const dt = 0.1 * speedMult; // real seconds per tick

    // 仿真时钟: 1:1 推进
    simSecAccum += dt;
    const totalSec = simSecAccum;
    const h = Math.floor(totalSec / 3600) % 24;
    const m = Math.floor((totalSec % 3600) / 60);
    const s = Math.floor(totalSec % 60);
    const newTime = `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;

    // 真实 9号线: 每站约 2min (120s), 站距 ~1300m, 最高 80km/h
    // 速度曲线: 加速(~30s)→巡航(~60s)→制动(~30s)
    const segDist = 1347; // 郭公庄→丰台科技园 ~1347m
    const stationTime = 120; // 每站 120 秒 仿真时间
    const totalSegments = stations.length - 1;
    const routeTime = stationTime * totalSegments; // 单程总时间

    // ═══ 上下行方向自动循环 ═══
    // 根据累计仿真时间判断当前属于哪个半程
    const elapsedInCycle = simSecAccum % (routeTime * 2);
    currentRunDirection = elapsedInCycle < routeTime ? 'DOWN' : 'UP';

    // 当前半程内的位置
    const phaseTime = elapsedInCycle % routeTime;
    const simTravelDist = phaseTime * (segDist / stationTime);
    const curSegment = Math.floor(simTravelDist / segDist);
    const offsetInSegment = simTravelDist % segDist;

    // 根据方向映射站点索引
    let curStationIdx: number;
    let nextIdx: number;

    if (currentRunDirection === 'DOWN') {
      // 下行: 郭公庄(0) → 国家图书馆(N-1)
      curStationIdx = Math.min(curSegment, totalSegments - 1);
      nextIdx = Math.min(curSegment + 1, totalSegments);
    } else {
      // 上行: 国家图书馆(N-1) → 郭公庄(0)
      curStationIdx = totalSegments - Math.min(curSegment, totalSegments - 1);
      nextIdx = Math.max(curStationIdx - 1, 0);
    }

    // 加速段 25%, 巡航 50%, 制动 25%
    const accelLen = segDist * 0.25;
    const brakeLen = segDist * 0.25;
    const cruiseSpd = 22.22; // 80 km/h

    let spd = 0;
    if (offsetInSegment < accelLen) {
      spd = cruiseSpd * (offsetInSegment / accelLen);
    } else if (offsetInSegment < segDist - brakeLen) {
      spd = cruiseSpd;
    } else {
      spd = cruiseSpd * ((segDist - offsetInSegment) / brakeLen);
    }
    spd = Math.max(0, Math.min(cruiseSpd, spd));

    const targetDist = segDist - offsetInSegment;

    // KPI 平滑波动
    const kpiWait = 100 + Math.floor(Math.random() * 80);
    const kpiPunct = 96 + Math.random() * 3;

    // 当前区间进度 0~1
    const segProgress = segDist > 0 ? offsetInSegment / segDist : 0;

    // 列车地图位置 — 沿9号线 polyline 插值
    let trainLat: number | null = null;
    let trainLng: number | null = null;
    const line9 = state.metroLines.find((l) => l.id === '9');
    if (line9 && cachedPolyline) {
      // 如缓存未建则重建
      if (!cachedStationPolyIdx || cachedStationPolyIdx.length !== line9.stations.length) {
        buildPolylineCache(line9);
      }
      const pos = interpolateOnPolyline(curStationIdx, nextIdx, segProgress);
      if (pos) {
        trainLat = pos[0];
        trainLng = pos[1];
      }
    }

    set({
      simTime: newTime,
      currentSpeedMps: Math.round(spd * 100) / 100,
      permittedSpeedMps: 22.22,
      targetSpeedMps: 22.22,
      currentStation: stations[curStationIdx],
      nextStation: stations[nextIdx],
      endStation: currentRunDirection === 'DOWN' ? stations[stations.length - 1] : stations[0],
      distanceToNextStationM: Math.round(targetDist),
      targetDistanceM: segDist,
      driveMode: 'AM',
      runDirection: currentRunDirection,
      stationIndex: curStationIdx,
      segmentProgress: Math.round(segProgress * 1000) / 1000,
      trainLat,
      trainLng,

      // KPI
      punctuality: Math.round(kpiPunct * 10) / 10,
      avgWaitTime: kpiWait,
      avgLoadRate: 60 + Math.floor(Math.random() * 30),
      totalPassengers: state.totalPassengers + Math.floor(Math.random() * 100),
      totalBoarded: state.totalBoarded + Math.floor(Math.random() * 60),
    });
  },
}));
