import { create } from 'zustand';
import type { MetroLineData } from '../data/amapMetroApi';
import type {
  PowerNetworkState,
  PowerTopology,
  SimDispatchDecision,
  SimPowerState,
  SimStateResponse,
  SimStationInfo,
  TrackMapData,
  SpeedProfilePoint,
  SpeedProfileMeta,
} from '../data/backendApi';
import { simStart, simPause, simResume, simStop } from '../data/backendApi';

type ViewMode = 'macro' | 'micro' | 'interlocking' | 'fullLine' | 'driver' | 'power';

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
  trackMap: TrackMapData | null;
  powerTopology: PowerTopology | null;
  viewMode: ViewMode;

  // KPI
  punctuality: number;
  avgWaitTime: number;
  avgLoadRate: number;
  totalPassengers: number;
  totalBoarded: number;
  totalWaitingPax: number;
  maxPlatformDensity: number;
  totalTractionEnergyKwh: number;
  minTractionLimitRatio: number;
  minTrainVoltageV: number;
  totalAbsorbedRegenKw: number;
  totalWastedRegenKw: number;
  powerLossesKw: number;
  lastDispatchAction: string;
  simStations: SimStationInfo[];
  simPower: SimPowerState[];
  simPowerNetwork: PowerNetworkState | null;
  dispatchDecisions: SimDispatchDecision[];

  // 选中的列车ID
  selectedTrainId: string | null;

  // 选中的车站代码 (用于轨道级→联锁图跳转)
  selectedStationCode: string | null;

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
  tractionPercent: number;
  brakePercent: number;
  energyKwh: number;
  estimatedRunTimeS: number;
  pathPositionM: number;
  pathTotalLengthM: number;
  currentSegmentId: number | null;
  localSpeedLimitMps: number;
  gradeRatio: number;
  pathSegmentCount: number;
  pathConstraintCount: number;
  runDirection: 'UP' | 'DOWN';
  // ── 速度曲线 ──
  speedProfile: SpeedProfilePoint[];
  speedProfileMeta: SpeedProfileMeta | null;
  speedHistory: Array<{
    positionM: number;
    speedMps: number;
    targetSpeedMps?: number;
    localSpeedLimitMps?: number;
    gradeRatio?: number;
    segmentId?: number | null;
  }>;
  speedTimeHistory: Array<{ elapsedS: number; speedMps: number }>;
  stationIndex: number;

  // 多线路列车在地图上的位置
  trainPositions: Record<string, { lat: number; lng: number }>;

  // 当前区间进度 0~1
  segmentProgress: number;

  // 动作
  toggleRunning: () => void;
  setSpeed: (speed: number) => void;
  setDayType: (dayType: 'weekday' | 'friday' | 'saturday' | 'sunday') => void;
  selectTrain: (id: string | null) => void;
  setSelectedStationCode: (code: string | null) => void;
  tick: () => void;

  // 后端仿真引擎
  engineClockState: string;
  updateFromBackend: (data: SimStateResponse) => void;
  startBackendSim: () => Promise<void>;
  pauseBackendSim: () => Promise<void>;
  resumeBackendSim: () => Promise<void>;
  stopBackendSim: () => Promise<void>;

  // 线路管理
  setMetroLines: (lines: MetroLineData[]) => void;
  setLinesLoading: (loading: boolean) => void;
  setLinesError: (error: string | null) => void;
  setBackendStatus: (status: 'idle' | 'connected' | 'fallback' | 'error') => void;
  setTrackMap: (trackMap: TrackMapData | null) => void;
  setPowerTopology: (powerTopology: PowerTopology | null) => void;
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
let cachedStationDistances: number[] | null = null; // 各站累计里程 (m)，来自 line 数据

/** 从线路数据构建站间距缓存 */
function buildDistanceCache(line9: MetroLineData) {
  const mileages = line9.stations.map((s) => s.mileageM ?? 0);
  // 如果有有效里程数据（非零且递增），直接使用
  const hasMileage = mileages.length > 1 && mileages[0] < mileages[mileages.length - 1];
  if (hasMileage) {
    cachedStationDistances = mileages;
    return;
  }
  // 否则用 polyline 估算：每个站点在 polyline 上的累计距离
  if (!cachedPolyline || !cachedStationPolyIdx) return;
  const dists: number[] = [0];
  for (let i = 1; i < cachedStationPolyIdx.length; i++) {
    let d = 0;
    const from = cachedStationPolyIdx[i - 1];
    const to = cachedStationPolyIdx[i];
    for (let j = from + 1; j <= to; j++) {
      const dx = cachedPolyline[j][0] - cachedPolyline[j - 1][0];
      const dy = cachedPolyline[j][1] - cachedPolyline[j - 1][1];
      d += Math.sqrt(dx * dx + dy * dy) * 111000; // 度→米近似
    }
    dists.push(dists[i - 1] + d);
  }
  cachedStationDistances = dists;
}

