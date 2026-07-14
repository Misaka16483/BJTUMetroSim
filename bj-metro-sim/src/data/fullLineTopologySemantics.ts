import { listInterlockingStations } from './stationInterlockingData';

export type MainlineLane = 'up' | 'down';

export interface SegmentSemantic {
  lane: MainlineLane;
  /** Physical left-to-right order used by the full-line diagram. */
  order: number;
}

export interface FullLineStationAnchor {
  code: string;
  name: string;
  segmentIds: readonly number[];
  /** Platform Segs are the visual anchor for the station name and guide line. */
  platformSegmentIds: readonly number[];
}

/** Interval Segs in physical station order: 郭公庄 -> 国家图书馆. */
export const FULL_LINE_UP_INTERVAL_SEGMENTS: readonly (readonly number[])[] = [
  [22, 23, 235], [25, 54], [56, 57], [59, 60, 61],
  [63, 64, 66, 67, 82, 83], [85, 86, 87],
  [89, 90, 92, 93, 94, 96, 125], [127, 128],
  [130, 131, 133, 134, 169], [171, 172, 173],
  [178, 203], [205, 206],
];

const DOWN_INTERVALS_IN_RUNNING_DIRECTION: readonly (readonly number[])[] = [
  [219, 218], [195, 194, 192, 191, 190, 188, 187, 186],
  [184, 183, 182, 181], [179, 146, 145, 144, 142, 141, 140],
  [138, 137], [135, 116, 115, 113, 112, 111, 109, 108, 106, 105, 104],
  [102, 101, 100, 99], [97, 81, 79, 78], [76, 75, 74, 73],
  [71, 70], [68, 52], [50, 49, 48, 46, 45, 43, 41, 40],
];

/** Down-line Segs matched to the same physical station pairs as the UP list. */
export const FULL_LINE_DOWN_INTERVAL_SEGMENTS = [...DOWN_INTERVALS_IN_RUNNING_DIRECTION]
  .reverse()
  .map((segments) => [...segments].reverse());

/**
 * Semantic backbone shared by the full-line and topology views. A segment is
 * mainline because it is explicitly assigned to an up/down station track or
 * interval. Turnout status is intentionally supplied by live switch data:
 * a frog segment can at the same time be part of a mainline.
 */
export function getFullLineMainlineSemantics(): Map<number, SegmentSemantic> {
  const result = new Map<number, SegmentSemantic>();
  const stations = listInterlockingStations();

  // A horizontal slot belongs to a *station block*, not to one direction's
  // independent count of Segs.  This is what keeps the two platform tracks
  // of FSP, GTG, etc. vertically aligned even when their interval Seg counts
  // differ.  The engine direction codes are deliberately not inferred here.
  const stationSegments = new Set(stations.flatMap((station) => station.tracks.flatMap((track) => track.segmentIds)));
  let blockStart = 0;
  stations.forEach((station, index) => {
    const trackSegments = (lane: MainlineLane) => station.tracks
      .filter((track) => track.id.startsWith(lane === 'up' ? 'up-' : 'dn-'))
      .flatMap((track) => track.segmentIds);
    const upStation = trackSegments('up');
    const downStation = trackSegments('down');
    const upInterval = (FULL_LINE_UP_INTERVAL_SEGMENTS[index] ?? []).filter((id) => !stationSegments.has(id));
    const downInterval = (FULL_LINE_DOWN_INTERVAL_SEGMENTS[index] ?? []).filter((id) => !stationSegments.has(id));
    const stationWidth = Math.max(3, upStation.length, downStation.length);
    const put = (lane: MainlineLane, segmentIds: readonly number[], start: number) => {
      segmentIds.forEach((segmentId, localIndex) => result.set(segmentId, { lane, order: start + localIndex }));
    };

    put('up', upStation, blockStart);
    put('down', downStation, blockStart);
    put('up', upInterval, blockStart + stationWidth);
    put('down', downInterval, blockStart + stationWidth);
    blockStart += stationWidth + Math.max(upInterval.length, downInterval.length) + 2;
  });

  return result;
}

/** Station membership used to annotate the two physical backbones. */
export function getFullLineStationAnchors(): FullLineStationAnchor[] {
  return listInterlockingStations().map((station) => ({
    code: station.stationCode,
    name: station.stationName,
    segmentIds: station.tracks
      .filter((track) => track.id.startsWith('up-') || track.id.startsWith('dn-'))
      .flatMap((track) => track.segmentIds),
    platformSegmentIds: station.tracks
      .filter((track) => track.id.endsWith('-plat'))
      .flatMap((track) => track.segmentIds),
  }));
}
