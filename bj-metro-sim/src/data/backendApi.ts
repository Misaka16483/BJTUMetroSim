import type { MetroLineData } from './amapMetroApi';

export interface Line9Station {
  lineId: string;
  stationId: number;
  stationCode: string;
  stationName: string;
  mileageM: number;
  speedLimitToNextKmh: number;
  dwellSeconds: number;
  lat: number;
  lng: number;
  platformIds: number[];
  platformSegmentIds: number[];
  platforms: {
    id: number;
    segmentId: number;
    direction: string | null;
    mileageM: number;
  }[];
}

export interface TrackSegment {
  id: number;
  lengthM: number;
  startEndpointId: number;
  endEndpointId: number;
  nextSegmentIds: number[];
  ciAreaId: number;
  zcAreaId: number;
  stationName: string | null;
}

export interface TrackMapData {
  lineId: string;
  name: string;
  lengthM: number;
  counts: Record<string, number>;
  stations: Line9Station[];
  segments: TrackSegment[];
  platforms: {
    id: number;
    mileageM: number;
    segmentId: number;
    direction: string | null;
    clearPassengerFlag: string | null;
  }[];
  signals: {
    id: number;
    name: string;
    type: number;
    segmentId: number;
    offsetM: number;
    direction: string | null;
    aspectInfo: string | null;
  }[];
  speedRestrictions: {
    id: number;
    segmentId: number;
    startOffsetM: number;
    endOffsetM: number;
    speedLimitMps: number;
  }[];
  gradients: {
    id: number;
    startSegmentId: number;
    startOffsetM: number;
    endSegmentId: number;
    endOffsetM: number;
    slopePermille: number;
  }[];
}

async function getJson<T>(url: string): Promise<T> {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  return response.json() as Promise<T>;
}

async function postJson(url: string): Promise<unknown> {
  const response = await fetch(url, { method: 'POST' });
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  return response.json();
}

export function fetchBackendLine9(): Promise<MetroLineData> {
  return getJson<MetroLineData>('/api/lines/9/macro');
}

export function fetchBackendTrackMap(): Promise<TrackMapData> {
  return getJson<TrackMapData>('/api/lines/9/track-map');
}

export function fetchBackendPowerTopology(): Promise<PowerTopology> {
  return getJson<PowerTopology>('/api/lines/9/power-topology');
}

export async function fetchBackendBundle(): Promise<{
  line: MetroLineData;
  trackMap: TrackMapData;
  powerTopology: PowerTopology;
}> {
  const [line, trackMap, powerTopology] = await Promise.all([
    fetchBackendLine9(),
    fetchBackendTrackMap(),
    fetchBackendPowerTopology(),
  ]);
  return { line, trackMap, powerTopology };
}

// ═══════════════════════════════════════════════════════════
//  仿真引擎 API
// ═══════════════════════════════════════════════════════════

export interface SimTrainState {
  trainId: string;
  lineId: string;
  stationIndex: number;
  direction: 'UP' | 'DOWN';
  phase: string;
  currentStationCode: string;
  nextStationCode: string;
  speedMps: number;
  permittedSpeedMps: number;
  distanceToNextM: number;
  targetDistanceM: number;
  dwellRemainingSec: number;
  onboardPax: number;
  capacityPax: number;
  loadFactor: number;
  currentStation: string;
  nextStation: string;
  segmentProgress: number;
  lastDispatchAction: string;
  lastDispatchReason: string;
  tractionPercent?: number;
  brakePercent?: number;
  energyKwh?: number;
  targetSpeedMps?: number;
  estimatedRunTimeS?: number;
  pathPositionM?: number;
  pathTotalLengthM?: number;
  currentSegmentId?: number | null;
  localSpeedLimitMps?: number;
  gradeRatio?: number;
  pathSegmentCount?: number;
  pathConstraintCount?: number;
  operationMode?: string;
}

export interface SimStationInfo {
  name: string;
  code: string;
  waitingPax?: number;
  leftBehindPax?: number;
  arrivalsLastTick?: number;
  platformDensity?: number;
  crowdingLevel?: 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL' | string;
  direction?: string;
}

