import { create } from 'zustand';
import type { MetroLineData } from '../data/amapMetroApi';
import type {
  PowerNetworkState,
  PowerTopology,
  DispatchRuntimeState,
  OperationPlanState,
  SimDispatchDecision,
  SimPowerState,
  SimStateResponse,
  SimStationInfo,
  SimTrainState,
  TrackMapData,
  SpeedProfilePoint,
  SpeedProfileMeta,
  VehicleConfigPayload,
  VehicleConfigResponse,
  AddTrainPayload,
  DriverCabHardwareStatus,
} from '../data/backendApi';
import { simStart, simPause, simResume, simStop, simSetSpeedMultiplier, simSetVehicleConfig, simSendManualCommand, simSendDoorCommand, simAddTrain, simRemoveTrain, simSetTrainManualMode, fetchDriverCabStatus } from '../data/backendApi';

type ViewMode = 'macro' | 'micro' | 'interlocking' | 'fullLine' | 'driver' | 'power' | 'stationFlow' | 'memberCDemo' | 'topologyLayout';
export type DataMode = 'LIVE_SIM' | 'REPLAY' | 'DEMO' | 'DISCONNECTED';
export type SnapshotUpdateResult = 'accepted' | 'stale' | 'gap';

/**
 * 从 Amap 9号线数据中提取站名列表（去"站"后缀）。
 *
 * 后端 stationIndex 的权威顺序是郭公庄 -> 国家图书馆，而高德返回的
 * 9 号线站序通常恰好相反。驾驶页会用 stationIndex 索引这个数组，
 * 因此必须先统一到后端顺序。
 */
export function deriveStations9(line9: MetroLineData | undefined): string[] {
  if (!line9) return [];
  const stations = line9.stations.map((s) => s.name.replace(/站$/, ''));
  const guogongzhuangIndex = stations.indexOf('郭公庄');
  const nationalLibraryIndex = stations.indexOf('国家图书馆');
  return guogongzhuangIndex > nationalLibraryIndex ? stations.reverse() : stations;
}

interface SimState {
  // 仿真状态
  isRunning: boolean;
  speed: number;
  simTime: string;
  simTimeMs: number;
  dayType: 'weekday' | 'friday' | 'saturday' | 'sunday';

  // 地铁线路数据
  metroLines: MetroLineData[];
  linesLoading: boolean;
  linesError: string | null;
  backendStatus: 'idle' | 'connected' | 'fallback' | 'error';
  dataMode: DataMode;
  dataStale: boolean;
  sessionId: string | null;
  runId: number | null;
  snapshotSequence: number;
  snapshotGapCount: number;
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
  dispatchRuntime: DispatchRuntimeState | null;
  operationPlan: OperationPlanState | null;

  // 所有列车状态
  trains: SimTrainState[];

  // 列车颜色 (trainId -> hex)
  trainColors: Record<string, string>;

  // 每车速度历史缓存
  speedHistoryByTrain: Record<string, SpeedHistoryEntry[]>;
  speedTimeHistoryByTrain: Record<string, SpeedTimeEntry[]>;

  // 每车按站间运行分段的曲线档案（切车后仍持续采集）
  speedRunsByTrain: Record<string, SpeedRunRecord[]>;
  activeSpeedRunIdByTrain: Record<string, string>;
  viewedSpeedRunIdByTrain: Record<string, string | null>;

  // 选中的列车ID
  selectedTrainId: string | null;

  // 选中的车站代码 (用于轨道级→联锁图跳转)
  selectedStationCode: string | null;

  // 信号屏 / MMI 字段 (由选中列车驱动)
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
  tractionEnergyKwh: number;
  regenGeneratedKwh: number;
  regenAcceptedKwh: number;
  regenWastedKwh: number;
  estimatedRunTimeS: number;
  pathPositionM: number;
  pathTotalLengthM: number;
  currentSegmentId: number | null;
  currentSegmentOffsetM: number;
  localSpeedLimitMps: number;
  gradeRatio: number;
  pathSegmentCount: number;
  pathConstraintCount: number;
  runDirection: 'UP' | 'DOWN';
  // ── 速度曲线 ──
  speedProfile: SpeedProfilePoint[];
  speedProfileMeta: SpeedProfileMeta | null;
  speedHistory: SpeedHistoryEntry[];
  speedTimeHistory: SpeedTimeEntry[];
  stationIndex: number;

  // 多车在地图上的位置 (trainId → 坐标)
  trainPositions: Record<string, { lat: number; lng: number }>;

  // 当前区间进度 0~1
  segmentProgress: number;

