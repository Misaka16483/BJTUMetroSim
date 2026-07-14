import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent, type WheelEvent as ReactWheelEvent } from 'react';
import { getFullLineMainlineSemantics, getFullLineStationAnchors, type MainlineLane } from '../data/fullLineTopologySemantics';

type ConnectionKind = 'forward' | 'diverging';

interface TopologySegment {
  id: number;
  lengthM: number;
  /** Original Member-C drawing slot. Kept only as a layout hint. */
  row: number;
  col: number;
  endForward: number | null;
  endDiverging: number | null;
  platformIds: number[];
}

interface TopologyData {
  segments: TopologySegment[];
  signals: { id: number; segId: number; name: string; offsetM?: number; direction?: string }[];
  switches: { id: string; frogSeg: number | null; normSeg: number | null; revSeg: number | null }[];
  routes: { id: string }[];
}

interface LiveTopologyState {
  clockState: string;
  trains: Array<{ id: string; segId: number | null; color: string; directionCode: 'UP' | 'DOWN'; speedMps: number }>;
  startOptions: Array<{ segmentId: number; stationCode: string; stationName: string; directions: Array<'UP' | 'DOWN'> }>;
}

interface Edge {
  source: number;
  target: number;
  kind: ConnectionKind;
}

interface Crossing {
  x: number;
  y: number;
}

interface Camera {
  x: number;
  y: number;
  width: number;
  height: number;
}

interface PositionedSegment {
  segment: TopologySegment;
  x: number;
  y: number;
  lane?: MainlineLane;
}

const SEG_WIDTH = 34;
const COLUMN_GAP = 42;
const MARGIN_X = 70;
// Leave a clear throat between the two parallel main tracks.  This gives
// crossover legs a real upper/lower side instead of forcing them to pass
// through the opposite running line.
const MAIN_Y: Record<MainlineLane, number> = { up: 82, down: 226 };
const GGZ_DEPOT_ENTRY_IDS = new Set([231, 232]);
const GGZ_DEPOT_SEGMENT_IDS = new Set([
  233, 234,
  ...Array.from({ length: 42 }, (_, index) => index + 239),
  ...Array.from({ length: 20 }, (_, index) => index + 282),
  ...Array.from({ length: 11 }, (_, index) => index + 303),
  ...Array.from({ length: 6 }, (_, index) => index + 315),
]);
const GGZ_DEPOT_INTERFACE_X = MARGIN_X + 220;
const GGZ_DEPOT_FIRST_TRACK_Y = MAIN_Y.down + 180;
const GGZ_DEPOT_TRACK_GAP = 28;
const PLANAR_CROSSOVER_SEGMENTS = { upperSwitch: 64, upperBranch: 65, lowerSwitch: 78, lowerBranch: 80 } as const;
const SECONDARY_THROAT_SEGMENTS = {
  w16: 89, w17: 94, w18: 118, w19: 122, w20: 121, w21: 105, w22: 109, w23: 113,
  upperFeed: [91, 119, 118, 120, 121, 122, 237],
  lowerFeed: [107, 117],
  w17Branch: [95, 124],
  w20Branch: [123, 114],
  w22Branch: [321, 110],
} as const;
const TERTIARY_THROAT_SEGMENTS = {
  w32: 175, w33: 197, w34: 200, w35: 187, w36: 192,
  upperChain: [177, 198, 199, 201],
  lowerChain: [189, 196, 202, 193],
} as const;
const BWR_TURNBACK_THROAT_SEGMENTS = {
  upperAttachment: 131,
  lowerAttachment: 142,
  upperTrack: [147, 148, 149, 150, 152, 153, 155, 157],
  lowerTrack: [158, 159, 160, 161, 163, 164, 166, 168],
  upperReturn: [151, 132],
  lowerReturn: [162, 143],
  outerCrossover: [154, 167],
  innerCrossover: [165, 156],
} as const;
const BQS_TERMINAL_THROAT_SEGMENTS = {
  topMain: [206, 207, 208, 209, 211, 213, 214, 215],
  bottomMain: [219, 220, 221, 222, 223, 225, 227, 228, 229],
  upperInner: [224, 212, 216],
  lowerInner: [210, 226, 230],
} as const;
// 郭公庄咽喉只按 Excel 连接和原联锁演示保留两条直通主线。这里是纯
// 呈现裁剪，不会写入或改变任何 Seg 的原始拓扑指针。
const GGZ_THROAT_SCOPE_IDS = new Set([
  1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23,
  24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43,
  44, 45, 46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 58, 59, 238,
  235,
  ...GGZ_DEPOT_ENTRY_IDS,
  ...GGZ_DEPOT_SEGMENT_IDS,
  314,
]);
const GGZ_THROAT_UP_LEAD = [1, 2, 3, 4] as const;
const GGZ_THROAT_UP_MAIN = [6, 7, 8, 10, 11, 13, 14, 15, 16, 19, 20, 22, 23, 235, 24, 25] as const;
const GGZ_THROAT_DOWN_MAIN = [31, 32, 34, 35, 37, 39, 40, 41, 43, 45, 46, 48, 49, 50, 51, 52] as const;
const GGZ_W4_W7_BRANCH = [9, 38] as const;
const GGZ_W8_W11_BRANCH = [17, 44] as const;
const GGZ_W5_W6_BRANCH = [12, 36] as const;
const GGZ_W9_W10_BRANCH = [18, 42] as const;
const GGZ_THROAT_VISIBLE_IDS = new Set<number>([
  ...GGZ_THROAT_UP_LEAD, ...GGZ_THROAT_UP_MAIN, ...GGZ_THROAT_DOWN_MAIN,
  ...GGZ_W4_W7_BRANCH, ...GGZ_W8_W11_BRANCH, ...GGZ_W5_W6_BRANCH, ...GGZ_W9_W10_BRANCH,
  // Terminal reverse legs at the FSP-side end of the throat.
  5, 21, 26, 27, 28, 29, 30, 33, 47, 53, 231, 232, 233, 234, 238, 239, 240,
  250, 251,
  246, 247, 248, 249, 252, 253, 254, 255, 256, 257, 258, 259, 260, 261,
  262, 263, 265, 266, 267, 268, 269, 270, 271, 273, 282, 285, 286, 287,
  288, 289, 290, 291, 294, 295, 296, 297, 300, 314, 315, 318,
]);
const SHOW_GGZ_DEPOT_BRANCH = false;
// Bump when a fixed local topology arrangement changes, so Vite Fast Refresh
// recomputes the memoized layout instead of retaining a previous coordinate set.
const LAYOUT_REVISION = 122;
// Final GGZ presentation coordinates. They are based on the reviewed V2 SVG,
// then normalized to the runtime topology contract: every switch marker is
// rendered at the right endpoint of its Excel/API frogSeg. Direct neighbours
// keep an 8px clearance unless a longer span is required by a 45-degree leg.
const GGZ_VERIFIED_THROAT_POSITIONS: readonly (readonly [number, number, number])[] = [
  [315, -1436, 34], [297, -1394, 34], [291, -1352, 34], [282, -1310, 34],
  [273, -1268, 34], [271, -1226, 34], [270, -1184, 34], [269, -1138, 34],
  [252, -1096, 34], [248, -1054, 34], [246, -784, 34], [240, -742, 34],
  [239, -700, 34], [233, -658, 34], [231, -616, 34], [1, -574, 34],
  [2, -532, 34], [3, -490, 34], [4, -392, 34], [5, -386, 82], [6, -344, 82],

  [249, -958, 130], [247, -880, 130],
  [268, -958, 226], [266, -880, 226],

  [318, -1776, 322], [300, -1734, 322], [294, -1692, 322], [288, -1650, 322],
  [287, -1608, 322], [286, -1566, 322], [285, -1524, 322],
  [257, -1440, 322], [254, -1398, 322], [253, -1238, 322], [251, -1184, 322],
  [250, -1142, 322], [234, -1100, 322], [232, -1058, 322], [53, -1016, 322],
  [26, -974, 322], [27, -932, 322], [28, -890, 322],
  [267, -826, 322], [265, -784, 322], [29, -574, 322], [238, -532, 322],

  [295, -1684, 288], [289, -1642, 288], [296, -1600, 288], [290, -1558, 288],
  [259, -1322, 364], [255, -1280, 364],
  [256, -1322, 406], [258, -1280, 406],
  [263, -1440, 448], [260, -1398, 448], [261, -1238, 448], [262, -1196, 448],

  [30, -492, 274], [33, -450, 274],
  [31, -444, 226], [32, -402, 226],
];
const fullLineSemantics = getFullLineMainlineSemantics();
const fullLineStationAnchors = getFullLineStationAnchors();

function pairKey(first: number, second: number) {
  return first < second ? `${first}:${second}` : `${second}:${first}`;
}

function isGgzThroatDisplayEdge(edge: Edge) {
  const touchesThroat = GGZ_THROAT_SCOPE_IDS.has(edge.source) || GGZ_THROAT_SCOPE_IDS.has(edge.target);
  if (!touchesThroat) return true;
  return isGgzThroatVisibleSegment(edge.source) && isGgzThroatVisibleSegment(edge.target);
}

function isGgzThroatVisibleSegment(id: number) {
  // Retain the explicitly traced station throat routes and every imported
  // full-line interval continuing beyond FSP.  Depot-only and alternate
  // legs remain out of the presentation graph.
  return !GGZ_THROAT_SCOPE_IDS.has(id) || GGZ_THROAT_VISIBLE_IDS.has(id) || fullLineSemantics.has(id);
}

function average(values: number[]) {
  return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : 0;
}

function findVisualCrossings(
  edges: Edge[],
  positions: Map<number, PositionedSegment>,
  switchByPair: Map<string, 'normal' | 'reverse'>,
  frogByPair: Map<string, number>,
) {
  const crossings = new Map<string, Crossing>();
  const point = (edge: Edge, end: 'start' | 'end') => {
    const source = positions.get(edge.source)!;
    const target = positions.get(edge.target)!;
    const key = pairKey(edge.source, edge.target);
    const targetIsReverseFrog = switchByPair.get(key) === 'reverse' && frogByPair.get(key) === edge.target;
    const endpoints = edgeEndpoints(source, target, targetIsReverseFrog);
    return {
      x: end === 'start' ? endpoints.sourceX : endpoints.targetX,
      y: end === 'start' ? source.y : target.y,
    };
  };
  for (let firstIndex = 0; firstIndex < edges.length; firstIndex += 1) {
    const first = edges[firstIndex];
    const a = point(first, 'start');
    const b = point(first, 'end');
    for (let secondIndex = firstIndex + 1; secondIndex < edges.length; secondIndex += 1) {
      const second = edges[secondIndex];
      if ([first.source, first.target].some((id) => id === second.source || id === second.target)) continue;
      const c = point(second, 'start');
      const d = point(second, 'end');
      const denominator = (b.x - a.x) * (d.y - c.y) - (b.y - a.y) * (d.x - c.x);
      if (Math.abs(denominator) < 0.001) continue;
      const firstRatio = ((c.x - a.x) * (d.y - c.y) - (c.y - a.y) * (d.x - c.x)) / denominator;
      const secondRatio = ((c.x - a.x) * (b.y - a.y) - (c.y - a.y) * (b.x - a.x)) / denominator;
      // Endpoints are topology joins; only interior intersections receive the
      // non-connection marker.
      if (firstRatio <= 0.06 || firstRatio >= 0.94 || secondRatio <= 0.06 || secondRatio >= 0.94) continue;
      const x = a.x + firstRatio * (b.x - a.x);
      const y = a.y + firstRatio * (b.y - a.y);
      crossings.set(`${Math.round(x / 6)}:${Math.round(y / 6)}`, { x, y });
    }
  }
  return [...crossings.values()];
}

function fitCamera(layout: { canvasX: number; canvasY: number; width: number; height: number }): Camera {
  return { x: layout.canvasX, y: layout.canvasY, width: layout.width, height: layout.height };
}

function clampCamera(camera: Camera, layout: { canvasX: number; canvasY: number; width: number; height: number }): Camera {
  const paddingX = layout.width * 0.3;
  const paddingY = layout.height * 0.3;
  const minX = layout.canvasX - paddingX;
  const maxX = layout.canvasX + layout.width + paddingX - camera.width;
  const minY = layout.canvasY - paddingY;
  const maxY = layout.canvasY + layout.height + paddingY - camera.height;
  return {
    ...camera,
    x: Math.min(Math.max(camera.x, Math.min(minX, maxX)), Math.max(minX, maxX)),
    y: Math.min(Math.max(camera.y, Math.min(minY, maxY)), Math.max(minY, maxY)),
  };
}

function isGuogongzhuangDepotComponent(ids: readonly number[]) {
  return ids.filter((id) => GGZ_DEPOT_SEGMENT_IDS.has(id)).length >= 20;
}