function getSegmentDist(idx: number): number {
  if (!cachedStationDistances || idx < 0 || idx + 1 >= cachedStationDistances.length) return 1347;
  return Math.round(cachedStationDistances[idx + 1] - cachedStationDistances[idx]);
}

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
  powerTopology: null as PowerTopology | null,
  punctuality: 98.5,
  avgWaitTime: 145,
  avgLoadRate: 68,
  totalPassengers: 0,
  totalBoarded: 0,
  totalWaitingPax: 0,
  maxPlatformDensity: 0,
  totalTractionEnergyKwh: 0,
  minTractionLimitRatio: 1,
  minTrainVoltageV: 750,
  totalAbsorbedRegenKw: 0,
  totalWastedRegenKw: 0,
  powerLossesKw: 0,
  lastDispatchAction: 'FOLLOW_TIMETABLE',
  simStations: [],
  simPower: [],
  simPowerNetwork: null,
  dispatchDecisions: [],
  selectedTrainId: null,
  selectedStationCode: null,

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
  tractionPercent: 0,
  brakePercent: 0,
  energyKwh: 0,
  estimatedRunTimeS: 0,
  pathPositionM: 0,
  pathTotalLengthM: 0,
  currentSegmentId: null,
  localSpeedLimitMps: 22.22,
  gradeRatio: 0,
  pathSegmentCount: 0,
  pathConstraintCount: 0,
  runDirection: 'DOWN',
  speedProfile: [],
  speedProfileMeta: null,
  speedHistory: [],
  speedTimeHistory: [],
  stationIndex: 0,
  trainPositions: {},
  segmentProgress: 0,
  engineClockState: 'IDLE',

  toggleRunning: () => {
    const state = get();
    if (state.backendStatus === 'connected') {
      // 后端模式: 不直接切换, 由 App 层的 useEffect 处理
      return;
    }
    // 前端独立模式
    const next = !state.isRunning;
    if (!next) { tickCount = 0; simSecAccum = 7 * 3600; currentRunDirection = 'DOWN'; }
    set({ isRunning: next, trainPositions: {} });
  },
  setSpeed: (speed: number) => set({ speed }),
  setDayType: (dayType) => set({ dayType }),
  selectTrain: (id: string | null) => set({ selectedTrainId: id }),
  setSelectedStationCode: (code: string | null) => set({ selectedStationCode: code }),

  setMetroLines: (lines) => {
    const line9 = lines.find((l) => l.id === '9');
    if (line9) { buildPolylineCache(line9); buildDistanceCache(line9); }
    set({ metroLines: lines, linesLoading: false, line9Stations: deriveStations9(line9) });
  },
  setLinesLoading: (loading) => set({ linesLoading: loading }),
  setLinesError: (error) => set({ linesError: error, linesLoading: false }),
  setBackendStatus: (status) => set({ backendStatus: status }),
  setTrackMap: (trackMap) => set({ trackMap }),
  setPowerTopology: (powerTopology) => set({ powerTopology }),
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

    // 后端模式下不跑本地 tick
    if (state.backendStatus === 'connected') return;

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

    // 根据真实站间距计算（优先级：mileageM > polyline 估算 > 默认 1347）
    const segDists: number[] = [];
    const totalSegments = stations.length - 1;
    let totalDist = 0;
    for (let i = 0; i < totalSegments; i++) {
      const d = getSegmentDist(i);
      segDists.push(d);
      totalDist += d;
    }
    // 平均速度 ~40km/h = 11.1 m/s（含停站），单程时间与总距离成正比
    const avgSpeedMps = 11.1;
    const routeTime = Math.round(totalDist / avgSpeedMps);

    // ═══ 上下行方向自动循环 ═══
    const elapsedInCycle = simSecAccum % (routeTime * 2);
    currentRunDirection = elapsedInCycle < routeTime ? 'DOWN' : 'UP';

    // 当前半程内的位置：按距离累积找到当前区段
    const phaseTime = elapsedInCycle % routeTime;
    const simTravelDist = phaseTime * avgSpeedMps; // 当前已行驶距离
    let curSegment = 0;
    let accumDist = 0;
    for (let i = 0; i < totalSegments; i++) {
      if (accumDist + segDists[i] > simTravelDist) {
        curSegment = i;
        break;
      }
      accumDist += segDists[i];
      curSegment = i;
    }
    const segDist = segDists[Math.min(curSegment, totalSegments - 1)] || 1347;
    const offsetInSegment = Math.max(0, simTravelDist - accumDist);

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
    const newPositions: Record<string, { lat: number; lng: number }> = {};
    const line9 = state.metroLines.find((l) => l.id === '9');
    if (line9 && cachedPolyline) {
      // 如缓存未建则重建
      if (!cachedStationPolyIdx || cachedStationPolyIdx.length !== line9.stations.length) {
        buildPolylineCache(line9);
      }
      const pos = interpolateOnPolyline(curStationIdx, nextIdx, segProgress);
      if (pos) {
        newPositions['9'] = { lat: pos[0], lng: pos[1] };
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
      trainPositions: newPositions,

      // KPI
      punctuality: Math.round(kpiPunct * 10) / 10,
      avgWaitTime: kpiWait,
      avgLoadRate: 60 + Math.floor(Math.random() * 30),
      totalPassengers: state.totalPassengers + Math.floor(Math.random() * 100),
      totalBoarded: state.totalBoarded + Math.floor(Math.random() * 60),
    });
  },

  // ═══════════════════════════════════════════════════
  //  后端仿真引擎
  // ═══════════════════════════════════════════════════

  updateFromBackend: (data: SimStateResponse) => {
    const { clock, trains, kpi, stations, power, powerNetwork, dispatchDecisions } = data;
    const t0 = trains[0];

    const state = get();
    const isEngineRunning = clock.state === 'RUNNING';
    set({
      engineClockState: clock.state,
      isRunning: isEngineRunning,
      simStations: stations ?? [],
      simPower: power ?? [],
      simPowerNetwork: powerNetwork ?? null,
      dispatchDecisions: dispatchDecisions ?? [],
      totalWaitingPax: kpi.totalWaitingPax ?? 0,
      maxPlatformDensity: kpi.maxPlatformDensity ?? 0,
      totalTractionEnergyKwh: kpi.totalTractionEnergyKwh ?? 0,
      minTractionLimitRatio: kpi.minTractionLimitRatio ?? 1,
      minTrainVoltageV: kpi.minTrainVoltageV ?? 750,
      totalAbsorbedRegenKw: kpi.totalAbsorbedRegenKw ?? 0,
      totalWastedRegenKw: kpi.totalWastedRegenKw ?? 0,
      powerLossesKw: kpi.powerLossesKw ?? 0,
      lastDispatchAction: kpi.lastDispatchAction ?? 'FOLLOW_TIMETABLE',
    });

    if (!t0) return;

    if (!isEngineRunning && clock.state !== 'PAUSED') return;

    // 仿真时间
    set({ simTime: clock.simTime });

    // 信号屏字段
    // 站台变化时重置速度曲线
    const currentPathLengthM = t0.pathTotalLengthM ?? t0.targetDistanceM ?? 0;
    const intervalChanged = (
      t0.currentStation !== state.currentStation
      || t0.nextStation !== state.nextStation
      || Math.abs(currentPathLengthM - state.pathTotalLengthM) > 0.5
    );
    const baseSpeedHistory = intervalChanged ? [] : state.speedHistory;
    const baseSpeedTimeHistory = intervalChanged ? [] : state.speedTimeHistory;
    if (intervalChanged) {
      set({ speedHistory: [], speedProfile: [], speedProfileMeta: null, speedTimeHistory: [] });
    }
    set({
      currentStation: t0.currentStation,
      nextStation: t0.nextStation,
      currentSpeedMps: t0.speedMps,
      permittedSpeedMps: t0.permittedSpeedMps,
      distanceToNextStationM: Math.round(t0.distanceToNextM),
      targetDistanceM: Math.round(t0.targetDistanceM),
      runDirection: t0.direction,
      stationIndex: t0.stationIndex,
      segmentProgress: t0.segmentProgress,
      driveMode: t0.phase === 'DWELLING' ? 'CM' : 'AM',
      tractionPercent: t0.tractionPercent ?? 0,
      brakePercent: t0.brakePercent ?? 0,
      energyKwh: t0.energyKwh ?? 0,
      estimatedRunTimeS: t0.estimatedRunTimeS ?? 0,
      targetSpeedMps: t0.targetSpeedMps ?? 22.22,
      pathPositionM: t0.pathPositionM ?? 0,
      pathTotalLengthM: t0.pathTotalLengthM ?? 0,
      currentSegmentId: t0.currentSegmentId ?? null,
      localSpeedLimitMps: t0.localSpeedLimitMps ?? t0.permittedSpeedMps,
      gradeRatio: t0.gradeRatio ?? 0,
      pathSegmentCount: t0.pathSegmentCount ?? 0,
      pathConstraintCount: t0.pathConstraintCount ?? 0,
    });

    // 列车地图位置 — polyline 插值 (支持多线路)
    const newPositions: Record<string, { lat: number; lng: number }> = { ...state.trainPositions };
    const line9 = state.metroLines.find((l) => l.id === '9');
    if (line9 && cachedPolyline) {
      if (!cachedStationPolyIdx || cachedStationPolyIdx.length !== line9.stations.length) {
        buildPolylineCache(line9);
      }
      const nextIdx = t0.direction === 'UP'
        ? Math.min(t0.stationIndex + 1, state.line9Stations.length - 1)
        : Math.max(t0.stationIndex - 1, 0);
      const pos = interpolateOnPolyline(t0.stationIndex, nextIdx, t0.segmentProgress);
      if (pos) {
        newPositions['9'] = { lat: pos[0], lng: pos[1] };
      }
    }
    set({ trainPositions: newPositions });

    // KPI
    set({
      avgLoadRate: Math.round(t0.loadFactor * 100),
      totalPassengers: t0.onboardPax,
      totalBoarded: kpi.totalOnboardPax,
      avgWaitTime: kpi.totalWaitingPax ?? 0,
      totalWaitingPax: kpi.totalWaitingPax ?? 0,
      maxPlatformDensity: kpi.maxPlatformDensity ?? 0,
      totalTractionEnergyKwh: kpi.totalTractionEnergyKwh ?? 0,
      minTractionLimitRatio: kpi.minTractionLimitRatio ?? 1,
      minTrainVoltageV: kpi.minTrainVoltageV ?? 750,
      totalAbsorbedRegenKw: kpi.totalAbsorbedRegenKw ?? 0,
      totalWastedRegenKw: kpi.totalWastedRegenKw ?? 0,
      powerLossesKw: kpi.powerLossesKw ?? 0,
      lastDispatchAction: t0.lastDispatchAction ?? kpi.lastDispatchAction ?? 'FOLLOW_TIMETABLE',
    });

    // 速度历史曲线 — 记录 (位置, 速度) 对
    const hasPathPosition = typeof t0.pathPositionM === 'number' && (t0.pathTotalLengthM ?? 0) > 0;
    if (hasPathPosition) {
      const newHistory = [
        ...baseSpeedHistory,
        {
          positionM: t0.pathPositionM ?? 0,
          speedMps: t0.speedMps,
          targetSpeedMps: t0.targetSpeedMps ?? 0,
          localSpeedLimitMps: t0.localSpeedLimitMps,
          gradeRatio: t0.gradeRatio,
          segmentId: t0.currentSegmentId,
        },
      ];
      if (newHistory.length > 500) newHistory.splice(0, newHistory.length - 500);
      set({ speedHistory: newHistory });
    } else {
      const tm = state.trackMap;
      if (tm?.stations?.length) {
      const stIdx = t0.stationIndex;
      const nextIdx = t0.direction === 'UP' ? stIdx + 1 : stIdx - 1;
      if (nextIdx >= 0 && nextIdx < tm.stations.length) {
        const curM = tm.stations[stIdx]?.mileageM ?? 0;
        const nextM = tm.stations[nextIdx]?.mileageM ?? curM + 1347;
        const positionM = curM + t0.segmentProgress * Math.abs(nextM - curM);
        const newHistory = [
          ...baseSpeedHistory,
          {
            positionM,
            speedMps: t0.speedMps,
            targetSpeedMps: t0.targetSpeedMps ?? 0,
            localSpeedLimitMps: t0.localSpeedLimitMps,
            gradeRatio: t0.gradeRatio,
            segmentId: t0.currentSegmentId,
          },
        ];
        if (newHistory.length > 500) newHistory.splice(0, newHistory.length - 500);
        set({ speedHistory: newHistory });
      }
      }
    }

    // 速度-时间曲线 — 记录 (发车后秒数, 速度) 对
    if (t0.phase !== 'DWELLING' && t0.phase !== 'IDLE') {
      const elapsedS = baseSpeedTimeHistory.length > 0
        ? baseSpeedTimeHistory[baseSpeedTimeHistory.length - 1].elapsedS + 0.25
        : 0.25;
      const newTimeHistory = [...baseSpeedTimeHistory, { elapsedS, speedMps: t0.speedMps }];
      if (newTimeHistory.length > 500) newTimeHistory.splice(0, newTimeHistory.length - 500);
      set({ speedTimeHistory: newTimeHistory });
    }
  },

  startBackendSim: async () => {
    await simStart();
    set({ isRunning: true, engineClockState: 'RUNNING' });
  },

  pauseBackendSim: async () => {
    await simPause();
    set({ engineClockState: 'PAUSED' });
  },

  resumeBackendSim: async () => {
    await simResume();
    set({ isRunning: true, engineClockState: 'RUNNING' });
  },

  stopBackendSim: async () => {
    await simStop();
    set({
      isRunning: false,
      engineClockState: 'STOPPED',
      trainPositions: {},
      speedProfile: [],
      speedProfileMeta: null,
      speedHistory: [],
      speedTimeHistory: [],
    });
  },
}));