export interface SimPowerState {
  powerSectionId: string;
  requestedPowerKw: number;
  availablePowerKw: number;
  tractionLimitRatio: number;
  voltageLevel: string;
  energyKwh: number;
  regenEnergyKwh: number;
  absorbedRegenKw: number;
  wastedRegenKw: number;
  minTrainVoltageV?: number;
  maxTrainCurrentA?: number;
  substationCount?: number;
  overloadedSubstations?: number;
  overloadedFeeders?: number;
  lossesKw?: number;
  feedbackRegenKw?: number;
  alerts?: Array<Record<string, unknown>>;
  source: string;
  quality: string;
}

export interface PowerSubstationState {
  substationId: string;
  name: string;
  mileageM: number;
  voltageV: number;
  currentA: number;
  powerKw: number;
  energyKwh: number;
  loadRatio: number;
  status: string;
}

export interface PowerTopologySubstation {
  substationId: string;
  name: string;
  mileageM: number;
  noLoadVoltageV: number;
  internalResistanceOhm: number;
  ratedCurrentA: number;
  overloadCurrentA: number;
  efsCapacityKw: number;
  status: string;
}

export interface PowerTopology {
  lineId: string;
  nominalVoltageV: number;
  quality: string;
  substations: PowerTopologySubstation[];
  feeders: unknown[];
  contactRailSections: unknown[];
  returnRailSections?: unknown[];
  switches: unknown[];
}

export interface PowerFeederState {
  feederId: string;
  substationId: string;
  direction: string;
  side: string;
  currentA: number;
  powerKw: number;
  loadRatio: number;
  status: string;
}

export interface TrainVoltageState {
  trainId: string;
  powerSectionId: string;
  mileageM?: number;
  voltageV: number;
  currentA: number;
  requestedPowerKw: number;
  tractionLimitRatio: number;
  regenLimitRatio: number;
  voltageLevel: string;
  leftSubstationId?: string | null;
  rightSubstationId?: string | null;
}

export interface PowerNetworkState {
  simTimeMs?: number;
  substations: PowerSubstationState[];
  feeders: PowerFeederState[];
  trainVoltages: TrainVoltageState[];
  regen: {
    generatedKw: number;
    absorbedKw: number;
    feedbackKw: number;
    wastedKw: number;
  };
  lossesKw: number;
  solver?: {
    converged: boolean;
    iterations: number;
    solveTimeMs: number;
    powerBalanceErrorKw: number;
    powerBalanceErrorRatio: number;
  };
  switches?: Array<{
    switchId: string;
    switchType: string;
    mileageM: number;
    fromNodeId: string;
    toNodeId: string;
    normalState: string;
    currentState: string;
    remoteControllable: boolean;
  }>;
  commandResults?: Array<{
    commandId: string;
    commandType: string;
    simTimeMs: number;
    status: string;
    error?: string;
  }>;
  alerts: Array<Record<string, unknown>>;
  source?: string;
  quality?: string;
}

export interface SimDispatchDecision {
  decisionId: string;
  simTimeMs: number;
  trainId: string | null;
  stationId: string | null;
  action: string;
  durationSec: number;
  reason: string;
  applied: boolean;
  expectedImpact: Record<string, number | string | boolean>;
}

export interface SimKpi {
  activeTrains: number;
  totalTrains: number;
  avgSpeed: number;
  totalOnboardPax: number;
  totalWaitingPax?: number;
  maxPlatformDensity?: number;
  totalTractionEnergyKwh?: number;
  minTractionLimitRatio?: number;
  minTrainVoltageV?: number;
  totalAbsorbedRegenKw?: number;
  totalWastedRegenKw?: number;
  powerLossesKw?: number;
  lastDispatchAction?: string;
}

export interface SimClock {
  state: string;
  simTime: string;
  tick: number;
  simTimeMs: number;
}

export interface SimStateResponse {
  clock: SimClock;
  trains: SimTrainState[];
  stations: SimStationInfo[];
  power?: SimPowerState[];
  powerNetwork?: PowerNetworkState;
  dispatchDecisions?: SimDispatchDecision[];
  kpi: SimKpi;
  source: string;
}

// ── 速度规划曲线 ──
export interface SpeedProfilePoint {
  positionM: number;
  speedMps: number;
  mode: string;
  localSpeedLimitMps?: number;
  gradeRatio?: number;
  segmentId?: number | null;
}

export interface SpeedProfileResponse {
  profiles: Record<string, SpeedProfilePoint[]>;
  profileMeta?: Record<string, SpeedProfileMeta>;
  source: string;
}