function buildLayout(topology: TopologyData) {
  const byId = new Map(topology.segments.map((segment) => [segment.id, segment]));
  const edges: Edge[] = [];
  const neighbours = new Map<number, number[]>();
  const switchByPair = new Map<string, 'normal' | 'reverse'>();
  const frogByPair = new Map<string, number>();

  for (const sw of topology.switches) {
    if (sw.frogSeg != null && sw.normSeg != null) {
      const key = pairKey(sw.frogSeg, sw.normSeg);
      switchByPair.set(key, 'normal');
      frogByPair.set(key, sw.frogSeg);
    }
    if (sw.frogSeg != null && sw.revSeg != null) {
      const key = pairKey(sw.frogSeg, sw.revSeg);
      switchByPair.set(key, 'reverse');
      frogByPair.set(key, sw.frogSeg);
    }
  }
  for (const segment of topology.segments) {
    for (const [target, kind] of [[segment.endForward, 'forward'], [segment.endDiverging, 'diverging']] as const) {
      if (target === null || !byId.has(target)) continue;
      edges.push({ source: segment.id, target, kind });
      (neighbours.get(segment.id) ?? neighbours.set(segment.id, []).get(segment.id)!).push(target);
      (neighbours.get(target) ?? neighbours.set(target, []).get(target)!).push(segment.id);
    }
  }

  const positions = new Map<number, PositionedSegment>();
  for (const segment of topology.segments) {
    const semantic = fullLineSemantics.get(segment.id);
    if (!semantic) continue;
    positions.set(segment.id, {
      segment,
      lane: semantic.lane,
      x: MARGIN_X + semantic.order * COLUMN_GAP,
      y: MAIN_Y[semantic.lane],
    });
  }

  // The final route map in 《线路数据说明》 shows the Guogongzhuang depot as
  // two in/out leads feeding a compact throat and a fan of parallel storage
  // tracks.  S231/S232 are the two data-model entry Segs; giving them fixed
  // slots first keeps the yard component attached to the station rather than
  // being mixed into a generic, unanchored branch component.
  for (const id of GGZ_DEPOT_ENTRY_IDS) {
    const segment = byId.get(id);
    if (!segment) continue;
    positions.set(id, {
      segment,
      x: GGZ_DEPOT_INTERFACE_X + (id === 232 ? COLUMN_GAP : 0),
      y: GGZ_DEPOT_FIRST_TRACK_Y + (id === 232 ? GGZ_DEPOT_TRACK_GAP : 0),
    });
  }

  // W1--W13 form the first compact throat before the larger depot fan.  The
  // imported cells put its reverse Segs hundreds of pixels from their frogs;
  // keep the two running chains on the main lines and use four local lanes
  // for the return/crossover chains, as in the station throat drawings.
  const earlyThroatChains: Array<{ ids: readonly number[]; y: number }> = [
    // S1--S4 are the outer lead of W1. Keep it above the up main so W1
    // reads as a short turnout into the throat instead of another crowded
    // middle-lane branch.
    { ids: [1, 2, 3, 4], y: MAIN_Y.up - 48 },
    { ids: [5, 6, 7, 8, 10, 11, 13, 14, 15, 16, 19, 20, 22, 23], y: MAIN_Y.up },
    // S9--S38 is one straight return chain.  Put it on the central lane so
    // W4 and W7 use equal legs and W5 no longer cuts across the chain.
    { ids: [9, 38], y: MAIN_Y.up + 72 },
    { ids: [12, 36], y: MAIN_Y.up + 72 },
    { ids: [17, 44], y: MAIN_Y.up + 48 },
    { ids: [18, 42], y: MAIN_Y.up + 72 },
    // W12/W13 are the two terminal return legs.  Put them outside the two
    // running lines instead of consuming the same middle lanes as W4--W11;
    // this keeps the crossover field readable as horizontal track rows.
    { ids: [21], y: MAIN_Y.up - 48 },
    { ids: [24, 25, 54, 55, 56, 57, 58, 59], y: MAIN_Y.up },
    { ids: [26, 27, 28, 29, 53, 238], y: MAIN_Y.down },
    { ids: [30, 33], y: MAIN_Y.down - 24 },
    { ids: [31, 32, 34, 35, 37, 39, 40, 41, 43, 45, 46, 48, 49, 50, 51, 52], y: MAIN_Y.down },
    { ids: [47], y: MAIN_Y.down + 48 },
  ];
  const earlyIds = new Set(earlyThroatChains.flatMap((chain) => chain.ids));
  const s22 = positions.get(22);
  const earlyBaseX = s22 ? s22.x - 12 * COLUMN_GAP : MARGIN_X;
    for (const { ids, y } of earlyThroatChains) {
    for (const id of ids) {
      const segment = byId.get(id);
      // These chains contain a few true full-line Segs (S22/S23, S25/S54,
      // etc.). Their shared semantic columns are the mainline alignment
      // contract, so only local branch Segs may be moved here.
      if (!segment || fullLineSemantics.has(id)) continue;
      positions.set(id, { segment, x: earlyBaseX + segment.col * COLUMN_GAP, y });
    }
  }
  for (const edge of edges) {
    if (!earlyIds.has(edge.source) || !earlyIds.has(edge.target)) continue;
    const key = pairKey(edge.source, edge.target);
    if (switchByPair.get(key) !== 'reverse') continue;
    const frogId = frogByPair.get(key);
    const source = positions.get(edge.source)!;
    const target = positions.get(edge.target)!;
    const targetIsFrog = frogId === edge.target;
    const sourceEnd = source.x + SEG_WIDTH;
    const targetEnd = target.x + (targetIsFrog ? SEG_WIDTH : 0);
    const direction = Math.sign(targetEnd - sourceEnd) || 1;
    const rise = Math.abs(target.y - source.y);
    if (frogId === edge.source) positions.set(edge.target, { ...target, x: sourceEnd + direction * rise });
    else if (frogId === edge.target) positions.set(edge.source, { ...source, x: target.x - direction * rise });
  }

  // The GGZ/FSP interlocking demo identifies S1--S13--S22 and
  // S31--S39--S48 as the two running routes. S1--S4 is the W1 approach
  // lead, therefore it remains a separate straight row so S4--W1 is 45°.
  // The remaining connectors occupy two straight running rows. The offsets
  // keep the GGZ platforms (S13/S39) and FSP platforms (S24/S51) aligned.
  const ggzUpStartX = (positions.get(13)?.x ?? MARGIN_X) - 5 * COLUMN_GAP;
  const ggzDownStartX = ggzUpStartX;
  const setGgzMain = (ids: readonly number[], x: number, y: number) => {
    ids.forEach((id, index) => {
      const segment = byId.get(id);
      if (segment) positions.set(id, { segment, x: x + index * COLUMN_GAP, y });
    });
  };
  setGgzMain(GGZ_THROAT_UP_MAIN, ggzUpStartX, MAIN_Y.up);
  setGgzMain(GGZ_THROAT_DOWN_MAIN, ggzDownStartX, MAIN_Y.down);
  setGgzMain(GGZ_THROAT_UP_LEAD, ggzUpStartX - 48 - (GGZ_THROAT_UP_LEAD.length - 1) * COLUMN_GAP, MAIN_Y.up - 48);

  // W4/W7 and W5/W6 are the mirrored four-turnout crossover.  The W4/W7
  // carrier occupies the lower middle row; the opposite W5/W6 carrier is
  // above it, matching the two-short-track X structure of the reference.
  // Open matching columns after S11/S35 so the first carrier is 45° at both
  // frogs while station platform columns stay aligned.
  // W5/W6 follow the same left-pair convention as the reference crossover:
  // both frogs feed their short carrier to the upper-right.  This requires
  // W5's frog to be two Seg widths (plus one small carrier gap) right of
  // W6's frog; the paired S36--S12 connector then remains horizontal.
  // S32--S34 and S35--S37 retain straight, deliberately open joins.
  const s32Position = positions.get(32);
  const s34Main = byId.get(34);
  const s35Main = byId.get(35);
  if (s32Position && s34Main && s35Main) {
    const s34X = s32Position.x + SEG_WIDTH + 64;
    positions.set(34, { segment: s34Main, x: s34X, y: MAIN_Y.down });
    positions.set(35, { segment: s35Main, x: s34X + COLUMN_GAP, y: MAIN_Y.down });
  }
  const shiftGgzMain = (ids: readonly number[], shift: number) => ids.forEach((id) => {
    const position = positions.get(id);
    if (position) positions.set(id, { ...position, x: position.x + shift });
  });
  const w6FrogBeforeAlign = positions.get(34);
  const w4FrogBeforeAlign = positions.get(7);
  if (w6FrogBeforeAlign && w4FrogBeforeAlign) {
    // S32 occupies the lower row's original W4 column. Open its horizontal
    // join, then align W4 over W6 before building the combined crossover.
    shiftGgzMain(GGZ_THROAT_UP_MAIN.slice(GGZ_THROAT_UP_MAIN.indexOf(7)), w6FrogBeforeAlign.x - w4FrogBeforeAlign.x);
  }
  const w4Frog = positions.get(7);
  const w6Frog = positions.get(34);
  const w5FrogBeforeAlign = positions.get(11);
  const w7FrogBeforeAlign = positions.get(37);
  if (w4Frog && w6Frog && w5FrogBeforeAlign && w7FrogBeforeAlign) {
    const carrierGap = 16;
    const rightFrogX = w4Frog.x + SEG_WIDTH * 2 + (MAIN_Y.down - MAIN_Y.up) + carrierGap;
    shiftGgzMain(GGZ_THROAT_UP_MAIN.slice(GGZ_THROAT_UP_MAIN.indexOf(11)), rightFrogX - w5FrogBeforeAlign.x);
    shiftGgzMain(GGZ_THROAT_DOWN_MAIN.slice(GGZ_THROAT_DOWN_MAIN.indexOf(37)), rightFrogX - w7FrogBeforeAlign.x);
  }
  const w5Frog = positions.get(11);
  const w7Frog = positions.get(37);
  const s9 = byId.get(9);
  const s38 = byId.get(38);
  const s12 = byId.get(12);
  const s36 = byId.get(36);
  if (w4Frog && w7Frog && s9 && s38) {
    const lowerCrossoverY = MAIN_Y.up + (MAIN_Y.down - MAIN_Y.up) * 2 / 3;
    positions.set(9, { segment: s9, x: w4Frog.x + SEG_WIDTH + (lowerCrossoverY - MAIN_Y.up), y: lowerCrossoverY });
    positions.set(38, { segment: s38, x: w7Frog.x - (MAIN_Y.down - lowerCrossoverY), y: lowerCrossoverY });
  }
  if (w5Frog && w6Frog && s12 && s36) {
    const upperCrossoverY = MAIN_Y.up + (MAIN_Y.down - MAIN_Y.up) / 3;
    positions.set(12, { segment: s12, x: w5Frog.x - (upperCrossoverY - MAIN_Y.up), y: upperCrossoverY });
    positions.set(36, { segment: s36, x: w6Frog.x + SEG_WIDTH + (MAIN_Y.down - upperCrossoverY), y: upperCrossoverY });
  }
  // Leave the GGZ platforms in their aligned columns and open their left
  // throat clearance by translating the complete W4--W7 structure together.
  // Moving frogs and both carrier chains as one preserves every 45-degree
  // turnout leg and each internal horizontal connection.
  const ggzPlatformApproachPull = 48;
  [7, 9, 11, 12, 34, 36, 37, 38].forEach((id) => {
    const position = positions.get(id);
    if (position) positions.set(id, { ...position, x: position.x - ggzPlatformApproachPull });
  });

  // W8/W9/W10/W11 repeat the same four-frog X: W8/W10 are the left pair,
  // W9/W11 the right pair.  Keep their carrier Segs on two separate rows so
  // there is no accidental overlap at the centre.
  // Keep S14/S40 to the right of the aligned GGZ platforms S13/S39.
  // Moving those frog Segs left as a cosmetic group reverses the imported
  // main-line order, so only the carrier rows below are adjusted here.
  const w8Frog = positions.get(14);
  const w10Frog = positions.get(40);
  const w9FrogBeforeAlign = positions.get(16);
  const w11FrogBeforeAlign = positions.get(43);
  if (w8Frog && w10Frog && w9FrogBeforeAlign && w11FrogBeforeAlign) {
    const carrierGap = 16;
    const rightFrogX = w8Frog.x + SEG_WIDTH * 2 + (MAIN_Y.down - MAIN_Y.up) + carrierGap;
    shiftGgzMain(GGZ_THROAT_UP_MAIN.slice(GGZ_THROAT_UP_MAIN.indexOf(16)), rightFrogX - w9FrogBeforeAlign.x);
    shiftGgzMain(GGZ_THROAT_DOWN_MAIN.slice(GGZ_THROAT_DOWN_MAIN.indexOf(43)), rightFrogX - w11FrogBeforeAlign.x);
  }
  const w9Frog = positions.get(16);
  const w11Frog = positions.get(43);
  const s17 = byId.get(17);
  const s44 = byId.get(44);
  if (w8Frog && w11Frog && s17 && s44) {
    const lowerCrossoverY = MAIN_Y.up + (MAIN_Y.down - MAIN_Y.up) * 2 / 3;
    positions.set(17, { segment: s17, x: w8Frog.x + SEG_WIDTH + (lowerCrossoverY - MAIN_Y.up), y: lowerCrossoverY });
    positions.set(44, { segment: s44, x: w11Frog.x - (MAIN_Y.down - lowerCrossoverY), y: lowerCrossoverY });
  }

  // W10--S42--S18--W9 is the opposing lower-left to upper-right chain.
  const s18 = byId.get(18);
  const s42 = byId.get(42);
  if (w9Frog && w10Frog && s18 && s42) {
    const upperCrossoverY = MAIN_Y.up + (MAIN_Y.down - MAIN_Y.up) / 3;
    positions.set(42, { segment: s42, x: w10Frog.x + SEG_WIDTH + (MAIN_Y.down - upperCrossoverY), y: upperCrossoverY });
    positions.set(18, { segment: s18, x: w9Frog.x - (upperCrossoverY - MAIN_Y.up), y: upperCrossoverY });
  }

  // The W8--W11 crossover needs its full 45-degree span.  Rather than pull
  // S22/S48 back across S15/S41 (which reverses both imported main-line
  // orders), translate the complete GGZ-side structure left as one unit.
  // FSP and every Seg to its right stay fixed; the final joins are then
  // S20--S22 and S46--S48 with an 8px straight clearance.
  const ggzToFspClearancePull = 246;
  [...Array(21).keys(), ...Array.from({ length: 18 }, (_, index) => index + 30)].forEach((id) => {
    const position = positions.get(id);
    if (position) positions.set(id, { ...position, x: position.x - ggzToFspClearancePull });
  });

  // W14/W15 form a compact two-turnout crossover.  Both frogs remain on the
  // main lines.  The added S63--S64 spacing lets both turnout legs use the
  // same lower-left to upper-right, 45-degree drawing convention.
  const crossoverStep = (MAIN_Y.down - MAIN_Y.up) / 3;
  const upperMainReflowOffset = crossoverStep * 4;
  const upperSwitchOrder = fullLineSemantics.get(PLANAR_CROSSOVER_SEGMENTS.upperSwitch)?.order;
  if (upperSwitchOrder !== undefined) {
    for (const [id, position] of positions) {
      const semantic = fullLineSemantics.get(id);
      // Orders are shared by the up/down station blocks.  Shift both lanes so
      // platform tracks remain vertically aligned after making room for W14.
      if (semantic && semantic.order >= upperSwitchOrder) {
        positions.set(id, { ...position, x: position.x + upperMainReflowOffset });
      }
    }
  }

  // S124 is the left-hand link into S95, before W17.  Open a second shared
  // station-column gap at S94 so that chain can remain planar and W17's leg
  // is also a 45-degree lower-left to upper-right turnout.
  const w17Order = fullLineSemantics.get(94)?.order;
  const w17ReflowOffset = 304;
  if (w17Order !== undefined) {
    for (const [id, position] of positions) {
      const semantic = fullLineSemantics.get(id);
      if (semantic && semantic.order >= w17Order) {
        positions.set(id, { ...position, x: position.x + w17ReflowOffset });
      }
    }
  }

  // S80 (left) and S65 (right) share one middle short-track lane.  Keep that
  // chain exactly between the two main lines: W14 and W15 then each use the
  // same 72px 45-degree leg, rather than one short and one over-extended leg.
  const upperSwitch = positions.get(PLANAR_CROSSOVER_SEGMENTS.upperSwitch);
  const lowerSwitch = positions.get(PLANAR_CROSSOVER_SEGMENTS.lowerSwitch);
  const upperBranch = byId.get(PLANAR_CROSSOVER_SEGMENTS.upperBranch);
  const lowerBranch = byId.get(PLANAR_CROSSOVER_SEGMENTS.lowerBranch);
  if (upperSwitch && lowerSwitch && upperBranch && lowerBranch) {
    const middleTrackY = (MAIN_Y.up + MAIN_Y.down) / 2;
    const middleLeg = middleTrackY - MAIN_Y.up;
    positions.set(PLANAR_CROSSOVER_SEGMENTS.upperBranch, {
      segment: upperBranch,
      x: upperSwitch.x - middleLeg,
      y: middleTrackY,
    });
    positions.set(PLANAR_CROSSOVER_SEGMENTS.lowerBranch, {
      segment: lowerBranch,
      x: lowerSwitch.x + SEG_WIDTH + middleLeg,
      y: middleTrackY,
    });
  }

  // S236/S170 are the two in-line Segs between S169 and S171.  They are not
  // part of the semantic backbone, so the generic branch placer used to put
  // them back onto S169 and overlap its label/signal.  Open two real mainline
  // columns for them and shift every later physical column on both directions
  // together, preserving the station alignment beyond this point.
  const s169 = positions.get(169);
  const s171 = positions.get(171);
  const s180 = positions.get(180);
  const s236 = byId.get(236);
  const s170 = byId.get(170);
  if (s169 && s171 && s180 && s236 && s170) {
    const inlineGap = SEG_WIDTH + 8;
    const desiredS171X = s169.x + inlineGap * 3;
    const reflow = desiredS171X - s171.x;
    const s171Order = fullLineSemantics.get(171)?.order;
    if (reflow > 0 && s171Order !== undefined) {
      for (const [id, position] of positions) {
        const semantic = fullLineSemantics.get(id);
        // Coordinates may already include clearance inserted by an earlier
        // throat.  Reflow by shared physical column instead of current x so
        // both directions of every station remain vertically aligned.
        if (semantic && semantic.order >= s171Order) positions.set(id, { ...position, x: position.x + reflow });
      }
    }
    positions.set(236, { segment: s236, x: s169.x + inlineGap, y: MAIN_Y.up, lane: 'up' });
    positions.set(170, { segment: s170, x: s169.x + inlineGap * 2, y: MAIN_Y.up, lane: 'up' });
    // The added S236 column exists only on the up direction.  Move the paired
    // JBG down-platform Seg into S170's column so the station remains aligned
    // while S179/S181 continue as its two straight running approaches.
    positions.set(180, { ...s180, x: s169.x + inlineGap * 2 });
  }

  // The W16--W23 throat has two distinct parallel short-track layers.  Keep
  // the S91/S119/S118/S120... feed in the upper half of the space between the
  // two main lines; put S107/S117 in the lower half.  This removes the false
  // diagonal crossings and leaves each turnout with a short, readable leg.
  const w16 = positions.get(SECONDARY_THROAT_SEGMENTS.w16);
  const w17 = positions.get(SECONDARY_THROAT_SEGMENTS.w17);
  const w21 = positions.get(SECONDARY_THROAT_SEGMENTS.w21);
  const w22 = positions.get(SECONDARY_THROAT_SEGMENTS.w22);
  const w23 = positions.get(SECONDARY_THROAT_SEGMENTS.w23);
  const secondaryIds: number[] = [
    ...SECONDARY_THROAT_SEGMENTS.upperFeed,
    ...SECONDARY_THROAT_SEGMENTS.lowerFeed,
    ...SECONDARY_THROAT_SEGMENTS.w17Branch,
    ...SECONDARY_THROAT_SEGMENTS.w20Branch,
    ...SECONDARY_THROAT_SEGMENTS.w22Branch,
  ];
  const secondarySegments = new Map<number, TopologySegment | undefined>(secondaryIds.map((id) => [id, byId.get(id)]));
  if (w16 && w17 && w21 && w22 && w23 && [...secondarySegments.values()].every(Boolean)) {
    const upperInnerY = MAIN_Y.up + (MAIN_Y.down - MAIN_Y.up) / 3;
    const lowerInnerY = MAIN_Y.up + (MAIN_Y.down - MAIN_Y.up) * 2 / 3;
    const innerStep = lowerInnerY - upperInnerY;
    const bridgeY = (MAIN_Y.up + upperInnerY) / 2;
    const bridgeStep = upperInnerY - bridgeY;
    const set = (id: number, x: number, y: number) => positions.set(id, { segment: secondarySegments.get(id)!, x, y });
    // Keep the reverse legs of W16--W23 on the same \"lower-left to
    // upper-right\" 45-degree convention used for W14/W15.  The short
    // straight feeds occupy their own horizontal lanes between those legs.
    const w16FeedStartX = w16.x + SEG_WIDTH + innerStep;
    const w21FeedStartX = w21.x + SEG_WIDTH + innerStep;
    const w18X = w21FeedStartX + SEG_WIDTH + 8 + innerStep;
    const s120X = w18X + COLUMN_GAP;
    const s121X = s120X + COLUMN_GAP + 8;
    const s122X = s121X + COLUMN_GAP;
    const s123X = s121X + SEG_WIDTH + innerStep;
    const s114X = w23.x - innerStep;
    const s124X = s122X + SEG_WIDTH + bridgeStep;
    const s95X = s124X + SEG_WIDTH + 8;

    // Upper inner parallel: W16 feeds W18, W20 and W19 in order.
    set(91, w16FeedStartX, upperInnerY);
    set(119, w16FeedStartX + COLUMN_GAP, upperInnerY);
    set(118, w18X, upperInnerY);
    set(120, s120X, upperInnerY);
    set(121, s121X, upperInnerY);
    set(122, s122X, upperInnerY);
    set(237, s122X + COLUMN_GAP, upperInnerY);

    // Lower inner parallel: W21 reaches W18 from the opposite side.
    set(107, w21FeedStartX, lowerInnerY);
    set(117, w21FeedStartX + SEG_WIDTH + 8, lowerInnerY);

    // W17/W19 are a second short staggered chain in the clear space to the
    // right; W20/W23 and W22 use separate lower lanes so their Segs cannot
    // overlap at the same coordinate.
    // S95 and the S12x branch live in the clear strip between S96 (top main)
    // and S120 (upper inner line), rather than being pushed beneath W20/W19.
    set(95, s95X, bridgeY);
    set(123, s123X, lowerInnerY);
    set(124, s124X, bridgeY);
    set(114, s114X, lowerInnerY);
    set(110, w22.x - innerStep, MAIN_Y.down + innerStep);
    set(321, w22.x - innerStep - COLUMN_GAP, MAIN_Y.down + innerStep);
  }

  // W37--W42 are the BQS terminal throat.  Its two through routes remain
  // horizontal; the two short-track chains get independent inner lanes.  The
  // only diagonals are the catalogue-declared yellow turnout legs, each drawn
  // to its frog Seg's right-hand dot at a true 45 degrees.
  const bqsIds = [
    ...BQS_TERMINAL_THROAT_SEGMENTS.topMain,
    ...BQS_TERMINAL_THROAT_SEGMENTS.bottomMain,
    ...BQS_TERMINAL_THROAT_SEGMENTS.upperInner,
    ...BQS_TERMINAL_THROAT_SEGMENTS.lowerInner,
  ];
  const bqsSegments = new Map<number, TopologySegment | undefined>(bqsIds.map((id) => [id, byId.get(id)]));
  const applyBqsTerminalThroat = () => {
    // Read the anchors when the layout is applied: a preceding local throat
    // can legitimately have opened a common downstream mainline gap.
    const bqsUpPlatform = positions.get(207);
    const bqsDownPlatform = positions.get(220);
    if (!bqsUpPlatform || !bqsDownPlatform || ![...bqsSegments.values()].every(Boolean)) return;
    const shortStep = (MAIN_Y.down - MAIN_Y.up) / 3;
    const upperInnerY = MAIN_Y.up + shortStep;
    const lowerInnerY = MAIN_Y.down - shortStep;
    // S206/S219 and S207/S220 are deliberately left at their semantic
    // columns.  Start the throat after their shared platform column; every
    // added clearance bay is then a common downstream expansion, rather than
    // a one-sided shift that would make the station appear skewed.
    const baseX = Math.max(bqsUpPlatform.x, bqsDownPlatform.x) + COLUMN_GAP;
    const set = (id: number, x: number, y: number) => positions.set(id, { segment: bqsSegments.get(id)!, x, y });

    // Make the two sides a mirrored six-switch throat rather than two
    // unrelated diagonal fans. W37/W39, W38/W40 and W41/W42 share columns;
    // the two inner chains are parallel and only the turnout legs cross lanes.
    set(208, baseX, MAIN_Y.up);
    set(209, baseX + COLUMN_GAP, MAIN_Y.up);
    set(211, baseX + 220, MAIN_Y.up);
    set(213, baseX + 262, MAIN_Y.up);
    set(214, baseX + 304, MAIN_Y.up);
    set(215, baseX + 346, MAIN_Y.up);

    set(221, baseX - COLUMN_GAP, MAIN_Y.down);
    set(222, baseX, MAIN_Y.down);
    set(223, baseX + 178, MAIN_Y.down);
    set(225, baseX + 220, MAIN_Y.down);
    set(227, baseX + 262, MAIN_Y.down);
    set(228, baseX + 304, MAIN_Y.down);
    set(229, baseX + 346, MAIN_Y.down);

    // W37/W39 have matching 96 x 96 legs. Their return chains line up at
    // +130/+172 before meeting the aligned W38/W40 frogs. W41/W42 repeat the
    // same mirror with matching 48 x 48 legs; every grey link stays straight.
    set(224, baseX + 130, upperInnerY);
    set(212, baseX + 172, upperInnerY);
    set(216, baseX + 386, upperInnerY);
    set(210, baseX + 130, lowerInnerY);
    set(226, baseX + 172, lowerInnerY);
    set(230, baseX + 386, lowerInnerY);
  };
  applyBqsTerminalThroat();

  // The S147--S168 group at Beijing West is a compact turnback/crossover
  // throat between S131 (up) and S142 (down), rather than a depot tail.  Keep
  // the two through tracks inside the space between the main running lines.
  const bwrUpperAttachment = positions.get(BWR_TURNBACK_THROAT_SEGMENTS.upperAttachment);
  const bwrLowerAttachment = positions.get(BWR_TURNBACK_THROAT_SEGMENTS.lowerAttachment);
  const bwrIds = [
    ...BWR_TURNBACK_THROAT_SEGMENTS.upperTrack,
    ...BWR_TURNBACK_THROAT_SEGMENTS.lowerTrack,
    ...BWR_TURNBACK_THROAT_SEGMENTS.upperReturn,
    ...BWR_TURNBACK_THROAT_SEGMENTS.lowerReturn,
    ...BWR_TURNBACK_THROAT_SEGMENTS.outerCrossover,
    ...BWR_TURNBACK_THROAT_SEGMENTS.innerCrossover,
  ];
  const bwrSegments = new Map<number, TopologySegment | undefined>(bwrIds.map((id) => [id, byId.get(id)]));
  if (bwrUpperAttachment && bwrLowerAttachment && [...bwrSegments.values()].every(Boolean)) {
    // Six even 24 px layers fill the space between the two running lines:
    // upper return, upper short track, crossover lane, lower short track,
    // lower return.  The two parallel short tracks are consequently centred.
    const upperTrackY = MAIN_Y.up + 48;
    const crossoverY = MAIN_Y.up + 72;
    const lowerTrackY = MAIN_Y.down - 48;
    const lowerReturnY = MAIN_Y.down - 24;
    const upperLeadY = MAIN_Y.up + 24;
    const upperX = bwrUpperAttachment.x;
    const lowerX = bwrLowerAttachment.x;
    const set = (id: number, x: number, y: number) => positions.set(id, { segment: bwrSegments.get(id)!, x, y });

    // W24 is frog S131, with S132 as its reverse leg.  S151/S132 occupy one
    // horizontal short track and the yellow S132--W24 leg ends at the frog's
    // right-hand endpoint.
    set(147, lowerX - 250, upperTrackY);
    set(148, lowerX - 208, upperTrackY);
    set(149, lowerX - 166, upperTrackY);
    set(150, lowerX - 124, upperTrackY);
    set(152, lowerX - 84, upperTrackY);
    set(153, lowerX, upperTrackY);
    set(155, lowerX + 124, upperTrackY);
    set(157, lowerX + 166, upperTrackY);
    set(151, upperX - 66, upperLeadY);
    set(132, upperX - 24, upperLeadY);

    // W26--W29 form the outer crossover; W28--W27 form the inner crossover.
    // The grey middle pieces are horizontal.  Each yellow turnout leg changes
    // lane by exactly 24 px horizontally and vertically, i.e. a true 45°.
    set(154, lowerX - 26, crossoverY);
    set(167, lowerX + 58, crossoverY);
    set(165, lowerX + 16, crossoverY);
    set(156, lowerX + 100, crossoverY);

    set(158, lowerX - 210, lowerTrackY);
    set(159, lowerX - 168, lowerTrackY);
    set(160, lowerX - 126, lowerTrackY);
    set(161, lowerX - 84, lowerTrackY);
    set(163, lowerX - 42, lowerTrackY);
    set(164, lowerX, lowerTrackY);
    // Leave a clear visual bay around W29 and the adjacent signal symbol.
    set(166, lowerX + 82, lowerTrackY);
    set(168, lowerX + 124, lowerTrackY);
    // S162/S143 share the otherwise empty strip between the short lower track
    // and the down mainline.  Their grey links stay horizontal/vertical; W30
    // alone makes the 45-degree diagonal into this return lead.
    set(162, lowerX - 68, lowerReturnY);
    set(143, lowerX - 24, lowerReturnY);
  }

  // S175/S187/S192 are the three frogs on the two main lines around W32--W36.
  // Treat the W33/W34 return chain as the same three-layer pattern used at
  // W16--W23: short horizontal chains occupy the two inner lanes, while only
  // the catalogue-declared reverse legs are diagonal.  The downstream shared
  // reflow keeps the following up/down station columns vertically aligned.
  const s173 = positions.get(173);
  const s178 = positions.get(178);
  const tertiaryIds = [
    174,
    TERTIARY_THROAT_SEGMENTS.w32,
    176,
    TERTIARY_THROAT_SEGMENTS.w33,
    TERTIARY_THROAT_SEGMENTS.w34,
    ...TERTIARY_THROAT_SEGMENTS.upperChain,
    ...TERTIARY_THROAT_SEGMENTS.lowerChain,
  ];
  const tertiarySegments = new Map<number, TopologySegment | undefined>(tertiaryIds.map((id) => [id, byId.get(id)]));
  if (s173 && s178 && [...tertiarySegments.values()].every(Boolean)) {
    const turnoutStep = (MAIN_Y.down - MAIN_Y.up) / 3;
    const upperInnerY = MAIN_Y.up + turnoutStep;
    const lowerInnerY = MAIN_Y.down - turnoutStep;
    const compactGap = SEG_WIDTH + 8;
    const set = (id: number, x: number, y: number) => positions.set(id, { segment: tertiarySegments.get(id)!, x, y });

    // S174/W32/S176 are omitted from the semantic backbone because this is a
    // compact turnout group.  Place them explicitly on the upper main line,
    // then reflow every following mainline column on both directions together.
    const s174X = s173.x + compactGap;
    const w32X = s174X + compactGap;
    const s176X = w32X + compactGap;
    const mainlineReflow = s176X + compactGap - s178.x;
    const s178Order = fullLineSemantics.get(178)?.order;
    if (mainlineReflow > 0 && s178Order !== undefined) {
      for (const [id, position] of positions) {
        const semantic = fullLineSemantics.get(id);
        if (semantic && semantic.order >= s178Order) positions.set(id, { ...position, x: position.x + mainlineReflow });
      }
    }

    const w35 = positions.get(TERTIARY_THROAT_SEGMENTS.w35)!;
    const w36 = positions.get(TERTIARY_THROAT_SEGMENTS.w36)!;

    // W35's branch determines W33's frog coordinate: S189--S196 stay a
    // compact horizontal pair, then their yellow leg reaches W33's right dot.
    const s189X = w35.x + SEG_WIDTH + turnoutStep;
    const s196X = s189X + compactGap;
    const w33X = s196X + turnoutStep;
    const s202X = w33X + compactGap * 2 + SEG_WIDTH + turnoutStep;
    const s193X = s202X + compactGap;
    const w36X = s193X + turnoutStep;
    const downstreamShift = w36X - w36.x;

    // There is no spare column between W36 and the later stations.  Shift
    // both physical mainline lanes together so their station anchors remain
    // aligned after the throat is opened.
    if (downstreamShift > 0) {
      for (const [id, position] of positions) {
        const semantic = fullLineSemantics.get(id);
        if (semantic && position.x >= w36.x) positions.set(id, { ...position, x: position.x + downstreamShift });
      }
    }

    // W32's normal route stays on the top main line.  Its reverse route drops
    // to the upper inner lane; the intentionally larger grey span is still
    // a horizontal Seg-to-Seg connection, never a decorative diagonal.
    const s177X = w32X + SEG_WIDTH + turnoutStep;
    const s198X = w33X - compactGap;
    set(174, s174X, MAIN_Y.up);
    set(175, w32X, MAIN_Y.up);
    set(176, s176X, MAIN_Y.up);
    set(177, s177X, upperInnerY);
    set(198, s198X, upperInnerY);
    set(197, w33X, upperInnerY);
    set(199, w33X + compactGap, upperInnerY);
    set(200, w33X + compactGap * 2, upperInnerY);
    set(201, w33X + compactGap * 3, upperInnerY);

    set(189, s189X, lowerInnerY);
    set(196, s196X, lowerInnerY);
    set(202, s202X, lowerInnerY);
    set(193, s193X, lowerInnerY);
  }

  // The W32--W36 reflow above moves later mainline columns together. Reapply
  // the BQS local geometry from its now-final station anchors so that its
  // non-mainline short tracks move with the same station instead of leaving
  // stretched turnout legs behind.
  applyBqsTerminalThroat();

  // Keep a branch next to the station/mainline Segs it actually joins.  Its
  // original Member-C row/col grid remains the local ordering, but no longer
  // stretches arbitrarily across the whole railway.
  const branchIds = new Set(topology.segments.filter((segment) => !positions.has(segment.id)).map((segment) => segment.id));
  const components: number[][] = [];
  const seen = new Set<number>();
  for (const root of branchIds) {
    if (seen.has(root)) continue;
    const component: number[] = [];
    const queue = [root];
    seen.add(root);
    while (queue.length) {
      const current = queue.shift()!;
      component.push(current);
      for (const next of neighbours.get(current) ?? []) {
        if (branchIds.has(next) && !seen.has(next)) {
          seen.add(next);
          queue.push(next);
        }
      }
    }
    components.push(component);
  }

  const componentInfos = components.map((ids) => {
    const component = new Set(ids);
    const attachments = ids.flatMap((id) => (neighbours.get(id) ?? []))
      .filter((id) => !component.has(id))
      .map((id) => positions.get(id))
      .filter((item): item is PositionedSegment => item != null);
    const attachmentXs = attachments.map((item) => item.x);
    const segments = ids.map((id) => byId.get(id)!);
    return {
      ids,
      segments,
      attachments,
      anchorX: attachmentXs.length ? average(attachmentXs) : MARGIN_X,
      minAttachmentX: attachmentXs.length ? Math.min(...attachmentXs) : MARGIN_X,
      maxAttachmentX: attachmentXs.length ? Math.max(...attachmentXs) : MARGIN_X,
    };
  }).sort((left, right) => left.anchorX - right.anchorX || left.ids[0] - right.ids[0]);

  const localOrdinal = new Map<number, number>();
  for (const info of componentInfos) {
    if (isGuogongzhuangDepotComponent(info.ids)) {
      // A depot is a fan of horizontal tracks, not a graph laid out from the
      // imported spreadsheet row/column cells.  Contract every non-reverse
      // connection into a physical track first; yellow reverse-switch links
      // then become the only links allowed to change track.
      const yardIds = new Set<number>([
        ...info.ids.filter((id) => GGZ_DEPOT_SEGMENT_IDS.has(id)),
        ...GGZ_DEPOT_ENTRY_IDS,
      ]);
      const yardSegments = [...yardIds].map((id) => byId.get(id)).filter((segment): segment is TopologySegment => segment != null);
      const parent = new Map<number, number>([...yardIds].map((id) => [id, id]));
      const find = (id: number): number => {
        const current = parent.get(id)!;
        if (current === id) return id;
        const root = find(current);
        parent.set(id, root);
        return root;
      };
      const join = (left: number, right: number) => {
        const leftRoot = find(left);
        const rightRoot = find(right);
        if (leftRoot !== rightRoot) parent.set(rightRoot, leftRoot);
      };
      const yardEdges = edges.filter((edge) => yardIds.has(edge.source) && yardIds.has(edge.target));
      for (const edge of yardEdges) {
        if (switchByPair.get(pairKey(edge.source, edge.target)) !== 'reverse') join(edge.source, edge.target);
      }

      const tracks = new Map<number, TopologySegment[]>();
      for (const segment of yardSegments) (tracks.get(find(segment.id)) ?? tracks.set(find(segment.id), []).get(find(segment.id))!).push(segment);
      // Fixed physical lanes for the depot.  Chains sharing a row have
      // disjoint source-column spans; every overlapping span gets its own
      // lane.  This keeps Seg labels/lines apart while retaining short,
      // adjacent-layer turnout legs instead of fanning a reverse branch across
      // the entire depot.
      const yardLaneByAnchor = new Map<number, number>([
        // middle upper / middle lower: the two depot entrance spines
        [231, 130], [232, 322],
        // above the up main: return chains, packed by non-overlapping x span
        [241, 58], [243, 10], [272, 34], [275, -14], [283, -38],
        // middle crossover tracks between the entrance spines
        [247, 214], [266, 154],
        // below the down main: paired only when their x spans do not meet
        [255, 382], [256, 274], [260, 442], [289, 250], [290, 274], [306, 490], [309, 538],
      ]);
      const trackY = new Map<number, number>();
      for (const [root, segments] of tracks) {
        const anchor = segments.find((segment) => yardLaneByAnchor.has(segment.id))?.id;
        trackY.set(root, anchor == null ? MAIN_Y.down + 370 : yardLaneByAnchor.get(anchor)!);
      }

      const maxColumn = Math.max(15, ...yardSegments.map((segment) => segment.col));
      const rawX = new Map<number, number>();
      for (const segment of yardSegments) rawX.set(segment.id, GGZ_DEPOT_INTERFACE_X - (maxColumn - segment.col) * COLUMN_GAP);

      for (const segment of yardSegments) {
        positions.set(segment.id, {
          segment,
          x: rawX.get(segment.id)!,
          y: trackY.get(find(segment.id))!,
        });
      }
      // Shift the complete S318 entry spine left by one real column.  It keeps
      // every grey direct link in its original order, sends W46 through the
      // S40/S43 clearance, and lets the S232--S53 direct link pass to the
      // left of the W11/W13 pocket.
      for (const id of [232, 234, 250, 251, 253, 254, 257, 265, 267, 285, 286, 287, 288, 294, 300, 318]) {
        const position = positions.get(id);
        if (position) positions.set(id, { ...position, x: position.x - COLUMN_GAP });
      }
      // Keep the straight-track chains fixed.  Each reverse Seg belongs to a
      // single turnout, so it can be placed directly from that frog's drawn
      // endpoint without recursively dragging every other yard track across
      // the diagram.  The resulting yellow leg is exactly 45 degrees.
      for (const edge of yardEdges) {
        const key = pairKey(edge.source, edge.target);
        if (switchByPair.get(key) !== 'reverse') continue;
        const frogId = frogByPair.get(key);
        const source = positions.get(edge.source)!;
        const target = positions.get(edge.target)!;
        const targetIsFrog = frogId === edge.target;
        const sourceEnd = source.x + SEG_WIDTH;
        const targetEnd = target.x + (targetIsFrog ? SEG_WIDTH : 0);
        const direction = Math.sign(targetEnd - sourceEnd) || 1;
        const rise = Math.abs(target.y - source.y);
        if (frogId === edge.source) {
          positions.set(edge.target, { ...target, x: sourceEnd + direction * rise });
        } else if (frogId === edge.target) {
          positions.set(edge.source, { ...source, x: target.x - direction * rise });
        }
      }
      // W59/W60 share the short S303--S308 throat.  The generic 45-degree
      // placement correctly anchors S306 and S308 to their frogs, but leaves
      // the two intervening straight chains folded back over themselves.
      // Lay the complete chains out from those fixed endpoints, with a 6px
      // physical gap between adjacent Segs.  This removes the S306/S307/S308
      // overlap while retaining both yellow turnout legs at 45 degrees.
      const s303 = positions.get(303);
      const s306 = positions.get(306);
      const s307 = positions.get(307);
      const s308 = positions.get(308);
      const s309 = positions.get(309);
      const s310 = positions.get(310);
      const s311 = positions.get(311);
      const s312 = positions.get(312);
      const s313 = positions.get(313);
      if (s303 && s306 && s307 && s308 && s309 && s310 && s311 && s312 && s313) {
        const straightStep = SEG_WIDTH + 6;
        const turnoutStep = Math.abs(s306.y - s303.y);
        // Put the two lower chains to the left of their upper frogs.  W59
        // and W60 then both read consistently as lower-left to upper-right
        // 45-degree turnout legs.
        const s306X = s303.x - turnoutStep;
        const s307X = s306X - straightStep;
        const s308X = s307X - straightStep;
        // S309 is the W60 frog: its right endpoint stays one turnoutStep to
        // the left of S308's left endpoint.
        const s309X = s308X - turnoutStep - SEG_WIDTH;
        positions.set(306, { ...s306, x: s306X });
        positions.set(307, { ...s307, x: s307X });
        positions.set(308, { ...s308, x: s308X });
        positions.set(309, { ...s309, x: s309X });
        positions.set(310, { ...s310, x: s309X - straightStep });
        positions.set(311, { ...s311, x: s309X + straightStep });
        positions.set(312, { ...s312, x: s309X + straightStep * 2 });
        positions.set(313, { ...s313, x: s309X + straightStep * 3 });
      }
      // A few ordinary branch Segs are connected to the depot component in
      // the source graph but are not part of the yard. Keep them out of the
      // yard fan so they cannot stretch its entrance or storage-track lanes.
      const spillover = info.segments.filter((segment) => !yardIds.has(segment.id));
      if (spillover.length) {
        const spillMinCol = Math.min(...spillover.map((segment) => segment.col));
        const spillMinRow = Math.min(...spillover.map((segment) => segment.row));
        for (const segment of spillover) {
          positions.set(segment.id, {
            segment,
            x: info.maxAttachmentX + 120 + (segment.col - spillMinCol) * COLUMN_GAP,
            y: MAIN_Y.down + 82 + (segment.row - spillMinRow) * 32,
          });
        }
      }
      continue;
    }
    const anchorBucket = Math.round(info.anchorX / (COLUMN_GAP * 2));
    const ordinal = localOrdinal.get(anchorBucket) ?? 0;
    localOrdinal.set(anchorBucket, ordinal + 1);
    const minColumn = Math.min(...info.segments.map((segment) => segment.col));
    const maxColumn = Math.max(...info.segments.map((segment) => segment.col));
    const minRow = Math.min(...info.segments.map((segment) => segment.row));
    const sourceWidth = Math.max(1, maxColumn - minColumn);
    const localWidth = Math.max(160, info.maxAttachmentX - info.minAttachmentX + 150, sourceWidth * 38);
    const left = info.anchorX - localWidth / 2;

    // A one-Seg component is usually the omitted in/out connector between
    // two station-track Segs.  Keep it on that same track (or exactly in the
    // crossover gap) instead of drawing two long diagonals down to a generic
    // branch row.  Large yard and turnback components still use their local
    // row/column grid below the backbone.
    if (info.segments.length === 1 && info.attachments.length) {
      const segment = info.segments[0];
      const sameLane = info.attachments.every((item) => item.lane && item.lane === info.attachments[0].lane);
      positions.set(segment.id, {
        segment,
        x: average(info.attachments.map((item) => item.x)),
        y: sameLane ? info.attachments[0].y : average(info.attachments.map((item) => item.y)),
        lane: sameLane ? info.attachments[0].lane : undefined,
      });
      continue;
    }
    for (const segment of info.segments) {
      positions.set(segment.id, {
        segment,
        x: left + ((segment.col - minColumn) / sourceWidth) * localWidth,
        y: MAIN_Y.down + 82 + ordinal * 18 + (segment.row - minRow) * 42,
      });
    }
  }

  // Expand the two adjacent middle-station blocks instead of merely moving
  // their labels.  Each platform keeps an incoming and outgoing straight
  // Seg, with a second interval Seg on either side before the next platform.
  const setStraightSlot = (id: number, x: number) => {
    const position = positions.get(id);
    if (position) positions.set(id, { ...position, x });
  };
  const kylUp = positions.get(55);
  const kylDown = positions.get(69);
  if (kylUp && kylDown) {
    const step = SEG_WIDTH + 18;
    // Keep KYL and everything to its right fixed.  FSP's imported physical
    // order is S22--S23--S235--S24--S25 above and
    // S48--S49--S50--S51--S52 below.  Every platform keeps a real immediate
    // neighbouring Seg on both sides; no artificial blank slot is inserted.
    const kylX = average([kylUp.x, kylDown.x]);
    setStraightSlot(55, kylX);
    setStraightSlot(69, kylX);
    setStraightSlot(54, kylX - step);
    setStraightSlot(68, kylX - step);
    setStraightSlot(25, kylX - step * 2);
    setStraightSlot(52, kylX - step * 2);
    setStraightSlot(24, kylX - step * 3);
    setStraightSlot(51, kylX - step * 3);
    setStraightSlot(235, kylX - step * 4);
    setStraightSlot(50, kylX - step * 4);
    setStraightSlot(23, kylX - step * 5);
    setStraightSlot(49, kylX - step * 5);
    setStraightSlot(48, kylX - step * 6);
    setStraightSlot(22, kylX - step * 6);
  }

  // Normalise the visible GGZ--FSP--KYL corridor to one rail pitch: each
  // 34px Seg is followed by an 8px clear interval.  Use S16/S43 as the
  // fixed crossover exits and reflow both directions together, preserving
  // every station column while avoiding an unrelated right-side move.
  const corridorStep = SEG_WIDTH + 8;
  const reflowCorridor = (ids: readonly number[]) => {
    const anchor = positions.get(ids[0]);
    if (!anchor) return;
    ids.forEach((id, index) => setStraightSlot(id, anchor.x + index * corridorStep));
  };
  reflowCorridor([16, 19, 20, 22, 23, 235, 24, 25, 54, 55, 56]);
  reflowCorridor([43, 45, 46, 48, 49, 50, 51, 52, 68, 69, 70]);

  // W12/S20--S21 and W13/S46--S47 are the terminal reverse legs before FSP.
  // Put their short tracks outside the two running lines.  The branch right
  // endpoint is 48px horizontally and vertically from the frog right end,
  // which keeps both yellow turnout legs exactly 45 degrees.
  const s20Frog = positions.get(20);
  const s46Frog = positions.get(46);
  const s21Branch = byId.get(21);
  const s47Branch = byId.get(47);
  const s314Branch = byId.get(314);
  if (s20Frog && s21Branch) positions.set(21, {
    segment: s21Branch,
    // W12 is the left-up turnout: S21's right endpoint sits 48px left of
    // the S20 frog right endpoint, so the yellow branch is a true 45° leg.
    x: s20Frog.x - 48,
    y: MAIN_Y.up - 48,
  });
  const s21Position = positions.get(21);
  if (s21Position && s314Branch) positions.set(314, {
    segment: s314Branch,
    x: s21Position.x - SEG_WIDTH - 8,
    y: s21Position.y,
  });
  if (s46Frog && s47Branch) positions.set(47, {
    segment: s47Branch,
    // W13 is the matching left-down turnout.
    x: s46Frog.x - 48,
    y: MAIN_Y.down + 48,
  });

  // Complete the three remaining one-sided display links in the GGZ throat.
  // S5 is W1's normal approach, S33 is W3's reverse leg, and S231 feeds
  // the S1 depot lead.  The straight links keep an 8px interval; the W3
  // leg rises by 24px and reaches the frog's right end with a true 45°.
  const s6Main = positions.get(6);
  const s32Frog = positions.get(32);
  const s1Lead = positions.get(1);
  const s5Approach = byId.get(5);
  const s33Branch = byId.get(33);
  const s231Entry = byId.get(231);
  if (s6Main && s5Approach) positions.set(5, {
    segment: s5Approach,
    x: s6Main.x - SEG_WIDTH - 8,
    y: MAIN_Y.up,
  });
  if (s1Lead && s231Entry) positions.set(231, {
    segment: s231Entry,
    x: s1Lead.x - SEG_WIDTH - 8,
    y: s1Lead.y,
  });
  const s53Depot = positions.get(53);
  const s231Position = positions.get(231);
  const s232Entry = byId.get(232);
  const s233Entry = byId.get(233);
  const s234Entry = byId.get(234);
  if (s53Depot && s232Entry) positions.set(232, {
    segment: s232Entry,
    x: s53Depot.x - SEG_WIDTH - 8,
    y: s53Depot.y,
  });
  if (s231Position && s233Entry) positions.set(233, {
    segment: s233Entry,
    x: s231Position.x - SEG_WIDTH - 8,
    y: s231Position.y,
  });

  // W2 feeds the W3 return through S30.  W3 leaves S32 to the lower-left,
  // then S30--S33 remains horizontal.  The S29/S238 main spur occupies the
  // upper short lane, allowing W2 itself to enter S30 by a left-down 45°
  // leg without touching the lower running line.
  const s30Return = byId.get(30);
  const s29Frog = byId.get(29);
  const s238Normal = byId.get(238);
  const outerDepotIds = [53, 26, 27, 28] as const;
  if (s32Frog && s33Branch && s30Return && s29Frog && s238Normal) {
    const s32Right = s32Frog.x + SEG_WIDTH;
    const w2Rise = 48;
    const branchY = s32Frog.y + 48;
    const s29MainY = branchY + w2Rise;
    const w3Rise = branchY - s32Frog.y;
    const s33X = s32Right - w3Rise - SEG_WIDTH;
    const s30X = s33X - SEG_WIDTH - 8;
    // W2 runs from S29's frog to S30's left endpoint: right-up 45 degrees.
    const s29X = s30X - w2Rise - SEG_WIDTH;
    positions.set(33, { segment: s33Branch, x: s33X, y: branchY });
    positions.set(30, { segment: s30Return, x: s30X, y: branchY });
    positions.set(29, { segment: s29Frog, x: s29X, y: s29MainY });
    positions.set(238, { segment: s238Normal, x: s29X + SEG_WIDTH + 8, y: s29MainY });
      outerDepotIds.forEach((id, index) => {
        const segment = byId.get(id);
        if (segment) positions.set(id, {
          segment,
          x: s29X - (outerDepotIds.length - index) * (SEG_WIDTH + 8),
          y: s29MainY,
        });
      });
    const s53Position = positions.get(53);
    if (s53Position && s232Entry) positions.set(232, {
      segment: s232Entry,
      x: s53Position.x - SEG_WIDTH - 8,
      y: s53Position.y,
    });
    const s232Position = positions.get(232);
    if (s232Position && s234Entry) positions.set(234, {
      segment: s234Entry,
      x: s232Position.x - SEG_WIDTH - 8,
      y: s232Position.y,
    });
    // Excel's upstream through route into S234. Keep this entirely straight
    // and deliberately omit the S249/S256 turnout branches for this pass.
    const s234Position = positions.get(234);
    const upstreamTrunkRightToLeft = [250, 251, 253, 254, 257, 265, 267, 285, 286, 287, 288, 294, 300, 318] as const;
    if (s234Position) upstreamTrunkRightToLeft.forEach((id, index) => {
      const segment = byId.get(id);
      if (segment) positions.set(id, {
        segment,
        x: s234Position.x - (index + 1) * (SEG_WIDTH + 8),
        y: s234Position.y,
      });
    });

    // Add the six reverse legs attached to the newly restored trunk.  Each
    // pair gets a short straight continuation, while the three depth lanes
    // keep the 45-degree yellow turnout legs separate from one another.
    const placeReverseLeg = (
      frogId: number,
      branchId: number,
      continuationId: number,
      depth: number,
      continuationSide: 'left' | 'right',
    ) => {
      const frog = positions.get(frogId);
      const branch = byId.get(branchId);
      const continuation = byId.get(continuationId);
      if (!frog || !branch || !continuation) return;
      const laneY = frog.y + depth;
      const branchRight = frog.x + SEG_WIDTH - depth;
      const branchX = branchRight - SEG_WIDTH;
      positions.set(branchId, { segment: branch, x: branchX, y: laneY });
      positions.set(continuationId, {
        segment: continuation,
        x: continuationSide === 'left' ? branchX - SEG_WIDTH - 8 : branchX + SEG_WIDTH + 8,
        y: laneY,
      });
    };
    const normal45_52Y = MAIN_Y.down + 172;
    // W46 must land on S249's left endpoint, not its right endpoint.
    const s251Frog = positions.get(251);
    const s249Branch = byId.get(249);
    const s247Continuation = byId.get(247);
    if (s251Frog && s249Branch && s247Continuation) {
      const laneY = normal45_52Y + 76;
      const s249X = s251Frog.x + SEG_WIDTH - (laneY - s251Frog.y);
      positions.set(249, { segment: s249Branch, x: s249X, y: laneY });
      positions.set(247, { segment: s247Continuation, x: s249X + SEG_WIDTH + 8, y: laneY });
    }
    placeReverseLeg(253, 255, 259, 60, 'left');
    placeReverseLeg(265, 266, 268, 192, 'left');

    // Keep W57/W58 compact and above the new trunk: each yellow leg is the
    // same 34px pitch as a Seg. W58 is explicitly a left-up turnout.
    const placeShortUpperLeg = (frogId: number, branchId: number, continuationId: number) => {
      const frog = positions.get(frogId);
      const branch = byId.get(branchId);
      const continuation = byId.get(continuationId);
      if (!frog || !branch || !continuation) return;
      const pitch = SEG_WIDTH;
      const y = frog.y - pitch;
      const branchRight = frog.x + SEG_WIDTH - pitch;
      const branchX = branchRight - SEG_WIDTH;
      positions.set(branchId, { segment: branch, x: branchX, y });
      positions.set(continuationId, { segment: continuation, x: branchX - SEG_WIDTH - 8, y });
    };
    placeShortUpperLeg(285, 290, 296);
    placeShortUpperLeg(287, 289, 295);

    // W50 uses its own shallow lower-left lane, so the W49 leg can return
    // cleanly to the upper S263--S260--S261 normal rail.
    const s257Frog = positions.get(257);
    const s256Branch = byId.get(256);
    const s258Continuation = byId.get(258);
    if (s257Frog && s256Branch && s258Continuation) {
      const y = s257Frog.y + 35;
      const s256Right = s257Frog.x + SEG_WIDTH - 35;
      const s256X = s256Right - SEG_WIDTH;
      positions.set(256, { segment: s256Branch, x: s256X, y });
      positions.set(258, { segment: s258Continuation, x: s256X + SEG_WIDTH + 8, y });
    }

    // The W52/W45 normal path is one continuous straight chain. Its two
    // frogs sit at the ends; S269--S252--S248 are the normal Segs between.
    const s247Position = positions.get(247);
    const s246Entry = byId.get(246);
    const s248Entry = byId.get(248);
    const s252Entry = byId.get(252);
    const s269Entry = byId.get(269);
    const s270Entry = byId.get(270);
    if (s247Position && s246Entry && s248Entry && s252Entry && s269Entry && s270Entry) {
      const s246Right = s247Position.x + SEG_WIDTH - (s247Position.y - normal45_52Y);
      const s246X = s246Right - SEG_WIDTH;
      const step = SEG_WIDTH + 8;
      positions.set(246, { segment: s246Entry, x: s246X, y: normal45_52Y });
      positions.set(248, { segment: s248Entry, x: s246X - step, y: normal45_52Y });
      positions.set(252, { segment: s252Entry, x: s246X - step * 2, y: normal45_52Y });
      positions.set(269, { segment: s269Entry, x: s246X - step * 3, y: normal45_52Y });
      positions.set(270, { segment: s270Entry, x: s246X - step * 4, y: normal45_52Y });
    }

    // W48 and W49 share S260 as their normal Seg. Keeping S263--S260--
    // S261--S262 on one rail prevents the two reverse legs from crossing.
    const s259Position = positions.get(259);
    const s263Entry = byId.get(263);
    const s260Entry = byId.get(260);
    const s261Entry = byId.get(261);
    const s262Entry = byId.get(262);
    const normal48_49Y = MAIN_Y.down + 24;
    if (s259Position && s263Entry && s260Entry && s261Entry && s262Entry) {
      const s263Right = s259Position.x - (s259Position.y - normal48_49Y);
      const s263X = s263Right - SEG_WIDTH;
      const step = SEG_WIDTH + 8;
      positions.set(263, { segment: s263Entry, x: s263X, y: normal48_49Y });
      positions.set(260, { segment: s260Entry, x: s263X + step, y: normal48_49Y });
      positions.set(261, { segment: s261Entry, x: s263X + step * 2, y: normal48_49Y });
      positions.set(262, { segment: s262Entry, x: s263X + step * 3, y: normal48_49Y });
    }

    // S259--S255 is one straight upper short track between S263 and S267.
    // Rebuild the adjacent W48/W49/W50 geometry around that fact, instead
    // of leaving either gray continuation diagonal.
    const s253Frog = positions.get(253);
    const s259Branch = byId.get(259);
    if (s253Frog && s259Branch && s256Branch && s258Continuation && s263Entry && s260Entry && s261Entry && s262Entry) {
      const railY = MAIN_Y.down + 24;
      const shortTrackY = MAIN_Y.down + 76;
      const step = SEG_WIDTH + 8;
      const s255Right = s253Frog.x + SEG_WIDTH - (s253Frog.y - shortTrackY);
      const s255X = s255Right - SEG_WIDTH;
      const s259X = s255X - step;
      const s263Right = s259X - (shortTrackY - railY);
      const s263X = s263Right - SEG_WIDTH;
      positions.set(259, { segment: s259Branch, x: s259X, y: shortTrackY });
      const s255Position = positions.get(255);
      if (s255Position) positions.set(255, { ...s255Position, x: s255X, y: shortTrackY });
      positions.set(263, { segment: s263Entry, x: s263X, y: railY });
      positions.set(260, { segment: s260Entry, x: s263X + step, y: railY });
      positions.set(261, { segment: s261Entry, x: s263X + step * 2, y: railY });
      positions.set(262, { segment: s262Entry, x: s263X + step * 3, y: railY });

      const s261Right = s263Right + step * 2;
      const s256Y = railY + 25;
      const s258X = s261Right - (s256Y - railY);
      positions.set(256, { segment: s256Branch, x: s258X - step, y: s256Y });
      positions.set(258, { segment: s258Continuation, x: s258X, y: s256Y });
    }

    // Excel's actual leftward through chain starts at S1.  Keep S1 itself
    // fixed, then lay every forward predecessor in this exact order on one
    // straight, 8px-spaced rail.  Switch branches are arranged from these
    // frog coordinates separately; this block never changes the pointers.
    const s1Anchor = positions.get(1);
    const s1LeftMainline = [231, 233, 239, 240, 246, 248, 252, 269, 270, 271, 273, 282, 291, 297, 315] as const;
    if (s1Anchor) {
      s1LeftMainline.forEach((id, index) => {
        const segment = byId.get(id);
        if (!segment) return;
        positions.set(id, {
          segment,
          x: s1Anchor.x - (index + 1) * (SEG_WIDTH + 8),
          y: s1Anchor.y,
        });
      });
    }

    // S246 is immediately to the left of S240 in the Excel forward chain.
    // Open that local interval by translating the complete left-hand
    // continuation, so S240 remains fixed and every grey main-line join
    // retains its original horizontal order.
    const s240To246Clearance = 32;
    ([246, 248, 252, 269, 270, 271, 273, 282, 291, 297, 315] as const).forEach((id) => {
      const positioned = positions.get(id);
      if (positioned) positions.set(id, { ...positioned, x: positioned.x - s240To246Clearance });
    });

    // S250 is the fixed right-hand datum for this secondary trunk. Open its
    // left-hand clearance at S251, then carry the complete upstream chain
    // with it so no part of that main line is left behind.
    // W46 is specified as a right-up 45° leg.  Keep S250 fixed and open a
    // visibly larger clearance on its left so the frog S251 can remain the
    // right-up leg's origin without crowding the adjacent running rail.
    // The same 16px continuation translation is applied to the paired lower
    // trunk so W46/W51 remain exactly 45° after the S240--S246 clearance.
    const s250LeftClearance = 64;
    const leftOfS250 = [251, 253, 254, 257, 265, 267, 285, 286, 287, 288, 294, 300, 318] as const;
    leftOfS250.forEach((id) => {
      const positioned = positions.get(id);
      if (positioned) positions.set(id, { ...positioned, x: positioned.x - s250LeftClearance });
    });
    // W57/W58 move with their frogs; their compact upper short tracks must
    // therefore translate by the same amount before the later branch reflow.
    ([289, 290, 295, 296] as const).forEach((id) => {
      const positioned = positions.get(id);
      if (positioned) positions.set(id, { ...positioned, x: positioned.x - s250LeftClearance });
    });

    // W52's frog is S270 (not S276).  Pull the complete upstream chain left
    // far enough to place W52's reverse leg at left-up 45° while preserving
    // the existing left-up direction of W51.  S269 stays the fixed datum.
    const s269LeftClearance = 478;
    ([270, 271, 273, 282, 291, 297, 315] as const).forEach((id) => {
      const positioned = positions.get(id);
      if (positioned) positions.set(id, { ...positioned, x: positioned.x - s269LeftClearance });
    });

    // Lift the two reverse short tracks into the space above their lower
    // feeders.  Each pair remains horizontal, while its two yellow legs form
    // the intended clean X at exact 45 degrees:
    //   W45--S247/S249--W46 and W52--S268/S266--W51.
    // W45/W46/W51/W52 are a four-corner crossover.  Both short rails sit
    // on the centre row between S315 and S318, with S268--S266 on the left.
    const s240Anchor = positions.get(240);
    const s315Anchor = positions.get(315);
    const s318Anchor = positions.get(318);
    const coreIds = [
      246, 247, 248, 249, 251, 252, 253, 254, 257,
      265, 266, 268, 269, 270,
    ] as const;
    const coreSegments = new Map(coreIds.map((id) => [id, byId.get(id)]));
    if (
      s240Anchor && s315Anchor && s318Anchor
      && [...coreSegments.values()].every((segment) => segment)
    ) {
      const topY = s315Anchor.y;
      const bottomY = s318Anchor.y;
      const middleY = (topY + bottomY) / 2;
      const riseFromTop = middleY - topY;
      const riseFromBottom = bottomY - middleY;
      const shortPairStep = SEG_WIDTH + 8;
      const pairGap = 16;
      const s240To246Gap = 64;
      const s246Right = s240Anchor.x - s240To246Gap;
      const s249X = s246Right - riseFromTop - shortPairStep * 2;
      const s268X = s249X - pairGap - shortPairStep * 2;
      const s270Right = s268X - riseFromTop;
      const s251Right = s249X - riseFromBottom;
      const s265Right = s268X + shortPairStep * 2 + riseFromBottom;

      const placeRightEndpointChain = (ids: readonly (typeof coreIds[number])[], firstRight: number, lastRight: number, y: number) => {
        const step = (lastRight - firstRight) / (ids.length - 1);
        ids.forEach((id, index) => {
          const segment = coreSegments.get(id);
          if (segment) positions.set(id, { segment, x: firstRight + step * index - SEG_WIDTH, y });
        });
      };
      placeRightEndpointChain([246, 248, 252, 269, 270], s246Right, s270Right, topY);
      placeRightEndpointChain([251, 253, 254, 257, 265], s251Right, s265Right, bottomY);
      positions.set(268, { segment: coreSegments.get(268)!, x: s268X, y: middleY });
      positions.set(266, { segment: coreSegments.get(266)!, x: s268X + shortPairStep, y: middleY });
      positions.set(249, { segment: coreSegments.get(249)!, x: s249X, y: middleY });
      positions.set(247, { segment: coreSegments.get(247)!, x: s249X + shortPairStep, y: middleY });
    }

    // Turn W47 toward S255's *right* endpoint after the crossover core has
    // been laid out.  The direct S251 spine moves as a group, then W45/W46's
    // two short tracks are rebuilt from their actual frog endpoints.
    const s253FrogForTurnoutFlip = positions.get(253);
    const s251ForTurnoutFlip = positions.get(251);
    const s246FrogForTurnoutFlip = positions.get(246);
    const s247TrackForTurnoutFlip = byId.get(247);
    const s249TrackForTurnoutFlip = byId.get(249);
    if (
      s253FrogForTurnoutFlip && s251ForTurnoutFlip && s246FrogForTurnoutFlip
      && s247TrackForTurnoutFlip && s249TrackForTurnoutFlip
    ) {
      const step = SEG_WIDTH + 8;
      const s253Shift = SEG_WIDTH + step * 2;
      const movedS253 = { ...s253FrogForTurnoutFlip, x: s253FrogForTurnoutFlip.x + s253Shift };
      positions.set(253, movedS253);

      // Leave one complete column between S253's right frog endpoint and
      // S251. S250/S234/S232/S53 are its direct rightward spine, so they
      // move together instead of producing overlaps or bent grey links.
      const s251TargetX = movedS253.x + SEG_WIDTH + step;
      const rightSpineShift = s251TargetX - s251ForTurnoutFlip.x;
      ([251, 250, 234, 232, 53] as const).forEach((id) => {
        const positioned = positions.get(id);
        if (positioned) positions.set(id, { ...positioned, x: positioned.x + rightSpineShift });
      });

      const movedS251 = positions.get(251)!;
      const shortTrackY = (
        (movedS251.x + SEG_WIDTH)
        - (s246FrogForTurnoutFlip.x + SEG_WIDTH)
        + SEG_WIDTH * 2 + 8
        + s246FrogForTurnoutFlip.y + movedS251.y
      ) / 2;
      const s247Right = s246FrogForTurnoutFlip.x + SEG_WIDTH + (shortTrackY - s246FrogForTurnoutFlip.y);
      const s247X = s247Right - SEG_WIDTH;
      positions.set(247, { segment: s247TrackForTurnoutFlip, x: s247X, y: shortTrackY });
      positions.set(249, { segment: s249TrackForTurnoutFlip, x: s247X - SEG_WIDTH - 8, y: shortTrackY });
    }

    const s270Frog = positions.get(270);
    const s265FrogForUpperTrack = positions.get(265);
    const s268Track = byId.get(268);
    const s266Track = byId.get(266);
    if (s270Frog && s265FrogForUpperTrack && s268Track && s266Track) {
      // With the new S270--S269 clearance, one Seg-height rise makes W52
      // exactly left-up 45°.  The paired S266 endpoint then remains on W51's
      // original left-up 45° ray; the short S268--S266 chain stays horizontal.
      const y = s270Frog.y - SEG_WIDTH;
      const s268X = s270Frog.x + SEG_WIDTH - (s270Frog.y - y);
      positions.set(268, { segment: s268Track, x: s268X, y });
      positions.set(266, { segment: s266Track, x: s268X + SEG_WIDTH + 8, y });
    }

    // Lay this throat out as four ordered rows: S265, S259/S255,
    // S256/S258, then S263/S260/S261/S262.  The two middle short tracks
    // share their left and right columns.  This makes the grey continuations
    // horizontal while the yellow W48/W49/W50 legs use their real endpoints.
    const s257FrogForLowerLanes = positions.get(257);
    const s253FrogForLowerLanes = positions.get(253);
    const s255LowerTrack = byId.get(255);
    const s259LowerTrack = byId.get(259);
    if (
      s253FrogForLowerLanes && s257FrogForLowerLanes && s265FrogForUpperTrack
      && s255LowerTrack && s259LowerTrack
      && s256Branch && s258Continuation
      && s263Entry && s260Entry && s261Entry && s262Entry
    ) {
      const step = SEG_WIDTH + 8;
      // A row is one complete Seg plus its standard clearance.  W47 is
      // stored S255 -> S253, so its yellow leg uses S255's right endpoint.
      const rowPitch = step;
      const s259Y = s265FrogForUpperTrack.y + rowPitch;
      const s256Y = s259Y + rowPitch;
      const s263Y = s256Y + rowPitch;

      // W47 leaves S253's right frog endpoint down-left to S255's right
      // endpoint.  Its two middle tracks keep the requested column alignment.
      const s255X = s253FrogForLowerLanes.x - rowPitch;
      const s259X = s255X - step;
      const s256X = s259X;
      positions.set(259, { segment: s259LowerTrack, x: s259X, y: s259Y });
      positions.set(255, { segment: s255LowerTrack, x: s255X, y: s259Y });
      positions.set(256, { segment: s256Branch, x: s256X, y: s256Y });
      positions.set(258, { segment: s258Continuation, x: s256X + step, y: s256Y });

      // W48 reaches S259 from S263's right frog endpoint.  S263--S260 keeps
      // the requested 8px clearance.  W49's frog moves right until its right
      // endpoint sends a left-up 45-degree leg to S258's right endpoint.
      const s263Right = s259X - (s263Y - s259Y);
      const s263X = s263Right - SEG_WIDTH;
      const s260X = s263Right + 8;
      const s261Right = s256X + step + SEG_WIDTH + (s263Y - s256Y);
      const s261X = s261Right - SEG_WIDTH;
      positions.set(263, { segment: s263Entry, x: s263X, y: s263Y });
      positions.set(260, { segment: s260Entry, x: s260X, y: s263Y });
      positions.set(261, { segment: s261Entry, x: s261X, y: s263Y });
      positions.set(262, { segment: s262Entry, x: s261X + step, y: s263Y });
    }
  }

  // Map the ordered lower chain S194--S195--S217--S218--S219--S220--S221--
  // S222 onto the upper BQS/GTG columns.  This retains its Excel sequence
  // while making S217/S204 and S220/S207 true vertical station pairs.
  const s204 = positions.get(204);
  const s205 = positions.get(205);
  const s206 = positions.get(206);
  const s207 = positions.get(207);
  const s208 = positions.get(208);
  const s209 = positions.get(209);
  if (s204 && s205 && s206 && s207 && s208 && s209) {
    const step = SEG_WIDTH + 18;
    setStraightSlot(217, s204.x);
    setStraightSlot(195, s204.x - step);
    setStraightSlot(194, s204.x - step * 2);
    const s192 = positions.get(192);
    if (s192) setStraightSlot(194, s192.x + SEG_WIDTH + 6);
    setStraightSlot(218, s205.x);
    setStraightSlot(219, s206.x);
    setStraightSlot(220, s207.x);
    setStraightSlot(221, s208.x);
    setStraightSlot(222, s209.x);
    const s217 = positions.get(217);
    const s218 = positions.get(218);
    if (s217 && s218) {
      const rightGap = s218.x - (s217.x + SEG_WIDTH);
      setStraightSlot(195, s217.x - SEG_WIDTH - rightGap);
    }
  }

  // BDZ down platform: S184--[S185]--S186 is one station chain, so the
  // outgoing S186 occupies the immediately adjacent right-hand column.
  const bdzDownPlatform = positions.get(185);
  if (bdzDownPlatform) setStraightSlot(186, bdzDownPlatform.x + SEG_WIDTH + 6);

  // FTN up platform: retain the direct S179--[S180]--S181 station chain.
  const ftnUpPlatform = positions.get(180);
  if (ftnUpPlatform) setStraightSlot(179, ftnUpPlatform.x - SEG_WIDTH - 6);

  // Apply the reviewed GGZ geometry last. Earlier rules still provide the
  // generic/full-line layout, but none of them may overwrite this audited
  // local throat after the V2-to-runtime migration.
  for (const [id, x, y] of GGZ_VERIFIED_THROAT_POSITIONS) {
    const segment = byId.get(id);
    if (segment) positions.set(id, { segment, x, y });
  }

  const positioned = [...positions.values()];
  const xValues = positioned.map((item) => item.x);
  const yValues = positioned.map((item) => item.y);
  const translateY = Math.max(0, 32 - Math.min(...yValues));
  // Include the real left-most yard Seg.  The old width was measured from
  // zero to max-x, which silently cropped negative local yard coordinates.
  const canvasX = Math.min(0, Math.min(...xValues)) - 80;
  const canvasY = Math.min(0, Math.min(...yValues) + translateY) - 28;
  const canvasRight = Math.max(840, Math.max(...xValues) + SEG_WIDTH + 80);
  const canvasBottom = Math.max(310, Math.max(...yValues) + translateY + 82);
  const stationMarks = fullLineStationAnchors.flatMap((station) => {
    // Use the platform pair as the station's visual datum.  Averaging all
    // in/out Segs becomes wrong when a one-direction throat inserts a real
    // clearance column before the platform (for example JBG S236/S170).
    const anchorIds = station.platformSegmentIds.length ? station.platformSegmentIds : station.segmentIds;
    const xs = anchorIds.map((id) => positions.get(id)?.x).filter((x): x is number => x != null);
    return xs.length ? [{ code: station.code, name: station.name, x: average(xs) + SEG_WIDTH / 2 }] : [];
  });
  const depotSegments = [...GGZ_DEPOT_SEGMENT_IDS]
    .map((id) => positions.get(id))
    .filter((item): item is PositionedSegment => item != null);
  const depotLabel = SHOW_GGZ_DEPOT_BRANCH && depotSegments.length ? {
    x: average(depotSegments.map((item) => item.x + SEG_WIDTH / 2)),
    y: Math.min(...depotSegments.map((item) => item.y)) - 18,
  } : null;
  const depotCamera = SHOW_GGZ_DEPOT_BRANCH && depotSegments.length ? (() => {
    const minX = Math.min(...depotSegments.map((item) => item.x));
    const maxX = Math.max(...depotSegments.map((item) => item.x + SEG_WIDTH));
    const minY = Math.min(...depotSegments.map((item) => item.y)) + translateY;
    const maxY = Math.max(...depotSegments.map((item) => item.y)) + translateY;
    const width = Math.max(520, maxX - minX + 210);
    const height = Math.max(400, maxY - minY + 150);
    return {
      x: minX - 92,
      y: minY - 68,
      width,
      height,
    } satisfies Camera;
  })() : null;
  const displayEdges = edges.filter(isGgzThroatDisplayEdge);
  return {
    edges: displayEdges,
    positions,
    switchByPair,
    crossings: findVisualCrossings(displayEdges, positions, switchByPair, frogByPair),
    frogByPair,
    stationMarks,
    canvasX,
    canvasY,
    width: canvasRight - canvasX,
    height: canvasBottom - canvasY,
    translateY,
    depotLabel,
    depotCamera,
  };
}