  // 动作
  toggleRunning: () => void;
  setSpeed: (speed: number) => void;
  setDayType: (dayType: 'weekday' | 'friday' | 'saturday' | 'sunday') => void;
  selectTrain: (id: string | null) => void;
  selectSpeedRun: (trainId: string, runId: string | null) => void;
  applySpeedProfiles: (
    profiles: Record<string, SpeedProfilePoint[]>,
    profileMeta?: Record<string, SpeedProfileMeta>,
    expectedActiveRunIds?: Record<string, string>,
  ) => void;
  setSelectedStationCode: (code: string | null) => void;
  setSelectedTrainId: (id: string | null) => void;
  tick: () => void;

  // 后端仿真引擎
  engineClockState: string;
  updateFromBackend: (data: SimStateResponse, transport?: 'REST' | 'WS') => SnapshotUpdateResult;
  startBackendSim: () => Promise<void>;
  pauseBackendSim: () => Promise<void>;
  resumeBackendSim: () => Promise<void>;
  stopBackendSim: () => Promise<void>;

  // 车辆参数配置
  vehicleConfig: VehicleConfigPayload;
  vehicleConfigResponse: VehicleConfigResponse | null;
  showVehicleConfig: boolean;
  setVehicleConfig: (config: Partial<VehicleConfigPayload>) => void;
  submitVehicleConfig: () => Promise<void>;
  setShowVehicleConfig: (show: boolean) => void;

  // 列车管理
  addTrain: (payload: AddTrainPayload) => Promise<boolean>;
  removeTrain: (trainId: string) => Promise<boolean>;
  setTrainColor: (trainId: string, color: string) => void;

  // 手动驾驶
  manualMode: boolean;
  manualTrainIds: Set<string>;
  manualTraction: number;
  manualBrake: number;
  setManualMode: (enabled: boolean, trainId?: string) => Promise<void>;
  sendManualCommand: (traction: number, brake: number) => void;
  sendDoorCommand: (action: 'OPEN' | 'CLOSE', side?: 'LEFT' | 'RIGHT' | 'NONE') => Promise<boolean>;
  _lastManualSend: number;

  // 司机台硬件状态 (PLC)
  cabStatus: DriverCabHardwareStatus | null;
  fetchCabStatus: () => Promise<void>;

