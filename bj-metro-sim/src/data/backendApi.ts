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