function edgeEndpoints(source: PositionedSegment, target: PositionedSegment, targetIsReverseFrog = false) {
  const attachToFrogRight = [
    [PLANAR_CROSSOVER_SEGMENTS.upperBranch, PLANAR_CROSSOVER_SEGMENTS.upperSwitch],
    [117, 118], [110, 109], [114, 113], [95, 94],
    // W24 is S131; its reverse S132 leg ends at the same drawn frog endpoint.
    [132, 131], [143, 142],
    [212, 211], [226, 225],
    [156, 155], [167, 166],
    // W33/W36 are stored from their reverse-leg Segs.  Their yellow legs
    // therefore need the frog's drawn right endpoint rather than its left.
    [196, 197], [193, 192],
    // W12/W13 have reciprocal reverse pointers in the imported topology.
    // Both directions must therefore attach to the branch right end so they
    // overlay one physical 45-degree turnout leg.
    [20, 21], [46, 47],
    // W3's reverse leg meets S33's right end. W2 deliberately is not listed:
    // it meets S30's left end, exactly as in the physical throat.
    [32, 33],
  ].some(([sourceId, targetId]) => source.segment.id === sourceId && target.segment.id === targetId);
  return {
    sourceX: source.x + SEG_WIDTH,
    targetX: targetIsReverseFrog || attachToFrogRight ? target.x + SEG_WIDTH : target.x,
  };
}