  // 线路管理
  setMetroLines: (lines: MetroLineData[]) => void;
  setLinesLoading: (loading: boolean) => void;
  setLinesError: (error: string | null) => void;
  setBackendStatus: (status: 'idle' | 'connected' | 'fallback' | 'error') => void;
  setDataMode: (mode: DataMode) => void;
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
let currentRunDirection: 'UP' | 'DOWN' = 'UP';
let backendStartPromise: Promise<void> | null = null;
let awaitingRunConfirmation = false;

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

type SpeedHistoryEntry = {
  positionM: number;
  speedMps: number;
  targetSpeedMps?: number;
  localSpeedLimitMps?: number;
  gradeRatio?: number;
  segmentId?: number | null;
};

type SpeedTimeEntry = { elapsedS: number; speedMps: number };

export type SpeedRunRecord = {
  id: string;
  intervalKey: string;
  trainId: string;
  direction: 'UP' | 'DOWN';
  startStation: string;
  endStation: string;
  startedAtSimTime: string;
  startedAtSimTimeMs: number;
  endedAtSimTime?: string;
  completed: boolean;
  pathTotalLengthM: number;
  positionHistory: SpeedHistoryEntry[];
  timeHistory: SpeedTimeEntry[];
  profile: SpeedProfilePoint[];
  profileMeta: SpeedProfileMeta | null;
  lastSampleTick: number;
};

const DEF_TRAIN_COLORS = [
  '#8FC31F', '#58a6ff', '#f59e0b', '#22c55e', '#ef4444',
  '#c084fc', '#00a8ff', '#fbbf24', '#ec4899', '#14b8a6',
  '#8b5cf6', '#f97316',
];

function _nextTrainColor(used: Set<string>): string {
  for (const c of DEF_TRAIN_COLORS) {
    if (!used.has(c)) return c;
  }
  return DEF_TRAIN_COLORS[used.size % DEF_TRAIN_COLORS.length];
}

function _intervalKey(t: SimTrainState): string {
  return `${t.direction}:${t.currentStationCode || t.currentStation}>${t.nextStationCode || t.nextStation}`;
}

function _activeRun(
  runs: Record<string, SpeedRunRecord[]>,
  activeIds: Record<string, string>,
  trainId: string,
): SpeedRunRecord | undefined {
  const id = activeIds[trainId];
  return id ? runs[trainId]?.find((run) => run.id === id) : undefined;
}
function _applyTrainDetail(t: SimTrainState, state?: ReturnType<typeof useSimStore.getState>, forceReset = false) {
  const manualMode = t.operationMode === 'MANUAL';
  const base = {
    currentStation: t.currentStation,
    nextStation: t.nextStation,
    currentSpeedMps: t.speedMps,
    permittedSpeedMps: t.permittedSpeedMps,
    distanceToNextStationM: Math.round(t.distanceToNextM),
    targetDistanceM: Math.round(t.targetDistanceM),
    runDirection: t.direction,
    stationIndex: t.stationIndex,
    segmentProgress: t.segmentProgress,
    driveMode: t.phase === 'DWELLING' ? 'CM' : manualMode ? 'RM' : 'AM',
    manualMode,
    tractionPercent: t.tractionPercent ?? 0,
    brakePercent: t.brakePercent ?? 0,
    energyKwh: t.energyKwh ?? 0,
    tractionEnergyKwh: t.tractionEnergyKwh ?? 0,
    regenGeneratedKwh: t.regenGeneratedKwh ?? 0,
    regenAcceptedKwh: t.regenAcceptedKwh ?? 0,
    regenWastedKwh: t.regenWastedKwh ?? 0,
    estimatedRunTimeS: t.estimatedRunTimeS ?? 0,
    targetSpeedMps: t.targetSpeedMps ?? 22.22,
    pathPositionM: t.pathPositionM ?? 0,
    pathTotalLengthM: t.pathTotalLengthM ?? 0,
    currentSegmentId: t.currentSegmentId ?? null,
    currentSegmentOffsetM: t.currentSegmentOffsetM ?? 0,
    localSpeedLimitMps: t.localSpeedLimitMps ?? t.permittedSpeedMps,
    gradeRatio: t.gradeRatio ?? 0,
    pathSegmentCount: t.pathSegmentCount ?? 0,
    pathConstraintCount: t.pathConstraintCount ?? 0,
    avgLoadRate: Math.round(t.loadFactor * 100),
    totalPassengers: t.onboardPax,
    lastDispatchAction: t.lastDispatchAction,
  };
  if (!state) return base;

  let intervalChanged = forceReset;
  if (!forceReset) {
    const currentPathLengthM = t.pathTotalLengthM ?? t.targetDistanceM ?? 0;
    intervalChanged = (
      t.currentStation !== state.currentStation
      || t.nextStation !== state.nextStation
      || Math.abs(currentPathLengthM - state.pathTotalLengthM) > 0.5
    );
  }
  return {
    ...base,
    speedHistory: intervalChanged ? [] : state.speedHistory,
    speedTimeHistory: intervalChanged ? [] : state.speedTimeHistory,
    speedProfile: intervalChanged ? [] : state.speedProfile,
    speedProfileMeta: intervalChanged ? null : state.speedProfileMeta,
  };
}

export const useSimStore = create<SimState>((set, get) => ({
  isRunning: false,
  speed: 1,
  simTime: '06:00:00',
  simTimeMs: 21_600_000,
  dayType: 'weekday',
  metroLines: [],
  linesLoading: false,
  linesError: null,
  backendStatus: 'idle',
  dataMode: 'DISCONNECTED',
  dataStale: false,
  sessionId: null,
  runId: null,
  snapshotSequence: 0,
  snapshotGapCount: 0,
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
  dispatchRuntime: null,
  operationPlan: null,
  trains: [],
  trainColors: {},
  speedHistoryByTrain: {},
  speedTimeHistoryByTrain: {},
  speedRunsByTrain: {},
  activeSpeedRunIdByTrain: {},
  viewedSpeedRunIdByTrain: {},
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
  tractionEnergyKwh: 0,
  regenGeneratedKwh: 0,
  regenAcceptedKwh: 0,
  regenWastedKwh: 0,
  estimatedRunTimeS: 0,
  pathPositionM: 0,
  pathTotalLengthM: 0,
  currentSegmentId: null,
  currentSegmentOffsetM: 0,
  localSpeedLimitMps: 22.22,
  gradeRatio: 0,
  pathSegmentCount: 0,
  pathConstraintCount: 0,
  runDirection: 'UP',
  speedProfile: [],
  speedProfileMeta: null,
  speedHistory: [],
  speedTimeHistory: [],
  stationIndex: 0,
  trainPositions: {},
  segmentProgress: 0,
  engineClockState: 'IDLE',

  vehicleConfig: {
    formation: 'Tc-M-M-M-M-Tc',
    carMassesKg: [34500, 39000, 39000, 39000, 39000, 34500],
    headCarLengthM: 20.2,
    middleCarLengthM: 19.4,
    wheelRadiusM: 0.46,
    maxSpeedMps: 22.22,
    maxTractionForceN: 300000,
    maxServiceBrakeForceN: 300000,
    emergencyBrakeForceN: 337500,
  },
  vehicleConfigResponse: null,
  showVehicleConfig: false,

  manualMode: false,
  manualTrainIds: new Set<string>(),
  manualTraction: 0,
  manualBrake: 0,
  _lastManualSend: 0,

  cabStatus: null as DriverCabHardwareStatus | null,
  fetchCabStatus: async () => {
    try {
      const response = await fetchDriverCabStatus();
      if (response.ok) set({ cabStatus: response.status });
    } catch { /* 静默失败, 硬件未连接时保持 null */ }
  },

  toggleRunning: () => {
    const state = get();
    if (state.dataMode !== 'DEMO') {
      // 后端模式: 不直接切换, 由 App 层的 useEffect 处理
      return;
    }
    // 前端独立模式
    const next = !state.isRunning;
    if (!next) { tickCount = 0; simSecAccum = 7 * 3600; currentRunDirection = 'UP'; }
    set({ isRunning: next, trainPositions: {} });
  },
  setSpeed: (speed: number) => {
    const multiplier = Math.max(1, Math.min(240, Math.floor(speed)));
    set({ speed: multiplier });
    void simSetSpeedMultiplier(multiplier).catch(() => {
      // Keep the UI usable when the backend is unavailable; the next backend
      // snapshot will restore the authoritative multiplier.
    });
  },
  setDayType: (dayType) => set({ dayType }),
  selectTrain: (id: string | null) => {
    const state = get();
    if (!id) {
      set({ selectedTrainId: null });
      return;
    }
    const train = state.trains.find((t) => t.trainId === id);
    if (!train) return;
    const sel = _applyTrainDetail(train);
    const activeRun = _activeRun(state.speedRunsByTrain, state.activeSpeedRunIdByTrain, id);
    set({
      selectedTrainId: id,
      ...sel,
      speedHistory: activeRun?.positionHistory ?? [],
      speedTimeHistory: activeRun?.timeHistory ?? [],
      speedProfile: activeRun?.profile ?? [],
      speedProfileMeta: activeRun?.profileMeta ?? null,
    });
  },
  selectSpeedRun: (trainId, runId) => set((state) => ({
    viewedSpeedRunIdByTrain: { ...state.viewedSpeedRunIdByTrain, [trainId]: runId },
  })),
  applySpeedProfiles: (profiles, profileMeta = {}, expectedActiveRunIds) => {
    const state = get();
    const nextRuns = { ...state.speedRunsByTrain };
    let selectedProfile: SpeedProfilePoint[] | undefined;
    let selectedMeta: SpeedProfileMeta | null | undefined;

    for (const [trainId, points] of Object.entries(profiles)) {
      const nextMeta = profileMeta[trainId] ?? null;
      if (points.length === 0 && nextMeta === null) continue;
      const activeId = state.activeSpeedRunIdByTrain[trainId];
      // 请求发出后若车辆已经到下一站，丢弃迟到的旧区间响应。
      if (expectedActiveRunIds && expectedActiveRunIds[trainId] !== activeId) continue;
      const trainRuns = nextRuns[trainId];
      const runIndex = activeId && trainRuns ? trainRuns.findIndex((run) => run.id === activeId) : -1;
      if (runIndex < 0 || !trainRuns) continue;

      const run = trainRuns[runIndex];
      const profileEndM = points[points.length - 1]?.positionM;
      if (
        profileEndM !== undefined
        && run.pathTotalLengthM > 0
        && Math.abs(profileEndM - run.pathTotalLengthM) > 2
      ) continue;

      const updatedRun = { ...run, profile: points, profileMeta: nextMeta };
      const updatedTrainRuns = [...trainRuns];
      updatedTrainRuns[runIndex] = updatedRun;
      nextRuns[trainId] = updatedTrainRuns;
      if (trainId === state.selectedTrainId) {
        selectedProfile = points;
        selectedMeta = updatedRun.profileMeta;
      }
    }

    set({
      speedRunsByTrain: nextRuns,
      ...(selectedProfile ? { speedProfile: selectedProfile, speedProfileMeta: selectedMeta ?? null } : {}),
    });
  },
  setSelectedStationCode: (code: string | null) => set({ selectedStationCode: code }),
  setSelectedTrainId: (id: string | null) => set({ selectedTrainId: id }),

  setMetroLines: (lines) => {
    const line9 = lines.find((l) => l.id === '9');
    if (line9) { buildPolylineCache(line9); buildDistanceCache(line9); }
    set({ metroLines: lines, linesLoading: false, line9Stations: deriveStations9(line9) });
  },
  setLinesLoading: (loading) => set({ linesLoading: loading }),
  setLinesError: (error) => set({ linesError: error, linesLoading: false }),
  setBackendStatus: (status) => set((state) => ({
    backendStatus: status,
    ...(status === 'connected' ? {} : {
      dataMode: 'DISCONNECTED' as DataMode,
      dataStale: state.snapshotSequence > 0,
      isRunning: false,
    }),
  })),
  setDataMode: (mode) => set((state) => {
    if (mode === 'DEMO' && import.meta.env.VITE_ENABLE_DEMO_MODE !== 'true') return state;
    return {
      dataMode: mode,
      dataStale: mode === 'DISCONNECTED' && state.snapshotSequence > 0,
      isRunning: mode === 'DEMO' ? state.isRunning : mode === 'LIVE_SIM' ? state.isRunning : false,
    };
  }),
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

    // 本地推进只能由显式 DEMO 模式启用；断线状态必须冻结最后快照。
    if (state.dataMode !== 'DEMO') return;

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
    currentRunDirection = elapsedInCycle < routeTime ? 'UP' : 'DOWN';

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

    if (currentRunDirection === 'UP') {
      // 上行: 郭公庄(0) → 国家图书馆(N-1)
      curStationIdx = Math.min(curSegment, totalSegments - 1);
      nextIdx = Math.min(curSegment + 1, totalSegments);
    } else {
      // 下行: 国家图书馆(N-1) → 郭公庄(0)
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
    const kpiWave = (Math.sin(tickCount / 30) + 1) / 2;
    const kpiWait = 100 + Math.floor(kpiWave * 80);
    const kpiPunct = 96 + kpiWave * 3;

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
      endStation: currentRunDirection === 'UP' ? stations[stations.length - 1] : stations[0],
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
      avgLoadRate: 60 + Math.floor(kpiWave * 30),
      totalPassengers: state.totalPassengers + Math.floor(kpiWave * 100),
      totalBoarded: state.totalBoarded + Math.floor(kpiWave * 60),
    });
  },

  // ═══════════════════════════════════════════════════
  //  后端仿真引擎
  // ═══════════════════════════════════════════════════

  updateFromBackend: (data: SimStateResponse, transport = 'REST') => {
    const { clock, trains, kpi, stations, power, powerNetwork, dispatchDecisions, dispatchRuntime, operations } = data;
    const state = get();
    const sameStream = state.sessionId === data.sessionId && state.runId === data.runId;
    if (sameStream && data.snapshotSequence <= state.snapshotSequence) return 'stale';
    const hasGap = sameStream
      && state.snapshotSequence > 0
      && data.snapshotSequence > state.snapshotSequence + 1;
    if (hasGap && transport === 'WS') {
      set({ snapshotGapCount: state.snapshotGapCount + 1 });
      return 'gap';
    }
    const streamChanged = state.sessionId !== null && !sameStream;
    // A GET started before POST /start may arrive later with a stale LOADED or
    // STOPPED snapshot. Do not let it overwrite the acknowledged start state.
    if (awaitingRunConfirmation && clock.state !== 'RUNNING') return 'stale';
    if (clock.state === 'RUNNING') awaitingRunConfirmation = false;
    const isEngineRunning = clock.state === 'RUNNING';

    // 如果有车但没有选中，自动选第一辆
    let selId = state.selectedTrainId;
    if (selId && !trains.find((t) => t.trainId === selId)) selId = null;
    if (!selId && trains.length > 0) selId = trains[0].trainId;

    const selTrain = selId ? trains.find((t) => t.trainId === selId) : null;

    set({
      backendStatus: 'connected',
      dataMode: data.dataMode,
      dataStale: false,
      sessionId: data.sessionId,
      runId: data.runId,
      snapshotSequence: data.snapshotSequence,
      snapshotGapCount: hasGap ? state.snapshotGapCount + 1 : state.snapshotGapCount,
      engineClockState: clock.state,
      isRunning: isEngineRunning,
      speed: clock.speedMultiplier ?? state.speed,
      simTime: clock.simTime,
      simTimeMs: clock.simTimeMs,
      trains: trains,
      selectedTrainId: selId,
      simStations: stations ?? [],
      simPower: power ?? [],
      simPowerNetwork: powerNetwork ?? null,
      dispatchDecisions: dispatchDecisions ?? [],
      dispatchRuntime: dispatchRuntime ?? null,
      operationPlan: operations ?? null,
      totalWaitingPax: kpi.totalWaitingPax ?? 0,
      maxPlatformDensity: kpi.maxPlatformDensity ?? 0,
      totalTractionEnergyKwh: kpi.totalTractionEnergyKwh ?? 0,
      minTractionLimitRatio: kpi.minTractionLimitRatio ?? 1,
      minTrainVoltageV: kpi.minTrainVoltageV ?? 750,
      totalAbsorbedRegenKw: kpi.totalAbsorbedRegenKw ?? 0,
      totalWastedRegenKw: kpi.totalWastedRegenKw ?? 0,
      powerLossesKw: kpi.powerLossesKw ?? 0,
      lastDispatchAction: kpi.lastDispatchAction ?? 'FOLLOW_TIMETABLE',
      ...(streamChanged ? {
        speedHistory: [],
        speedTimeHistory: [],
        speedProfile: [],
        speedProfileMeta: null,
        speedHistoryByTrain: {},
        speedTimeHistoryByTrain: {},
        speedRunsByTrain: {},
        activeSpeedRunIdByTrain: {},
        viewedSpeedRunIdByTrain: {},
      } : {}),
    });

    // 为新出现的列车自动分配颜色
    const curColors = get().trainColors;
    const usedColors = new Set(Object.values(curColors));
    const newColors = { ...curColors };
    let changed = false;
    for (const t of trains) {
      if (!newColors[t.trainId]) {
        newColors[t.trainId] = _nextTrainColor(usedColors);
        usedColors.add(newColors[t.trainId]);
        changed = true;
      }
    }
    if (changed) set({ trainColors: newColors });

    if (selTrain) {
      // LOADED snapshots are already authoritative for a newly placed train.
      // Applying details only after RUNNING left SEG, offset and CM mode stale
      // on the first frame of both the driver and topology workflows.
      const sel = _applyTrainDetail(selTrain, get());
      set({ ...sel });
    }

    // 全部列车地图位置
    const line9 = state.metroLines.find((l) => l.id === '9');
    const newPositions: Record<string, { lat: number; lng: number }> = {};
    if (line9 && cachedPolyline) {
      if (!cachedStationPolyIdx || cachedStationPolyIdx.length !== line9.stations.length) {
        buildPolylineCache(line9);
      }
      // 宏观地图数据的站序可能与后端 stationIndex 相反。
      // 优先用后端返回的站名定位地图站点，避免“郭公庄发车却显示在国家图书馆”。
      const normalizeStationName = (name?: string) => (name ?? '').replace(/站$/, '');
      const mapStationIndexByName = new Map(
        line9.stations.map((station, index) => [normalizeStationName(station.name), index]),
      );
      const guogongzhuangMapIndex = mapStationIndexByName.get('郭公庄');
      const nationalLibraryMapIndex = mapStationIndexByName.get('国家图书馆');
      const mapUsesBackendOrder = guogongzhuangMapIndex !== undefined
        && nationalLibraryMapIndex !== undefined
        && guogongzhuangMapIndex < nationalLibraryMapIndex;
      const backendToMapIndex = (backendIndex: number) => {
        const clamped = Math.max(0, Math.min(backendIndex, line9.stations.length - 1));
        return mapUsesBackendOrder ? clamped : line9.stations.length - 1 - clamped;
      };
      for (const t of trains) {
        const fallbackNextBackendIndex = t.direction === 'UP'
          ? Math.min(t.stationIndex + 1, (line9.stations.length || 1) - 1)
          : Math.max(t.stationIndex - 1, 0);
        const currentMapIndex = mapStationIndexByName.get(normalizeStationName(t.currentStation))
          ?? backendToMapIndex(t.stationIndex);
        const nextMapIndex = mapStationIndexByName.get(normalizeStationName(t.nextStation))
          ?? backendToMapIndex(fallbackNextBackendIndex);
        const pos = interpolateOnPolyline(currentMapIndex, nextMapIndex, t.segmentProgress);
        if (pos) {
          newPositions[t.trainId] = { lat: pos[0], lng: pos[1] };
        }
      }
    }
    set({ trainPositions: newPositions });

    // KPI
    set({
      totalBoarded: kpi.totalOnboardPax,
      avgWaitTime: kpi.totalWaitingPax ?? 0,
    });

    // 速度曲线档案 — 每次轮询采集全部列车，并按站间运行分段。
    // 这样切换车辆不会中断采样，也不会把上一站末点与下一站首点连起来。
    if (data.dataMode === 'LIVE_SIM' && (isEngineRunning || clock.state === 'PAUSED')) {
      const curState = get();
      const nextRuns = { ...curState.speedRunsByTrain };
      const nextActiveIds = { ...curState.activeSpeedRunIdByTrain };
      const nextPositionCache = { ...curState.speedHistoryByTrain };
      const nextTimeCache = { ...curState.speedTimeHistoryByTrain };

      for (const train of trains) {
        const pathTotalLengthM = train.pathTotalLengthM ?? train.targetDistanceM ?? 0;
        if (pathTotalLengthM <= 0) continue;

        const intervalKey = _intervalKey(train);
        const trainRuns = [...(nextRuns[train.trainId] ?? [])];
        const activeId = nextActiveIds[train.trainId];
        let activeIndex = activeId ? trainRuns.findIndex((run) => run.id === activeId) : -1;
        let run = activeIndex >= 0 ? trainRuns[activeIndex] : undefined;

        if (!run || run.intervalKey !== intervalKey) {
          if (run && !run.completed) {
            trainRuns[activeIndex] = { ...run, completed: true, endedAtSimTime: clock.simTime };
          }
          const runId = `${train.trainId}:${intervalKey}:${clock.tick}`;
          run = {
            id: runId,
            intervalKey,
            trainId: train.trainId,
            direction: train.direction,
            startStation: train.currentStation,
            endStation: train.nextStation,
            startedAtSimTime: clock.simTime,
            startedAtSimTimeMs: clock.simTimeMs,
            completed: false,
            pathTotalLengthM,
            positionHistory: [],
            timeHistory: [],
            profile: [],
            profileMeta: null,
            lastSampleTick: -1,
          };
          trainRuns.push(run);
          activeIndex = trainRuns.length - 1;
          nextActiveIds[train.trainId] = runId;
        }

        // 同一个后端 tick 可能被 200ms 轮询读到多次，只记录一次。
        if (run.lastSampleTick !== clock.tick) {
          const positionHistory = typeof train.pathPositionM === 'number'
            ? [...run.positionHistory, {
                positionM: train.pathPositionM,
                speedMps: train.speedMps,
                targetSpeedMps: train.targetSpeedMps ?? 0,
                localSpeedLimitMps: train.localSpeedLimitMps,
                gradeRatio: train.gradeRatio,
                segmentId: train.currentSegmentId,
              }]
            : run.positionHistory;
          const shouldRecordTime = train.phase !== 'DWELLING' && train.phase !== 'IDLE';
          const elapsedS = Math.max(0, (clock.simTimeMs - run.startedAtSimTimeMs) / 1000);
          const timeHistory = shouldRecordTime
            ? [...run.timeHistory, { elapsedS, speedMps: train.speedMps }]
            : run.timeHistory;
          run = { ...run, pathTotalLengthM, positionHistory, timeHistory, lastSampleTick: clock.tick };
          trainRuns[activeIndex] = run;
        }

        nextRuns[train.trainId] = trainRuns;
        nextPositionCache[train.trainId] = run.positionHistory;
        nextTimeCache[train.trainId] = run.timeHistory;
      }

      const selectedRun = selId
        ? _activeRun(nextRuns, nextActiveIds, selId)
        : undefined;
      set({
        speedRunsByTrain: nextRuns,
        activeSpeedRunIdByTrain: nextActiveIds,
        speedHistoryByTrain: nextPositionCache,
        speedTimeHistoryByTrain: nextTimeCache,
        speedHistory: selectedRun?.positionHistory ?? [],
        speedTimeHistory: selectedRun?.timeHistory ?? [],
        speedProfile: selectedRun?.profile ?? [],
        speedProfileMeta: selectedRun?.profileMeta ?? null,
      });
    }
    return 'accepted';
  },

  startBackendSim: async () => {
    if (get().engineClockState === 'RUNNING') return;
    if (backendStartPromise) return backendStartPromise;
    backendStartPromise = (async () => {
      awaitingRunConfirmation = true;
      try {
        await simStart();
        set({ isRunning: true, engineClockState: 'RUNNING' });
      } catch (error) {
        awaitingRunConfirmation = false;
        throw error;
      }
    })();
    try {
      await backendStartPromise;
    } finally {
      backendStartPromise = null;
    }
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
    awaitingRunConfirmation = false;
    await simStop();
    set({
      isRunning: false,
      engineClockState: 'STOPPED',
      trainPositions: {},
      speedProfile: [],
      speedProfileMeta: null,
      speedHistory: [],
      speedTimeHistory: [],
      speedHistoryByTrain: {},
      speedTimeHistoryByTrain: {},
      speedRunsByTrain: {},
      activeSpeedRunIdByTrain: {},
      viewedSpeedRunIdByTrain: {},
      simStations: [],
      simPower: [],
      simPowerNetwork: null,
      dispatchDecisions: [],
      dispatchRuntime: null,
      operationPlan: null,
      totalWaitingPax: 0,
      maxPlatformDensity: 0,
      totalTractionEnergyKwh: 0,
      minTractionLimitRatio: 1,
      minTrainVoltageV: 750,
      totalAbsorbedRegenKw: 0,
      totalWastedRegenKw: 0,
      powerLossesKw: 0,
      totalBoarded: 0,
      avgWaitTime: 0,
      currentSpeedMps: 0,
      tractionPercent: 0,
      brakePercent: 0,
      energyKwh: 0,
      pathPositionM: 0,
      segmentProgress: 0,
    });
  },

  setVehicleConfig: (partial) =>
    set((s) => ({ vehicleConfig: { ...s.vehicleConfig, ...partial } })),

  submitVehicleConfig: async () => {
    const cfg = get().vehicleConfig;
    const resp = await simSetVehicleConfig(cfg);
    set({ vehicleConfigResponse: resp, showVehicleConfig: false });
  },

  setShowVehicleConfig: (show) => set({ showVehicleConfig: show }),

  addTrain: async (payload: AddTrainPayload) => {
    const resp = await simAddTrain(payload);
    if (resp.ok) {
      const state = get();
      const usedColors = new Set(Object.values(state.trainColors));
      const color = payload.color || _nextTrainColor(usedColors);
      set({ trainColors: { ...state.trainColors, [payload.trainId]: color } });
      if (!state.selectedTrainId) {
        set({ selectedTrainId: payload.trainId });
      }
    }
    return !!resp.ok;
  },

  removeTrain: async (trainId: string) => {
    const resp = await simRemoveTrain(trainId);
    if (resp.ok) {
      const state = get();
      const nextColors = { ...state.trainColors };
      delete nextColors[trainId];
      const nextSpdHist = { ...state.speedHistoryByTrain };
      delete nextSpdHist[trainId];
      const nextTimeHist = { ...state.speedTimeHistoryByTrain };
      delete nextTimeHist[trainId];
      const nextRuns = { ...state.speedRunsByTrain };
      delete nextRuns[trainId];
      const nextActiveIds = { ...state.activeSpeedRunIdByTrain };
      delete nextActiveIds[trainId];
      const nextViewedIds = { ...state.viewedSpeedRunIdByTrain };
      delete nextViewedIds[trainId];
      const historyState = {
        trainColors: nextColors,
        speedHistoryByTrain: nextSpdHist,
        speedTimeHistoryByTrain: nextTimeHist,
        speedRunsByTrain: nextRuns,
        activeSpeedRunIdByTrain: nextActiveIds,
        viewedSpeedRunIdByTrain: nextViewedIds,
      };
      if (state.selectedTrainId === trainId) {
        const remaining = state.trains.filter((t) => t.trainId !== trainId);
        set({ selectedTrainId: remaining[0]?.trainId ?? null, ...historyState });
      } else {
        set(historyState);
      }
    }
    return !!resp.ok;
  },

  setTrainColor: (trainId: string, color: string) => {
    set((s) => ({ trainColors: { ...s.trainColors, [trainId]: color } }));
  },

  setManualMode: async (enabled: boolean, trainId?: string) => {
    const id = trainId ?? get().selectedTrainId;
    if (!id) return;
    const cabStatus = get().cabStatus;
    if (cabStatus?.state === 'CONNECTED' && cabStatus.trainId === id) return;
    const response = await simSetTrainManualMode(id, enabled);
    if (!response.ok) {
      await get().fetchCabStatus();
      return;
    }
    const next = new Set(get().manualTrainIds);
    if (enabled) next.add(id);
    else next.delete(id);
    set({ manualMode: enabled, manualTrainIds: next, manualTraction: 0, manualBrake: 0 });
  },

  sendDoorCommand: async (action, side = 'NONE') => {
    const id = get().selectedTrainId;
    if (!id) return false;
    try {
      const response = await simSendDoorCommand(id, action, side);
      if (response.ok && (response.train || response.doorSystem)) {
        set((state) => {
          const current = state.trains.find((train) => train.trainId === id);
          const updated = response.train ?? (
            current && response.doorSystem
              ? {
                  ...current,
                  doorSystem: response.doorSystem,
                  doorState: response.doorSystem.aggregateState,
                  doorSide: response.doorSystem.activeSide !== 'NONE'
                    ? response.doorSystem.activeSide
                    : response.doorSystem.permittedSide,
                  doorTransitionRemainingSec: response.doorSystem.transitionRemainingSec,
                }
              : null
          );
          if (!updated) return {};
          const trains = state.trains.map((train) => (
            train.trainId === id ? updated : train
          ));
          const selected = updated.trainId === state.selectedTrainId
            ? _applyTrainDetail(updated, state)
            : {};
          return { trains, ...selected };
        });
      }
      return response.ok;
    } catch {
      return false;
    }
  },

  sendManualCommand: (traction, brake) => {
    const now = Date.now();
    const s = get();
    if (now - s._lastManualSend < 100) return;
    const id = s.selectedTrainId;
    if (!id) return;
    set({ _lastManualSend: now, manualTraction: traction, manualBrake: brake });
    simSendManualCommand(traction, brake, id).catch(() => {});
  },
}));
