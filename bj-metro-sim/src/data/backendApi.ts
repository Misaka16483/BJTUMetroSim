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

export async function fetchBackendBundle(): Promise<{
  line: MetroLineData;
  trackMap: TrackMapData;
}> {
  const [line, trackMap] = await Promise.all([
    fetchBackendLine9(),
    fetchBackendTrackMap(),
  ]);
  return { line, trackMap };
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
  source: string;
  quality: string;
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
  dispatchDecisions?: SimDispatchDecision[];
  kpi: SimKpi;
  source: string;
}

export function fetchSimState(): Promise<SimStateResponse> {
  return getJson<SimStateResponse>('/api/sim/state');
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