function edgePath(source: PositionedSegment, target: PositionedSegment, targetIsReverseFrog = false) {
  const { sourceX, targetX } = edgeEndpoints(source, target, targetIsReverseFrog);
  return `M ${sourceX} ${source.y} L ${targetX} ${target.y}`;
}

function roleText(segmentId: number, isTurnout: boolean) {
  const semantic = fullLineSemantics.get(segmentId);
  const base = semantic ? `主线 ${semantic.lane === 'up' ? 'A' : 'B'}` : '支线 / 车库 / 折返候选';
  return isTurnout ? `${base} · 道岔岔心` : base;
}

export default function OptimizedTopologyView() {
  const [topology, setTopology] = useState<TopologyData | null>(null);
  const [liveState, setLiveState] = useState<LiveTopologyState | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [trainActionError, setTrainActionError] = useState<string | null>(null);
  const [addingDirection, setAddingDirection] = useState<'UP' | 'DOWN' | null>(null);
  const [camera, setCamera] = useState<Camera | null>(null);
  const svgRef = useRef<SVGSVGElement | null>(null);
  const dragRef = useRef<{ pointerId: number; clientX: number; clientY: number; camera: Camera } | null>(null);
  const didPanRef = useRef(false);

  useEffect(() => {
    let active = true;
    fetch('/api/phase2/member-c/static-routes')
      .then((response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json() as Promise<TopologyData>;
      })
      .then((data) => {
        if (!active) return;
        setTopology(data);
        setSelectedId(data.segments[0]?.id ?? null);
      })
      .catch((error: unknown) => {
        if (active) setLoadError(error instanceof Error ? error.message : '无法加载拓扑数据');
      });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    let active = true;
    let timer: number | undefined;
    const refresh = async () => {
      try {
        const response = await fetch('/api/sim/topology-state');
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const state = await response.json() as LiveTopologyState;
        if (active) {
          setLiveState(state);
          setTrainActionError((previous) => previous?.startsWith('列车状态读取失败：') ? null : previous);
        }
      } catch (error) {
        if (active) setTrainActionError(error instanceof Error ? `列车状态读取失败：${error.message}` : '列车状态读取失败');
      } finally {
        if (active) timer = window.setTimeout(refresh, 400);
      }
    };
    void refresh();
    return () => { active = false; if (timer !== undefined) window.clearTimeout(timer); };
  }, []);

  const layout = useMemo(() => topology ? buildLayout(topology) : null, [topology, LAYOUT_REVISION]);
  useEffect(() => {
    if (layout) setCamera(fitCamera(layout));
  }, [layout]);
  const selected = topology?.segments.find((segment) => segment.id === selectedId) ?? null;
  const switchByFrog = useMemo(() => new Map((topology?.switches ?? []).filter((sw) => sw.frogSeg != null).map((sw) => [sw.frogSeg!, sw])), [topology]);
  const signalsBySeg = useMemo(() => {
    const result = new Map<number, TopologyData['signals']>();
    for (const signal of topology?.signals ?? []) (result.get(signal.segId) ?? result.set(signal.segId, []).get(signal.segId)!).push(signal);
    return result;
  }, [topology]);
  const trainsBySegment = useMemo(() => {
    const result = new Map<number, Array<LiveTopologyState['trains'][number]>>();
    for (const train of liveState?.trains ?? []) {
      if (train.segId == null) continue;
      (result.get(train.segId) ?? result.set(train.segId, []).get(train.segId)!).push(train);
    }
    return result;
  }, [liveState]);

  const zoom = (factor: number) => {
    if (!camera || !layout) return;
    const nextWidth = Math.min(layout.width * 2.4, Math.max(180, camera.width * factor));
    const nextHeight = Math.min(layout.height * 2.4, Math.max(120, camera.height * factor));
    setCamera(clampCamera({
      x: camera.x + (camera.width - nextWidth) / 2,
      y: camera.y + (camera.height - nextHeight) / 2,
      width: nextWidth,
      height: nextHeight,
    }, layout));
  };

  const onPointerDown = (event: ReactPointerEvent<SVGSVGElement>) => {
    if (!camera) return;
    dragRef.current = { pointerId: event.pointerId, clientX: event.clientX, clientY: event.clientY, camera };
    didPanRef.current = false;
    event.currentTarget.setPointerCapture(event.pointerId);
  };

  const onPointerMove = (event: ReactPointerEvent<SVGSVGElement>) => {
    const drag = dragRef.current;
    const svg = svgRef.current;
    if (!drag || drag.pointerId !== event.pointerId || !svg || !layout) return;
    const rect = svg.getBoundingClientRect();
    const dx = event.clientX - drag.clientX;
    const dy = event.clientY - drag.clientY;
    if (Math.abs(dx) > 2 || Math.abs(dy) > 2) didPanRef.current = true;
    setCamera(clampCamera({
      ...drag.camera,
      x: drag.camera.x - dx * drag.camera.width / rect.width,
      y: drag.camera.y - dy * drag.camera.height / rect.height,
    }, layout));
  };

  const onPointerUp = (event: ReactPointerEvent<SVGSVGElement>) => {
    if (dragRef.current?.pointerId === event.pointerId) dragRef.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
  };

  const onWheel = (event: ReactWheelEvent<SVGSVGElement>) => {
    if (!camera || !layout) return;
    event.preventDefault();
    const rect = event.currentTarget.getBoundingClientRect();
    const ratioX = (event.clientX - rect.left) / rect.width;
    const ratioY = (event.clientY - rect.top) / rect.height;
    const factor = event.deltaY < 0 ? 0.8 : 1.25;
    const width = Math.min(layout.width * 2.4, Math.max(180, camera.width * factor));
    const height = Math.min(layout.height * 2.4, Math.max(120, camera.height * factor));
    setCamera(clampCamera({
      x: camera.x + (camera.width - width) * ratioX,
      y: camera.y + (camera.height - height) * ratioY,
      width,
      height,
    }, layout));
  };

  if (loadError) return <div className="flex h-full items-center justify-center text-sm text-[#ff8c82]">拓扑优化视图加载失败：{loadError}</div>;
  if (!topology || !layout) return <div className="flex h-full items-center justify-center text-sm text-[#7f96a9]">正在构建全线对齐拓扑…</div>;
  const activeCamera = camera ?? fitCamera(layout);

  const selectedSwitch = selected ? switchByFrog.get(selected.id) : undefined;
  const selectedStart = liveState?.startOptions.find((option) => option.segmentId === selectedId);
  const addEngineTrain = async (direction: 'UP' | 'DOWN') => {
    if (!selectedStart || selectedId == null) return;
    setAddingDirection(direction);
    setTrainActionError(null);
    try {
      const trainId = `T-TOPO-${Date.now().toString().slice(-6)}`;
      const response = await fetch('/api/sim/train/add', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ trainId, initialStationCode: selectedStart.stationCode, initialSegmentId: selectedId, direction, operationMode: 'ATO' }),
      });
      const result = await response.json() as { ok?: boolean; error?: string };
      if (!result.ok) throw new Error(result.error ?? '加车失败');
      const action = liveState?.clockState === 'PAUSED' ? '/api/sim/resume' : '/api/sim/start';
      await fetch(action, { method: 'POST' });
    } catch (error) {
      setTrainActionError(error instanceof Error ? error.message : '加车失败');
    } finally {
      setAddingDirection(null);
    }
  };

  return (
    <section className="flex h-full min-h-0 gap-3 overflow-hidden p-3" style={{ background: '#040810', color: '#d7e0eb' }}>
      <div className="flex min-w-0 flex-1 flex-col overflow-hidden rounded border border-[#18293a] bg-[#040810]">
        <header className="flex shrink-0 items-center justify-between gap-4 border-b border-[#172436] px-4 py-3">
          <div>
            <p className="text-[10px] uppercase tracking-[0.16em] text-[#6f859b]">Full-line aligned topology</p>
            <h2 className="mt-0.5 text-sm font-semibold text-[#e4edf6]">Seg 拓扑优化图</h2>
          </div>
          <div className="flex items-center gap-3 text-[10px] text-[#91a7ba]">
            <span><i className="mr-1 inline-block h-px w-3 bg-[#6e8599] align-middle" />原始正向连接</span>
            <span><i className="mr-1 inline-block h-px w-3 bg-[#d29922] align-middle" />原始道岔反位连接</span>
            <span><i className="mr-1 inline-block h-2 w-2 rounded-full border border-[#70869a] align-middle" />非连接跨线</span>
            <span>{layout.edges.length} 条连接 · {topology.segments.length} Seg</span>
          </div>
        </header>
        <div className="relative min-h-0 flex-1 overflow-hidden" style={{ backgroundImage: 'radial-gradient(rgba(104,133,157,.10) 1px, transparent 1px)', backgroundSize: '18px 18px' }}>
          <div className="absolute right-3 top-3 z-10 flex items-center gap-1 rounded border border-[#29445e] bg-[#08111c]/90 p-1 shadow-lg">
            <button type="button" onClick={() => zoom(1.25)} className="rounded px-2 py-1 text-xs text-[#b9dcff] hover:bg-[#15304a]" aria-label="缩小拓扑图">−</button>
            <button type="button" onClick={() => setCamera(fitCamera(layout))} className="rounded border-x border-[#29445e] px-2 py-1 text-[10px] text-[#b9dcff] hover:bg-[#15304a]">全图</button>
            {layout.depotCamera && <button type="button" onClick={() => setCamera(layout.depotCamera!)} className="rounded border-r border-[#29445e] px-2 py-1 text-[10px] text-[#d6bd79] hover:bg-[#15304a]">车辆段</button>}
            <button type="button" onClick={() => zoom(0.8)} className="rounded px-2 py-1 text-xs text-[#b9dcff] hover:bg-[#15304a]" aria-label="放大拓扑图">＋</button>
          </div>
          <svg
            ref={svgRef}
            width="100%"
            height="100%"
            viewBox={`${activeCamera.x} ${activeCamera.y} ${activeCamera.width} ${activeCamera.height}`}
            preserveAspectRatio="xMidYMid meet"
            role="img"
            aria-label="按全线主线、支线和道岔语义重新排布的 Seg 联锁拓扑图"
            onPointerDown={onPointerDown}
            onPointerMove={onPointerMove}
            onPointerUp={onPointerUp}
            onPointerCancel={onPointerUp}
            onWheel={onWheel}
            style={{ cursor: dragRef.current ? 'grabbing' : 'grab', touchAction: 'none' }}
          >
            <g transform={`translate(0 ${layout.translateY})`}>
              {layout.stationMarks.map((station) => <g key={station.code} opacity="0.75">
                <line x1={station.x} y1="54" x2={station.x} y2="187" stroke="#2a4054" strokeWidth="0.8" strokeDasharray="2 4" />
                <text x={station.x} y="46" textAnchor="middle" fill="#83a0b7" fontSize="8" fontFamily="monospace"><title>{station.name}</title>{station.code}</text>
              </g>)}
              {layout.depotLabel && <g opacity="0.9">
                <text x={layout.depotLabel.x} y={layout.depotLabel.y} textAnchor="middle" fill="#91a7ba" fontSize="9" fontFamily="sans-serif">郭公庄车辆段 · 库线群 / 咽喉 / 出入段线</text>
                <line x1={layout.depotLabel.x - 98} x2={layout.depotLabel.x + 98} y1={layout.depotLabel.y + 5} y2={layout.depotLabel.y + 5} stroke="#314960" strokeWidth="0.8" />
              </g>}
              {layout.edges.map((edge) => {
                const source = layout.positions.get(edge.source);
                const target = layout.positions.get(edge.target);
                if (!source || !target) return null;
                const switchPosition = layout.switchByPair.get(pairKey(edge.source, edge.target));
                // The switch catalogue is authoritative.  An endDiverging
                // pointer alone is a topology alternative, not sufficient to
                // draw a physical yellow turnout leg.
                const diverging = switchPosition === 'reverse';
                const targetIsReverseFrog = diverging && layout.frogByPair.get(pairKey(edge.source, edge.target)) === edge.target;
                return <path key={`${edge.source}-${edge.target}-${edge.kind}`} d={edgePath(source, target, targetIsReverseFrog)} fill="none" stroke={diverging ? '#d29922' : '#496178'} strokeWidth={diverging ? 1.8 : 1.15} opacity={diverging ? 0.95 : 0.65} />;
              })}
              {layout.crossings.map((crossing, index) => <g key={`crossing-${index}`} aria-label="非连接跨线">
                <circle cx={crossing.x} cy={crossing.y} r="3.2" fill="#040810" stroke="#70869a" strokeWidth="0.8" />
                <path d={`M ${crossing.x - 1.5} ${crossing.y + 1.5} L ${crossing.x + 1.5} ${crossing.y - 1.5}`} stroke="#8fa8bc" strokeWidth="0.7" />
              </g>)}
              {[...layout.positions.values()].filter((item) => isGgzThroatVisibleSegment(item.segment.id)).map((item) => {
                const isSelected = item.segment.id === selectedId;
                const hasPlatform = item.segment.platformIds.length > 0;
                const segmentTrains = trainsBySegment.get(item.segment.id) ?? [];
                const sw = switchByFrog.get(item.segment.id);
                const signals = signalsBySeg.get(item.segment.id) ?? [];
                // The W11/W13 pocket has reverse-facing signals below the
                // running line.  Keep their Seg labels on a second baseline
                // so signal symbols and neighbouring labels remain legible.
                const labelY = [43, 45, 46, 48, 49].includes(item.segment.id) ? item.y + 25 : item.y + 13;
                return <g key={item.segment.id} onClick={() => { if (didPanRef.current) { didPanRef.current = false; return; } setSelectedId(item.segment.id); }} role="button" tabIndex={0} aria-label={`选择 Seg ${item.segment.id}`} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); setSelectedId(item.segment.id); } }} style={{ cursor: 'pointer' }}>
                  {hasPlatform && <rect x={item.x} y={item.y - 5} width={SEG_WIDTH} height="10" fill="rgba(143,195,31,0.32)" />}
                  <line x1={item.x} y1={item.y} x2={item.x + SEG_WIDTH} y2={item.y} stroke={isSelected ? '#f0c040' : '#6e8599'} strokeWidth={isSelected ? 5 : 2.4} />
                  <circle cx={item.x} cy={item.y} r="1.8" fill={isSelected ? '#b9dcff' : '#91a7ba'} />
                  <circle cx={item.x + SEG_WIDTH} cy={item.y} r="1.8" fill={isSelected ? '#b9dcff' : '#91a7ba'} />
                  <text x={item.x + SEG_WIDTH / 2} y={labelY} textAnchor="middle" fill={isSelected ? '#d7ebff' : '#7f96a9'} fontSize="8" fontFamily="monospace">S{item.segment.id}</text>
                  {sw && <><circle cx={item.x + SEG_WIDTH} cy={item.y} r="4" fill="#d29922" /><text x={item.x + SEG_WIDTH + 5} y={item.y - 6} fill="#f0c040" fontSize="7" fontFamily="monospace">W{sw.id}</text></>}
                  {signals.map((signal, index) => {
                    const reverse = signal.direction?.toLowerCase() === '0xaa';
                    const ratio = Math.max(0.08, Math.min(0.92, (signal.offsetM ?? item.segment.lengthM / 2) / Math.max(item.segment.lengthM, 1)));
                    const x = item.x + SEG_WIDTH * ratio;
                    const side = reverse ? 1 : -1;
                    const y = item.y + side * (12 + index * 8);
                    return <g key={signal.id}><line x1={x} y1={item.y} x2={x} y2={y - side * 3} stroke="#3a4a5a" strokeWidth="0.6" /><circle cx={x} cy={y} r="2.5" fill="#ef5c5c" opacity="0.9" /><path d={reverse ? `M ${x - 7} ${y} L ${x - 2} ${y - 3.5} L ${x - 2} ${y + 3.5} Z` : `M ${x + 7} ${y} L ${x + 2} ${y - 3.5} L ${x + 2} ${y + 3.5} Z`} fill="#8094a8" /></g>;
                  })}
                  {segmentTrains.length > 0 && <><rect x={item.x + 7} y={item.y - 10} width="20" height="7" rx="1" fill={segmentTrains[0].color}><title>{segmentTrains[0].id}</title></rect><text x={item.x + 17} y={item.y - 4.5} textAnchor="middle" fill="#ffffff" fontSize="6" fontFamily="monospace">T</text></>}
                </g>;
              })}
            </g>
          </svg>
        </div>
      </div>

      <aside className="w-64 shrink-0 overflow-y-auto rounded border border-[#18293a] bg-[#08111c] p-4">
        <p className="text-[10px] uppercase tracking-[0.14em] text-[#6f859b]">Selected segment</p>
        <h3 className="mt-1 font-mono text-xl text-[#f1f6fa]">{selected ? `S${selected.id}` : '—'}</h3>
        <p className="mt-1 text-[11px] text-[#9ab1c4]">{selected ? roleText(selected.id, Boolean(selectedSwitch)) : '未选择 Seg'}</p>
        <dl className="mt-5 space-y-3 text-[11px]">
          <div><dt className="text-[#6f859b]">长度</dt><dd className="mt-0.5 text-[#d7e0eb]">{selected?.lengthM.toFixed(1) ?? '—'} m</dd></div>
          <div><dt className="text-[#6f859b]">正向连接</dt><dd className="mt-0.5 font-mono text-[#b7c8d8]">{selected?.endForward == null ? '无' : `S${selected.endForward}`}</dd></div>
          <div><dt className="text-[#d7a42b]">道岔分支</dt><dd className="mt-0.5 font-mono text-[#efc65c]">{selected?.endDiverging == null ? '无' : `S${selected.endDiverging}`}</dd></div>
          <div><dt className="text-[#6f859b]">站台</dt><dd className="mt-0.5 text-[#9ad8b7]">{selected?.platformIds.length ? selected.platformIds.join('、') : '无'}</dd></div>
        </dl>
        {selectedStart && <div className="mt-5 border-t border-[#1a2a3a] pt-4">
          <p className="text-[10px] text-[#6f859b]">{selectedStart.stationName} · 原联锁模拟加车</p>
          <div className="mt-2 flex gap-2">
            {selectedStart.directions.map((direction) => <button key={direction} type="button" onClick={() => { void addEngineTrain(direction); }} disabled={addingDirection !== null} className="rounded border border-[#2b4a6b] px-2 py-1 text-[10px] text-[#b9dcff] disabled:opacity-50">{addingDirection === direction ? '加车中…' : `发 ${direction} ATO`}</button>)}
          </div>
        </div>}
        {liveState?.trains.length ? <div className="mt-4 text-[10px] leading-5 text-[#9ab1c4]">实时列车：{liveState.trains.map((train) => `${train.id} · S${train.segId ?? '-'} · ${train.directionCode}`).join('\n')}</div> : null}
        {trainActionError && <p className="mt-3 text-[10px] text-[#ff8c82]">{trainActionError}</p>}
        <div className="mt-6 border-t border-[#1a2a3a] pt-4 text-[10px] leading-5 text-[#70869a]">
          此视图只重排 Seg 坐标：每条原始 endForward/endDiverging 连接都会完整绘制。黄色由原联锁的 frog/norm/rev 道岔目录判定；列车位置直接读取主引擎发布的当前 Seg。
        </div>
      </aside>
    </section>
  );
}
