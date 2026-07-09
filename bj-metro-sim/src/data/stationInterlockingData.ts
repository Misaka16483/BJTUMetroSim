import type { StationInterlockingData } from '../types/interlocking';

/**
 * 统一联锁图布局：
 * 左=郭公庄（南），右=国家图书馆（北）
 * 上行（up）从左向右，下行（down）从右向左
 */

/** 北京西站 (BWR) */
export function bwrInterlockingData(): StationInterlockingData {
  const Y_UP = 140, Y_DN = 220;
  const X0 = 240, X1 = 400, W = 640;
  return {
    stationId: 'BWR', stationName: '北京西站', stationCode: 'BWR', lineId: '9', bounds: { width: W, height: 320 },
    tracks: [
      { id: 'up-in', label: 'seg 128 (100m)', y: Y_UP, x: 30, width: X0 - 30, dir: 'up', segmentIds: [128] },
      { id: 'up-plat', y: Y_UP, x: X0, width: X1 - X0, dir: 'up', segmentIds: [129] },
      { id: 'up-out', label: 'seg 130 (158m)', y: Y_UP, x: X1, width: W - 30 - X1, dir: 'up', segmentIds: [130] },
      { id: 'dn-in', label: 'seg 138 (942m)', y: Y_DN, x: 30, width: X0 - 30, dir: 'down', segmentIds: [138] },
      { id: 'dn-plat', y: Y_DN, x: X0, width: X1 - X0, dir: 'down', segmentIds: [139] },
      { id: 'dn-out', label: 'seg 140 (100m)', y: Y_DN, x: X1, width: W - 30 - X1, dir: 'down', segmentIds: [140] },
    ],
    platforms: [
      { id: 'P17', name: '上行站台', trackId: 'up-plat', x: (X0 + X1) / 2, width: X1 - X0 - 20, mileageM: 10598.74, segmentIds: [129], direction: '0xaa' },
      { id: 'P18', name: '下行站台', trackId: 'dn-plat', x: (X0 + X1) / 2, width: X1 - X0 - 20, mileageM: 10598.74, segmentIds: [139], direction: '0x55' },
    ],
    signals: [
      { id: 30, name: 'XC', type: 1, trackId: 'up-plat', x: X0 - 8, dir: 'up' },
      { id: 102, name: 'Z1', type: 1, trackId: 'up-plat', x: X1 + 8, dir: 'up' },
      { id: 103, name: 'XC', type: 1, trackId: 'dn-plat', x: X1 + 8, dir: 'down' },
      { id: 79, name: 'SC', type: 3, trackId: 'dn-plat', x: X0 - 8, dir: 'down' },
    ],
    switches: [
      { id: 'cw-left', x: X0 - 60, y1: Y_UP, y2: Y_DN, type: 'crossover', trackId1: 'up-in', trackId2: 'dn-in' },
      { id: 'cw-right', x: X1 + 60, y1: Y_UP, y2: Y_DN, type: 'crossover', trackId1: 'up-out', trackId2: 'dn-out' },
    ],
    routes: [{ id: 63, name: 'F1→XC', startSignalId: 31, endSignalId: 30, path: [{ x: 30, y: Y_UP - 35 }, { x: (X0 + X1) / 2, y: Y_UP - 35 }], color: 'rgba(88,166,255,0.6)' }],
    labels: [
      { id: 'title', x: (X0 + X1) / 2, y: Y_UP - 65, text: '北京西站', fontSize: 14, color: '#dce8f8', align: 'center', font: 'sans-serif' },
      { id: 'code', x: (X0 + X1) / 2, y: Y_UP - 48, text: 'BWR · K10+598', fontSize: 10, color: '#6a7a90', align: 'center' },
      { id: 'seg-128', x: 35, y: Y_UP + 28, text: 'seg 128 (100m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'seg-129', x: X0 + 5, y: Y_UP + 28, text: 'seg 129 (129m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'seg-130', x: X1 + 10, y: Y_UP + 28, text: 'seg 130 (158m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'seg-138', x: 35, y: Y_DN + 28, text: 'seg 138 (942m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'seg-139', x: X0 + 5, y: Y_DN + 28, text: 'seg 139 (129m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'seg-140', x: X1 + 10, y: Y_DN + 28, text: 'seg 140 (100m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
    ],
    directionLabels: { up: '← 郭公庄', down: '国家图书馆 →' },
  };
}

/** 国家图书馆 (GTG) */
export function gtgInterlockingData(): StationInterlockingData {
  const Y_UP = 140, Y_DN = 220, Y_RET = 290;
  const X0 = 170, X1 = 310, SW_R = 400, END_X = 490, W = 540;
  return {
    stationId: 'GTG', stationName: '国家图书馆', stationCode: 'GTG', lineId: '9', bounds: { width: W, height: 360 },
    tracks: [
      { id: 'up-in', label: 'seg 206 (100m)', y: Y_UP, x: 30, width: X0 - 30, dir: 'up', segmentIds: [206] },
      { id: 'up-plat', y: Y_UP, x: X0, width: X1 - X0, dir: 'up', segmentIds: [207] },
      { id: 'up-end', y: Y_UP, x: X1, width: W - 30 - X1, dir: 'up', segmentIds: [208, 209, 211, 213, 214, 215] },
      { id: 'dn-in', label: 'seg 219 (867m)', y: Y_DN, x: 30, width: X0 - 30, dir: 'down', segmentIds: [219] },
      { id: 'dn-plat', y: Y_DN, x: X0, width: X1 - X0, dir: 'down', segmentIds: [220] },
      { id: 'dn-end', y: Y_DN, x: X1, width: W - 30 - X1, dir: 'down', segmentIds: [221, 222, 223, 225, 227, 228, 229] },
      { id: 'ret-1', y: Y_RET, x: SW_R + 10, width: END_X - SW_R - 10, dir: 'up', segmentIds: [210, 226] },
      { id: 'ret-2', y: Y_RET, x: SW_R + 10, width: END_X - SW_R - 10, dir: 'down', segmentIds: [224, 212] },
      { id: 'ret-end-up', y: Y_RET, x: END_X, width: 24, dir: 'up', segmentIds: [216] },
      { id: 'ret-end-dn', y: Y_RET, x: END_X, width: 24, dir: 'down', segmentIds: [230] },
    ],
    platforms: [
      { id: 'P25', name: '上行站台', trackId: 'up-plat', x: (X0 + X1) / 2, width: X1 - X0 - 20, mileageM: 16048.92, segmentIds: [207], direction: '0x55' },
      { id: 'P26', name: '下行站台', trackId: 'dn-plat', x: (X0 + X1) / 2, width: X1 - X0 - 20, mileageM: 16048.92, segmentIds: [220], direction: '0xaa' },
    ],
    signals: [
      { id: 46, name: 'XC', type: 1, trackId: 'up-plat', x: X0 - 8, dir: 'up' },
      { id: 47, name: 'F1', type: 3, trackId: 'up-plat', x: X1 + 8, dir: 'up' },
      { id: 94, name: 'Z2', type: 1, trackId: 'dn-plat', x: X0 - 8, dir: 'down' },
      { id: 95, name: 'SC', type: 3, trackId: 'dn-plat', x: X1 + 8, dir: 'down' },
    ],
    switches: [
      { id: 'cw-left', x: X0 - 50, y1: Y_UP, y2: Y_DN, type: 'crossover', trackId1: 'up-in', trackId2: 'dn-in' },
      { id: 'sw-ret-up', x: SW_R, y1: Y_UP, y2: Y_RET, type: 'turnout', trackId1: 'up-end', trackId2: 'ret-1' },
      { id: 'sw-ret-dn', x: SW_R, y1: Y_DN, y2: Y_RET, type: 'turnout', trackId1: 'dn-end', trackId2: 'ret-2' },
    ],
    routes: [{ id: 62, name: 'XC→XC', startSignalId: 30, endSignalId: 29, path: [{ x: 30, y: Y_UP - 35 }, { x: (X0 + X1) / 2, y: Y_UP - 35 }], color: 'rgba(88,166,255,0.6)' }],
    labels: [
      { id: 'title', x: (X0 + X1) / 2, y: Y_UP - 65, text: '国家图书馆', fontSize: 14, color: '#dce8f8', align: 'center', font: 'sans-serif' },
      { id: 'code', x: (X0 + X1) / 2, y: Y_UP - 48, text: 'GTG · K16+048', fontSize: 10, color: '#6a7a90', align: 'center' },
      { id: 'seg-206', x: 35, y: Y_UP + 28, text: 'seg 206 (100m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'seg-207', x: X0 + 5, y: Y_UP + 28, text: 'seg 207 (129m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'seg-219', x: 35, y: Y_DN + 28, text: 'seg 219 (867m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'seg-220', x: X0 + 5, y: Y_DN + 28, text: 'seg 220 (129m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'end-up', x: END_X + 12, y: Y_UP - 10, text: '■ 车挡', fontSize: 8, color: '#4a5a6a', align: 'right' },
      { id: 'end-dn', x: END_X + 12, y: Y_DN - 10, text: '■ 车挡', fontSize: 8, color: '#4a5a6a', align: 'right' },
      { id: 'ret-label', x: SW_R + 50, y: Y_RET + 16, text: '折返线', fontSize: 8, color: '#3a4a5a', align: 'center' },
    ],
    directionLabels: { up: '← 郭公庄', down: '终点（折返）' },
  };
}

/** 郭公庄 (GGZ) */
export function ggzInterlockingData(): StationInterlockingData {
  const Y_UP = 140, Y_DN = 220, Y_DEPOT = 290;
  const X0 = 210, X1 = 350, SW_R = 430, DEPOT_X = 80, W = 580;
  return {
    stationId: 'GGZ', stationName: '郭公庄', stationCode: 'GGZ', lineId: '9', bounds: { width: W, height: 360 },
    tracks: [
      { id: 'up-in', label: 'seg 1 (758m)', y: Y_UP, x: 30, width: X0 - 30, dir: 'up', segmentIds: [1] },
      { id: 'up-plat', y: Y_UP, x: X0, width: X1 - X0, dir: 'up', segmentIds: [13] },
      { id: 'up-out', label: 'seg 22 (190m)', y: Y_UP, x: X1, width: W - 30 - X1, dir: 'up', segmentIds: [22] },
      { id: 'dn-in', label: 'seg 48 (190m)', y: Y_DN, x: 30, width: X0 - 30, dir: 'down', segmentIds: [48] },
      { id: 'dn-plat', y: Y_DN, x: X0, width: X1 - X0, dir: 'down', segmentIds: [39] },
      { id: 'dn-out', label: 'seg 31 (220m)', y: Y_DN, x: X1, width: W - 30 - X1, dir: 'down', segmentIds: [31] },
      { id: 'depot-up', y: Y_DEPOT, x: DEPOT_X, width: X0 - DEPOT_X, dir: 'up', segmentIds: [231] },
      { id: 'depot-dn', y: Y_DEPOT, x: DEPOT_X, width: X0 - DEPOT_X, dir: 'down', segmentIds: [232] },
    ],
    platforms: [
      { id: 'P1', name: '上行站台', trackId: 'up-plat', x: (X0 + X1) / 2, width: X1 - X0 - 20, mileageM: 313.0, segmentIds: [13], direction: '0x55' },
      { id: 'P2', name: '下行站台', trackId: 'dn-plat', x: (X0 + X1) / 2, width: X1 - X0 - 20, mileageM: 313.0, segmentIds: [39], direction: '0xaa' },
    ],
    signals: [
      { id: 3, name: 'XQ1', type: 1, trackId: 'up-plat', x: X0 - 8, dir: 'up' },
      { id: 8, name: 'XC1', type: 3, trackId: 'up-plat', x: X0 - 8, dir: 'up' },
      { id: 57, name: 'F6', type: 3, trackId: 'dn-plat', x: X0 - 8, dir: 'down' },
      { id: 58, name: 'SC2', type: 3, trackId: 'dn-plat', x: X1 + 8, dir: 'down' },
      { id: 98, name: 'JC1', type: 3, trackId: 'depot-up', x: DEPOT_X + 50, dir: 'up' },
      { id: 99, name: 'JC2', type: 3, trackId: 'depot-dn', x: DEPOT_X + 50, dir: 'down' },
    ],
    switches: [
      { id: 'cw-left', x: X0 - 50, y1: Y_UP, y2: Y_DN, type: 'crossover', trackId1: 'up-in', trackId2: 'dn-in' },
      { id: 'cw-right', x: SW_R, y1: Y_UP, y2: Y_DN, type: 'crossover', trackId1: 'up-out', trackId2: 'dn-out' },
      { id: 'depot-up-conn', x: X0, y1: Y_UP, y2: Y_DEPOT, type: 'turnout', trackId1: 'up-in', trackId2: 'depot-up' },
      { id: 'depot-dn-conn', x: X0, y1: Y_DN, y2: Y_DEPOT, type: 'turnout', trackId1: 'dn-out', trackId2: 'depot-dn' },
    ],
    routes: [{ id: 8, name: 'XC1→XQ1', startSignalId: 8, endSignalId: 3, path: [{ x: 30, y: Y_UP - 35 }, { x: (X0 + X1) / 2, y: Y_UP - 35 }], color: 'rgba(88,166,255,0.6)' }],
    labels: [
      { id: 'title', x: (X0 + X1) / 2, y: Y_UP - 65, text: '郭公庄', fontSize: 14, color: '#dce8f8', align: 'center', font: 'sans-serif' },
      { id: 'code', x: (X0 + X1) / 2, y: Y_UP - 48, text: 'GGZ · K0+313', fontSize: 10, color: '#6a7a90', align: 'center' },
      { id: 'seg-1', x: 35, y: Y_UP + 28, text: 'seg 1 (758m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'seg-13', x: X0 + 5, y: Y_UP + 28, text: 'seg 13 (129m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'seg-22', x: X1 + 10, y: Y_UP + 28, text: 'seg 22 (190m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'seg-48', x: 35, y: Y_DN + 28, text: 'seg 48 (190m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'seg-39', x: X0 + 5, y: Y_DN + 28, text: 'seg 39 (129m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'seg-31', x: X1 + 10, y: Y_DN + 28, text: 'seg 31 (220m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'depot', x: DEPOT_X + 50, y: Y_DEPOT + 16, text: '车辆段', fontSize: 8, color: '#3a4a5a', align: 'center' },
    ],
    directionLabels: { up: '丰台科技园 →', down: '← 车辆段（起点）' },
  };
}

/** 丰台科技园 (FSP) */
export function fspInterlockingData(): StationInterlockingData {
  const Y_UP = 140, Y_DN = 220;
  const X0 = 210, X1 = 350, W = 580;
  return {
    stationId: 'FSP', stationName: '丰台科技园', stationCode: 'FSP', lineId: '9', bounds: { width: W, height: 320 },
    tracks: [
      { id: 'up-in', label: 'seg 23 (854m)', y: Y_UP, x: 30, width: X0 - 30, dir: 'up', segmentIds: [23] },
      { id: 'up-plat', y: Y_UP, x: X0, width: X1 - X0, dir: 'up', segmentIds: [24] },
      { id: 'up-out', label: 'seg 25 (560m)', y: Y_UP, x: X1, width: W - 30 - X1, dir: 'up', segmentIds: [25] },
      { id: 'dn-in', label: 'seg 50 (850m)', y: Y_DN, x: 30, width: X0 - 30, dir: 'down', segmentIds: [50] },
      { id: 'dn-plat', y: Y_DN, x: X0, width: X1 - X0, dir: 'down', segmentIds: [51] },
      { id: 'dn-out', label: 'seg 52 (100m)', y: Y_DN, x: X1, width: W - 30 - X1, dir: 'down', segmentIds: [52] },
    ],
    platforms: [
      { id: 'P3', name: '上行站台', trackId: 'up-plat', x: (X0 + X1) / 2, width: X1 - X0 - 20, mileageM: 1660.52, segmentIds: [24], direction: '0xaa' },
      { id: 'P4', name: '下行站台', trackId: 'dn-plat', x: (X0 + X1) / 2, width: X1 - X0 - 20, mileageM: 1660.52, segmentIds: [51], direction: '0x55' },
    ],
    signals: [
      { id: 13, name: 'XC3', type: 1, trackId: 'up-plat', x: X0 - 8, dir: 'up' },
      { id: 17, name: 'XC', type: 1, trackId: 'up-out', x: X1 + 20, dir: 'up' },
      { id: 62, name: 'SC4', type: 1, trackId: 'dn-plat', x: X1 + 8, dir: 'down' },
      { id: 14, name: 'XC', type: 1, trackId: 'dn-in', x: 35, dir: 'down' },
    ],
    switches: [
      { id: 'cw-left', x: X0 - 50, y1: Y_UP, y2: Y_DN, type: 'crossover', trackId1: 'up-in', trackId2: 'dn-in' },
      { id: 'cw-right', x: X1 + 60, y1: Y_UP, y2: Y_DN, type: 'crossover', trackId1: 'up-out', trackId2: 'dn-out' },
    ],
    routes: [{ id: 5, name: 'F3→SC2', startSignalId: 7, endSignalId: 58, path: [{ x: 30, y: Y_UP - 35 }, { x: (X0 + X1) / 2, y: Y_UP - 35 }], color: 'rgba(88,166,255,0.6)' }],
    labels: [
      { id: 'title', x: (X0 + X1) / 2, y: Y_UP - 65, text: '丰台科技园', fontSize: 14, color: '#dce8f8', align: 'center', font: 'sans-serif' },
      { id: 'code', x: (X0 + X1) / 2, y: Y_UP - 48, text: 'FSP · K1+660', fontSize: 10, color: '#6a7a90', align: 'center' },
      { id: 'seg-23', x: 35, y: Y_UP + 28, text: 'seg 23 (854m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'seg-24', x: X0 + 5, y: Y_UP + 28, text: 'seg 24 (129m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'seg-25', x: X1 + 10, y: Y_UP + 28, text: 'seg 25 (560m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'seg-50', x: 35, y: Y_DN + 28, text: 'seg 50 (850m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'seg-51', x: X0 + 5, y: Y_DN + 28, text: 'seg 51 (129m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'seg-52', x: X1 + 10, y: Y_DN + 28, text: 'seg 52 (100m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
    ],
    directionLabels: { up: '← 郭公庄', down: '国家图书馆 →' },
  };
}

/** 根据车站代码获取联锁图数据 */
export function getInterlockingData(stationCode: string): StationInterlockingData | null {
  switch (stationCode) {
    case 'BWR': return bwrInterlockingData();
    case 'GTG': return gtgInterlockingData();
    case 'GGZ': return ggzInterlockingData();
    case 'FSP': return fspInterlockingData();
    default: return null;
  }
}