export interface SpeedProfileMeta {
  source: string;
  terminalScore?: number | null;
  scheduledRunTimeS?: number;
  targetPositionM?: number;
  permittedSpeedMps?: number;
  pointCount?: number;
}

export function fetchSimState(): Promise<SimStateResponse> {
  return getJson<SimStateResponse>('/api/sim/state');
}

export function fetchSpeedProfile(): Promise<SpeedProfileResponse> {
  return getJson<SpeedProfileResponse>('/api/sim/speed-profile');
}

export function simStart(): Promise<unknown> {
  return postJson('/api/sim/start');
}

export function simPause(): Promise<unknown> {
  return postJson('/api/sim/pause');
}

export function simResume(): Promise<unknown> {
  return postJson('/api/sim/resume');
}

export function simStop(): Promise<unknown> {
  return postJson('/api/sim/stop');
}

export interface VehicleConfigPayload {
  formation: string;
  carMassesKg: number[];
  headCarLengthM: number;
  middleCarLengthM: number;
  wheelRadiusM: number;
  maxSpeedMps?: number;
  maxTractionForceN?: number;
  maxServiceBrakeForceN?: number;
  emergencyBrakeForceN?: number;
}

export interface VehicleConfigResponse {
  ok: boolean;
  vehicleConfig: {
    trainId: string;
    formation: string;
    carMassesKg: number[] | null;
    headCarLengthM: number;
    middleCarLengthM: number;
    wheelRadiusM: number;
    massKg: number;
    trainLengthM: number;
    maxSpeedMps: number;
    maxTractionForceN: number;
    maxServiceBrakeForceN: number;
    emergencyBrakeForceN: number;
  };
}

export function simSetVehicleConfig(payload: VehicleConfigPayload): Promise<VehicleConfigResponse> {
  return fetch('/api/sim/vehicle-config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }).then((resp) => {
    if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
    return resp.json() as Promise<VehicleConfigResponse>;
  });
}

export function simSetManualMode(enabled: boolean, trainId?: string): Promise<{ ok: boolean; manualMode: boolean }> {
  return fetch('/api/sim/manual-mode', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled, trainId }),
  }).then((resp) => {
    if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
    return resp.json() as Promise<{ ok: boolean; manualMode: boolean }>;
  });
}

export function simSendManualCommand(tractionPercent: number, brakePercent: number, trainId?: string): Promise<unknown> {
  return fetch('/api/sim/manual-command', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ tractionPercent, brakePercent, trainId }),
  }).then((resp) => {
    if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
    return resp.json();
  });
}

export interface AddTrainPayload {
  trainId: string;
  initialStationCode: string;
  direction: 'UP' | 'DOWN';
  operationMode?: 'ATO' | 'MANUAL';
  capacityPax?: number;
  initialLoadPax?: number;
  vehicleConfig?: VehicleConfigPayload;
  color?: string;
}

export interface AddTrainResponse {
  ok: boolean;
  train?: SimTrainState;
  error?: string;
}

export function simAddTrain(payload: AddTrainPayload): Promise<AddTrainResponse> {
  return fetch('/api/sim/train/add', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }).then((resp) => {
    if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
    return resp.json() as Promise<AddTrainResponse>;
  });
}

export function simRemoveTrain(trainId: string): Promise<{ ok: boolean; removed?: string; error?: string }> {
  return fetch('/api/sim/train/remove', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ trainId }),
  }).then((resp) => {
    if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
    return resp.json() as Promise<{ ok: boolean }>;
  });
}

export function simSetTrainVehicleConfig(trainId: string, payload: VehicleConfigPayload): Promise<VehicleConfigResponse> {
  return fetch('/api/sim/train/vehicle-config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ trainId, ...payload }),
  }).then((resp) => {
    if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
    return resp.json() as Promise<VehicleConfigResponse>;
  });
}

export function simSetTrainManualMode(trainId: string, enabled: boolean): Promise<{ ok: boolean; manualMode: boolean }> {
  return fetch('/api/sim/train/manual-mode', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ trainId, enabled }),
  }).then((resp) => {
    if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
    return resp.json() as Promise<{ ok: boolean; manualMode: boolean }>;
  });
}

export function simSendTrainManualCommand(trainId: string, tractionPercent: number, brakePercent: number): Promise<unknown> {
  return fetch('/api/sim/train/manual-command', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ trainId, tractionPercent, brakePercent }),
  }).then((resp) => {
    if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
    return resp.json();
  });
}
