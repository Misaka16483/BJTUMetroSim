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

async function postJson(url: string, payload?: unknown): Promise<unknown> {
  const response = await fetch(url, {
    method: 'POST',
    headers: payload === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: payload === undefined ? undefined : JSON.stringify(payload),
  });
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
  doorState?: string;
  doorSide?: string;
  doorNotice?: string;
  doorPermission?: string;
  doorTransitionRemainingSec?: number;
  lastBoarding?: number;
  lastAlighting?: number;
  currentBoardingPax?: number;
  currentAlightingPax?: number;
  currentBoardingRatePaxPerSec?: number;
  currentAlightingRatePaxPerSec?: number;
  lastPassengerEventMs?: number | null;
  currentStation: string;
  nextStation: string;
  segmentProgress: number;
  lastDispatchAction: string;
  lastDispatchReason: string;
  tractionPercent?: number;
  brakePercent?: number;
  energyKwh?: number;
  tractionEnergyKwh?: number;
  auxiliaryEnergyKwh?: number;
  regenGeneratedKwh?: number;
  regenSelfConsumedKwh?: number;
  regenAcceptedKwh?: number;
  regenWastedKwh?: number;
  tractionPowerRequestKw?: number;
  tractionPowerDeliveredKw?: number;
  auxiliaryPowerKw?: number;
  regenPowerAvailableKw?: number;
  regenPowerSelfConsumedKw?: number;
  regenPowerAcceptedKw?: number;
  regenPowerWastedKw?: number;
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
  trainLengthM: number;
  headMileageM: number;
  tailMileageM: number;
  pantographMileagesM: number[];
  spannedPowerSectionIds: string[];
  departureAuthorized?: boolean;
  interlockingHoldReason?: string | null;
  activeRouteIds?: string[];
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
  generatedRegenKw?: number;
  selfConsumedRegenKw?: number;
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
  rectifierPowerKw: number;
  feedbackPowerKw: number;
  sourceId: string;
  quality: string;
  parameterSources: Record<string, string>;
}

export interface PowerTopologySupercapacitorStorage {
  storageId: string;
  substationId: string;
  ratedEnergyKwh: number;
  maxChargePowerKw: number;
  maxDischargePowerKw: number;
  dischargeTriggerPowerKw: number;
  initialSoc: number;
  minSoc: number;
  maxSoc: number;
  chargeEfficiency: number;
  dischargeEfficiency: number;
  standbyPowerKw: number;
  status: string;
  sourceId: string;
  quality: string;
  parameterSources: Record<string, string>;
}

export interface SupercapacitorStorageState {
  storageId: string;
  substationId: string;
  soc: number;
  storedEnergyKwh: number;
  availableChargeEnergyKwh: number;
  availableDischargeEnergyKwh: number;
  chargePowerKw: number;
  dischargePowerKw: number;
  conversionLossesKw: number;
  cumulativeChargedKwh: number;
  cumulativeDischargedKwh: number;
  state: 'CHARGING' | 'DISCHARGING' | 'STANDBY' | 'FULL' | 'EMPTY' | 'OUT_OF_SERVICE';
  status: string;
}

export interface PowerTopology {
  lineId: string;
  nominalVoltageV: number;
  quality: string;
  modelVersion: string;
  provenance: {
    sources: Array<{
      sourceId: string;
      description: string;
      evidenceLevel: string;
    }>;
    parameterDocument: string;
    limitations: string[];
  };
  substations: PowerTopologySubstation[];
  supercapacitorStorageSystems?: PowerTopologySupercapacitorStorage[];
  feeders: PowerTopologyFeeder[];
  contactRailSections: PowerTopologyContactRailSection[];
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

export interface ContactRailPowerFlowState {
  sectionId: string;
  direction: string;
  currentA: number;
  powerKw: number;
  loadRatio: number;
  status: string;
  rectifierPowerKw?: number;
  feedbackPowerKw?: number;
}

export interface PowerTopologyFeeder {
  feederId: string;
  substationId: string;
  direction: string;
  side: string;
  status: string;
  sourceId: string;
  quality: string;
  parameterSources: Record<string, string>;
}

export interface PowerTopologyContactRailSection {
  sectionId: string;
  direction: string;
  fromMileageM: number;
  toMileageM: number;
  resistanceOhmPerKm: number;
  currentLimitA: number;
  status: string;
  sourceId: string;
  quality: string;
  parameterSources: Record<string, string>;
}

export interface TrainVoltageState {
  trainId: string;
  powerSectionId: string;
  mileageM?: number;
  voltageV: number;
  currentA: number;
  requestedPowerKw: number;
  tractionPowerRequestKw: number;
  tractionPowerDeliveredKw: number;
  auxiliaryPowerKw: number;
  regenPowerAvailableKw: number;
  regenPowerSelfConsumedKw: number;
  regenPowerExportedKw: number;
  regenPowerAcceptedKw: number;
  regenPowerWastedKw: number;
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
  contactRailFlows?: ContactRailPowerFlowState[];
  supercapacitorStorageSystems?: SupercapacitorStorageState[];
  trainVoltages: TrainVoltageState[];
  regen: {
    generatedKw: number;
    selfConsumedKw: number;
    absorbedKw: number;
    feedbackKw: number;
    storageChargedKw: number;
    storageDischargedKw: number;
    wastedKw: number;
    transferLossesKw: number;
    paths: Array<{
      sourceTrainId: string;
      sinkType: 'TRAIN' | 'TRAIN_AUXILIARY' | 'SUPERCAPACITOR' | 'SUBSTATION_FEEDBACK' | 'WASTE';
      sinkId: string;
      viaSubstationId: string | null;
      sourceFeederId: string | null;
      sinkFeederId: string | null;
      generatedKw: number;
      deliveredKw: number;
      lossesKw: number;
      currentA: number;
      pathResistanceOhm: number;
    }>;
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
  solverFailure?: {
    type: 'POWER_SOLVER_FAILURE';
    reasons: string[];
    simTimeMs: number;
    iterations: number;
    powerBalanceErrorRatio: number;
  } | null;
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
  passengerDemandScale?: number;
  passengerUsePoisson?: boolean;
  totalPassengerArrivedPax?: number;
  totalPassengerBoardedPax?: number;
  totalPassengerAlightedPax?: number;
  passengerServiceRatio?: number;
  passengerPlatformBalanced?: boolean;
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
  speedMultiplier?: number;
  tickIntervalMs?: number;
}

export interface InterlockingRuntimeState {
  mode: string;
  routeCount: number;
  occupiedSectionCount: number;
  lockedRouteCount: number;
  reservedIntervalCount: number;
  routes: Array<{
    routeId: string;
    name: string;
    startSignalId: number;
    endSignalId: number;
    axleSectionIds: string[];
    state: string;
    trainId?: string | null;
    failureReason?: string | null;
  }>;
  sections: Array<{
    sectionId: string;
    sectionType: string;
    occupied: boolean;
    trainIds: string[];
    segmentIds: number[];
  }>;
  switches: Array<Record<string, unknown>>;
  signals: Array<{ signalId: string; aspect: string; faulted: boolean }>;
  departureAuthorities: Array<{
    trainId: string;
    granted: boolean;
    authorityMode: string;
    routeIds: string[];
    signalAspects: Record<string, string>;
    failureReason?: string | null;
  }>;
}

export interface DispatchRuntimeState {
  registeredTrainCount: number;
  departureCount: number;
  recentDepartures: Array<{
    trainId: string;
    stationIndex: number;
    stationId: string;
    direction: string;
    simTimeS: number;
  }>;
}

export interface SimStateResponse {
  clock: SimClock;
  trains: SimTrainState[];
  stations: SimStationInfo[];
  power?: SimPowerState[];
  powerNetwork?: PowerNetworkState;
  dispatchDecisions?: SimDispatchDecision[];
  dispatchRuntime?: DispatchRuntimeState;
  interlocking?: InterlockingRuntimeState;
  passengerFlow?: PassengerFlowConfiguration;
  passengerExchanges?: CurrentPassengerExchange[];
  kpi: SimKpi;
  source: string;
}

export interface PassengerHistoryPoint {
  simTimeMs: number;
  waitingPax: number;
  arrivals: number;
  leftBehindPax: number;
  platformDensity: number;
}

export interface StationPassengerHistoryResponse {
  stationCode: string;
  source: string;
  history: Record<'UP' | 'DOWN', PassengerHistoryPoint[]>;
}

export interface PassengerFlowConfiguration {
  enabled: boolean;
  usePoisson: boolean;
  mode: 'POISSON_STOCHASTIC' | 'DISABLED_MANUAL';
  manualInputAllowed: boolean;
  demandScale: number;
  boardingPolicy: 'FILL_TO_CAPACITY';
  tickSeconds: number;
}

export interface CurrentPassengerExchange {
  trainId: string;
  stationCode: string;
  stationName: string;
  direction: 'UP' | 'DOWN';
  doorState: string;
  doorNotice: string;
  active: boolean;
  currentBoardingPax: number;
  currentAlightingPax: number;
  boardingRatePaxPerSec: number;
  alightingRatePaxPerSec: number;
  platformWaitingPax: number;
  onboardPax: number;
  capacityPax: number;
  loadFactor: number;
  dwellRemainingSec: number;
}

export interface PassengerExchangeResponse {
  simTimeMs: number;
  stationCode?: string | null;
  passengerFlow: PassengerFlowConfiguration;
  exchanges: CurrentPassengerExchange[];
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

export function fetchStationPassengerHistory(stationCode: string, sinceSimTimeMs?: number): Promise<StationPassengerHistoryResponse> {
  const suffix = sinceSimTimeMs === undefined ? '' : `?sinceSimTimeMs=${sinceSimTimeMs}`;
  return getJson<StationPassengerHistoryResponse>(`/api/sim/passenger-history/${encodeURIComponent(stationCode)}${suffix}`);
}

export function fetchCurrentPassengerExchange(stationCode?: string): Promise<PassengerExchangeResponse> {
  const suffix = stationCode === undefined ? '' : `?stationCode=${encodeURIComponent(stationCode)}`;
  return getJson<PassengerExchangeResponse>(`/api/sim/passenger-exchange${suffix}`);
}

export function fetchPassengerFlowMode(): Promise<{ ok: boolean; passengerFlow: PassengerFlowConfiguration }> {
  return getJson<{ ok: boolean; passengerFlow: PassengerFlowConfiguration }>('/api/sim/passenger-flow-mode');
}

export function setPassengerFlowMode(usePoisson: boolean): Promise<{ ok: boolean; passengerFlow: PassengerFlowConfiguration }> {
  return postJson('/api/sim/passenger-flow-mode', { enabled: usePoisson }) as Promise<{
    ok: boolean;
    passengerFlow: PassengerFlowConfiguration;
  }>;
}

export interface AddPlatformPassengersResponse {
  ok: boolean;
  status: 'QUEUED' | 'APPLIED';
  stationCode: string;
  direction: 'UP' | 'DOWN';
  passengers: number;
  waitingPax: number;
  projectedWaitingPax: number;
}

export function addPlatformPassengers(
  stationCode: string,
  direction: 'UP' | 'DOWN',
  passengers: number,
): Promise<AddPlatformPassengersResponse> {
  return postJson('/api/sim/passengers/add', { stationCode, direction, passengers }) as Promise<AddPlatformPassengersResponse>;
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

export function simSetTickInterval(intervalMs: number): Promise<{ ok: boolean; tickIntervalMs: number }> {
  return fetch('/api/sim/tick-interval', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ intervalMs }),
  }).then((resp) => {
    if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
    return resp.json() as Promise<{ ok: boolean; tickIntervalMs: number }>;
  });
}

export function simSetSpeedMultiplier(multiplier: number): Promise<unknown> {
  return postJson('/api/sim/speed', { multiplier });
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
  pantographOffsetsFromHeadM?: number[];
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
    pantographOffsetsFromHeadM: number[];
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

export interface ManualModeResponse {
  ok: boolean;
  manualMode?: boolean;
  error?: string;
  message?: string;
  trainId?: string;
}

export function simSetManualMode(enabled: boolean, trainId?: string): Promise<ManualModeResponse> {
  return fetch('/api/sim/manual-mode', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled, trainId }),
  }).then((resp) => {
    if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
    return resp.json() as Promise<ManualModeResponse>;
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

export function simSetTrainManualMode(trainId: string, enabled: boolean): Promise<ManualModeResponse> {
  return fetch('/api/sim/train/manual-mode', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ trainId, enabled }),
  }).then((resp) => {
    if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
    return resp.json() as Promise<ManualModeResponse>;
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

export type DriverCabConnectionState = 'DISCONNECTED' | 'CONNECTING' | 'CONNECTED' | 'ERROR';
export type DisplayConnectionState = 'DISCONNECTED' | 'CONNECTING' | 'CONNECTED' | 'RETRYING';

export type HardwareLogEndpoint = 'system' | 'plc' | 'networkScreen' | 'signalScreen' | 'vision';
export type HardwareLogLevel = 'INFO' | 'WARN' | 'ERROR';

export interface HardwareConnectionLog {
  sequence: number;
  timestamp: string;
  endpoint: HardwareLogEndpoint;
  level: HardwareLogLevel;
  event: string;
  message: string;
  details: Record<string, unknown>;
}

export interface DriverCabDisplayStatus {
  state: DisplayConnectionState;
  host: string;
  port: number;
  framesSent: number;
  framesReceived: number;
  bytesReceived: number;
  connectedAt: string | null;
  lastFrameAt: string | null;
  lastReceivedAt: string | null;
  lastError: string | null;
}

export interface DriverCabHardwareStatus {
  state: DriverCabConnectionState;
  host: string;
  port: number;
  trainId: string;
  controlState: 'IDLE' | 'WAITING_FOR_CONNECTION' | 'WAITING_FOR_TRAIN' | 'ACTIVE' | 'ATO_ACTIVE' | 'FAIL_SAFE_BRAKE';
  framesReceived: number;
  connectedAt: string | null;
  lastFrameAt: string | null;
  lastError: string | null;
  lastInput: {
    speedMps: number;
    direction: string;
    handleCode: number;
    tractionPercent: number;
    brakePercent: number;
    emergencyBrake: boolean;
    keyActive: boolean;
    atoStart: boolean;
    atoAvailableEcho: boolean;
    atoActiveEcho: boolean;
  } | null;
  lastCommand: {
    tractionPercent: number;
    brakePercent: number;
    emergencyBrake: boolean;
    handleMode: string;
  } | null;
  plcOutput: {
    atoAvailable: boolean;
    atoActive: boolean;
    frameLength: number;
    speedCmps: number | null;
  };
  networkScreenHost: string;
  networkScreenPort: number;
  signalScreenHost: string;
  signalScreenPort: number;
  networkScreen: DriverCabDisplayStatus;
  signalScreen: DriverCabDisplayStatus;
  logs: HardwareConnectionLog[];
}

export interface DriverCabHardwareResponse {
  ok: boolean;
  status: DriverCabHardwareStatus;
  error?: string;
}

export function fetchDriverCabStatus(): Promise<DriverCabHardwareResponse> {
  return getJson<DriverCabHardwareResponse>('/api/hardware/driver-cab/status');
}

export function connectDriverCab(
  host?: string,
  port?: number,
  networkScreenHost?: string,
  signalScreenHost?: string,
): Promise<DriverCabHardwareResponse> {
  const body: Record<string, unknown> = {};
  if (host) body.host = host;
  if (port) body.port = port;
  if (networkScreenHost) body.networkScreenHost = networkScreenHost;
  if (signalScreenHost) body.signalScreenHost = signalScreenHost;
  return fetch('/api/hardware/driver-cab/connect', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then((response) => {
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
    return response.json() as Promise<DriverCabHardwareResponse>;
  });
}

export function disconnectDriverCab(): Promise<DriverCabHardwareResponse> {
  return fetch('/api/hardware/driver-cab/disconnect', { method: 'POST' }).then((response) => {
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
    return response.json() as Promise<DriverCabHardwareResponse>;
  });
}

export type DriverCabEndpoint = 'plc' | 'network-screen' | 'signal-screen';

export function connectDriverCabEndpoint(
  endpoint: DriverCabEndpoint,
  host: string,
  port?: number,
): Promise<DriverCabHardwareResponse> {
  const body: Record<string, unknown> = { host };
  if (port !== undefined) body.port = port;
  return fetch(`/api/hardware/driver-cab/${endpoint}/connect`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then((response) => {
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
    return response.json() as Promise<DriverCabHardwareResponse>;
  });
}

export function disconnectDriverCabEndpoint(
  endpoint: DriverCabEndpoint,
): Promise<DriverCabHardwareResponse> {
  return fetch(`/api/hardware/driver-cab/${endpoint}/disconnect`, { method: 'POST' }).then((response) => {
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
    return response.json() as Promise<DriverCabHardwareResponse>;
  });
}

export type VisionConnectionState = 'DISCONNECTED' | 'STARTING' | 'CONNECTED' | 'RETRYING';
export type VisionFrameLayout = 'compact' | 'fixed';

export interface VisionHardwareStatus {
  state: VisionConnectionState;
  remoteHost: string;
  remotePort: number;
  localHost: string;
  localPort: number;
  intervalMs: number;
  layout: VisionFrameLayout;
  framesSent: number;
  bytesSent: number;
  lastFrameSize: number;
  lastFrameAt: string | null;
  lastError: string | null;
  nextLiveCounter: number;
  mapping: {
    protocolSignalCount: number;
    mappedSignalCount: number;
    protocolSwitchCount: number;
    mappedSwitchCount: number;
    unmappedSignalsDefault: string;
    unmappedSwitchesDefault: string;
  };
  logs: HardwareConnectionLog[];
}

export interface VisionHardwareResponse {
  ok: boolean;
  status: VisionHardwareStatus;
  error?: string;
}

export interface VisionConnectOptions {
  remoteHost: string;
  remotePort: number;
  localHost?: string;
  localPort: number;
  intervalMs?: number;
  layout: VisionFrameLayout;
  primaryTrainId?: string;
}

export function fetchVisionStatus(): Promise<VisionHardwareResponse> {
  return getJson<VisionHardwareResponse>('/api/hardware/vision/status');
}

export function connectVision(options: VisionConnectOptions): Promise<VisionHardwareResponse> {
  return fetch('/api/hardware/vision/connect', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(options),
  }).then((response) => {
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
    return response.json() as Promise<VisionHardwareResponse>;
  });
}

export function disconnectVision(): Promise<VisionHardwareResponse> {
  return fetch('/api/hardware/vision/disconnect', { method: 'POST' }).then((response) => {
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
    return response.json() as Promise<VisionHardwareResponse>;
  });
}

export interface ClearHardwareLogsResponse {
  ok: boolean;
  driverCab: DriverCabHardwareStatus;
  vision: VisionHardwareStatus;
  error?: string;
}

export function clearHardwareLogs(): Promise<ClearHardwareLogsResponse> {
  return fetch('/api/hardware/logs/clear', { method: 'POST' }).then((response) => {
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
    return response.json() as Promise<ClearHardwareLogsResponse>;
  });
}
