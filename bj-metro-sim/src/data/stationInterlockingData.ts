import type { StationInterlockingData } from '../types/interlocking';

const stationCatalog = [
  ['GGZ', '郭公庄', 313],
  ['FSP', '丰台科技园', 1660.52],
  ['KYL', '科怡路', 2448.61],
  ['FTN', '丰台南路', 3429.32],
  ['FTD', '丰台东大街', 5014.46],
  ['QLZ', '七里庄', 6339.90],
  ['LLQ', '六里桥', 8118.83],
  ['LLE', '六里桥东', 9429.16],
  ['BWR', '北京西站', 10598.74],
  ['JBG', '军事博物馆', 11996.97],
  ['BDZ', '白堆子', 13906.77],
  ['BQS', '白石桥南', 14954.01],
  ['GTG', '国家图书馆', 16048.92],
] as const;

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
  const Y_UP = 140, Y_DN = 220, Y_RET_UP = 290, Y_RET_DN = 350;
  const X0 = 170, X1 = 310, END_X = 500, W = 540;
  return {
    stationId: 'GTG', stationName: '国家图书馆', stationCode: 'GTG', lineId: '9', bounds: { width: W, height: 420 },
    tracks: [
      { id: 'up-in', label: 'seg 206 (100m)', y: Y_UP, x: 30, width: X0 - 30, dir: 'up', segmentIds: [206] },
      { id: 'up-plat', y: Y_UP, x: X0, width: X1 - X0, dir: 'up', segmentIds: [207] },
      { id: 'up-end', y: Y_UP, x: X1, width: W - 30 - X1, dir: 'up', segmentIds: [208, 209, 211, 213, 214, 215] },
      { id: 'dn-in', label: 'seg 219 (867m)', y: Y_DN, x: 30, width: X0 - 30, dir: 'down', segmentIds: [219] },
      { id: 'dn-plat', y: Y_DN, x: X0, width: X1 - X0, dir: 'down', segmentIds: [220] },
      { id: 'dn-end', y: Y_DN, x: X1, width: W - 30 - X1, dir: 'down', segmentIds: [221, 222, 223, 225, 227, 228, 229] },
      // 上行折返线：独立轨道层(Y_RET_UP)，接收上行折返道岔来车
      { id: 'ret-up', y: Y_RET_UP, x: X1 - 30, width: END_X - X1 + 30, dir: 'up', segmentIds: [210, 226] },
      // 下行折返线：独立轨道层(Y_RET_DN，比上行折返线更低)，物理分离
      { id: 'ret-dn', y: Y_RET_DN, x: X1 - 30, width: END_X - X1 + 30, dir: 'down', segmentIds: [224, 212] },
      // 上行折返车挡
      { id: 'ret-end-up', y: Y_RET_UP, x: END_X, width: 20, dir: 'up', segmentIds: [216] },
      // 下行折返车挡
      { id: 'ret-end-dn', y: Y_RET_DN, x: END_X, width: 20, dir: 'down', segmentIds: [230] },
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
      // 左端终点交叉渡线：标准交叉渡线连通seg206与seg219
      { id: 'cw-left', x: X0 - 50, y1: Y_UP, y2: Y_DN, type: 'crossover', trackId1: 'up-in', trackId2: 'dn-in' },
      // 上行折返道岔：起点在seg207右侧分界钢轨(=X1)，右移40px避开站台，独立接入上行折返线
      { id: 'sw-ret-up', x: X1 + 40, y1: Y_UP, y2: Y_RET_UP, type: 'turnout', trackId1: 'up-end', trackId2: 'ret-up' },
      // 下行折返道岔：起点在seg220右侧分界钢轨(=X1)，右移40px避开站台，独立接入下行折返线
      { id: 'sw-ret-dn', x: X1 + 40, y1: Y_DN, y2: Y_RET_DN, type: 'turnout', trackId1: 'dn-end', trackId2: 'ret-dn' },
    ],
    routes: [{ id: 62, name: 'XC→XC', startSignalId: 30, endSignalId: 29, path: [{ x: 30, y: Y_UP - 35 }, { x: (X0 + X1) / 2, y: Y_UP - 35 }], color: 'rgba(88,166,255,0.6)' }],
    labels: [
      { id: 'title', x: (X0 + X1) / 2, y: Y_UP - 65, text: '国家图书馆', fontSize: 14, color: '#dce8f8', align: 'center', font: 'sans-serif' },
      { id: 'code', x: (X0 + X1) / 2, y: Y_UP - 48, text: 'GTG · K16+048', fontSize: 10, color: '#6a7a90', align: 'center' },
      { id: 'seg-206', x: 35, y: Y_UP + 28, text: 'seg 206 (100m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'seg-207', x: X0 + 5, y: Y_UP + 28, text: 'seg 207 (129m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'seg-219', x: 35, y: Y_DN + 28, text: 'seg 219 (867m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'seg-220', x: X0 + 5, y: Y_DN + 28, text: 'seg 220 (129m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'ret-up-label', x: X1 + 40, y: Y_RET_UP + 16, text: '折返线(上行)', fontSize: 8, color: '#3a4a5a', align: 'center' },
      { id: 'ret-dn-label', x: X1 + 40, y: Y_RET_DN + 16, text: '折返线(下行)', fontSize: 8, color: '#3a4a5a', align: 'center' },
      { id: 'end-up', x: END_X + 12, y: Y_RET_UP - 10, text: '■ 车挡', fontSize: 8, color: '#4a5a6a', align: 'right' },
      { id: 'end-dn', x: END_X + 12, y: Y_RET_DN - 10, text: '■ 车挡', fontSize: 8, color: '#4a5a6a', align: 'right' },
    ],
    directionLabels: { up: '← 郭公庄', down: '终点（折返）' },
  };
}

/** 郭公庄 (GGZ) */
export function ggzInterlockingData(): StationInterlockingData {
  const Y_UP = 140, Y_DN = 220, Y_DEPOT = 290;
  const X0 = 210, X1 = 350, DEPOT_X = 80, W = 580;
  return {
    stationId: 'GGZ', stationName: '郭公庄', stationCode: 'GGZ', lineId: '9', bounds: { width: W, height: 360 },
    tracks: [
      { id: 'up-in', label: 'seg 1 (758m)', y: Y_UP, x: 30, width: X0 - 30, dir: 'up', segmentIds: [1] },
      { id: 'up-plat', y: Y_UP, x: X0, width: X1 - X0, dir: 'up', segmentIds: [13] },
      { id: 'up-out', label: 'seg 22 (190m)', y: Y_UP, x: X1, width: W - 30 - X1, dir: 'up', segmentIds: [22] },
      { id: 'dn-in', label: 'seg 48 (190m)', y: Y_DN, x: 30, width: X0 - 30, dir: 'down', segmentIds: [48] },
      { id: 'dn-plat', y: Y_DN, x: X0, width: X1 - X0, dir: 'down', segmentIds: [39] },
      { id: 'dn-out', label: 'seg 31 (220m)', y: Y_DN, x: X1, width: W - 30 - X1, dir: 'down', segmentIds: [31] },
      // 左侧车辆段上行线：depot-up-conn 斜线终点吸附在此（辙叉在下行正线seg48上）
      { id: 'depot-up', y: Y_DEPOT, x: DEPOT_X, width: 180, dir: 'up', segmentIds: [231] },
      // 右侧车辆段下行线：depot-dn-conn 斜线终点吸附（起点在seg39/seg31分界X1处）
      { id: 'depot-dn', y: Y_DEPOT, x: X1 - 20, width: 150, dir: 'down', segmentIds: [232] },
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
      { id: 99, name: 'JC2', type: 3, trackId: 'depot-dn', x: X1 - 10, dir: 'down' },
    ],
    switches: [
      // 左侧进站交叉渡线：标准双向连接seg1(上行)与seg48(下行)
      { id: 'cw-left', x: X0 - 40, y1: Y_UP, y2: Y_DN, type: 'crossover', trackId1: 'up-in', trackId2: 'dn-in' },
      // 左侧车辆段上行出入段道岔：辙叉搭接下行正线seg48，支线接入车辆段上行线
      { id: 'depot-up-conn', x: 190, y1: Y_DN, y2: Y_DEPOT, type: 'turnout', trackId1: 'dn-in', trackId2: 'depot-up' },
      // 右侧出站交叉渡线：标准双向连接上行seg22与下行seg31
      { id: 'cw-right', x: X1 + 55, y1: Y_UP, y2: Y_DN, type: 'crossover', trackId1: 'up-out', trackId2: 'dn-out' },
      // 右侧下行JC2出入段单渡线：右移至X1+20排除站台区域，向下接入seg232
      { id: 'depot-dn-conn', x: X1 + 20, y1: Y_DN, y2: Y_DEPOT, type: 'turnout', trackId1: 'dn-out', trackId2: 'depot-dn' },
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
      { id: 'depot', x: DEPOT_X + 50, y: Y_DEPOT + 16, text: '车辆段(上行)', fontSize: 8, color: '#3a4a5a', align: 'center' },
      { id: 'depot-dn-lbl', x: X1 + 50, y: Y_DEPOT + 16, text: '车辆段(下行)', fontSize: 8, color: '#3a4a5a', align: 'center' },
      { id: 'seg-232', x: X1 - 5, y: Y_DEPOT + 28, text: 'seg 232 (100m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
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

// ====== 以下 9 个站为根据 line_map.json 数据生成的联锁图 ======
// 每个车站显示：上下行双线 + 两个方向站台 + 信号机 + 道岔/交叉渡线

// 交叉渡线（crossover）= 上下行之间的对角连接桥
// 单开道岔（turnout）= 从主线一侧伸出的分叉

/* _addSwitches 工具（暂未使用，预留）
function _addSwitches(switches: { id: string; x: number; y1: number; y2: number; type: 'crossover' | 'turnout'; trackId1: string; trackId2: string }[],
  X0: number, X1: number, Y_UP: number, Y_DN: number,
  hasLeftCrossover: boolean, hasRightCrossover: boolean,
  hasLeftTurnoutUp?: boolean, hasLeftTurnoutDn?: boolean,
  hasRightTurnoutUp?: boolean, hasRightTurnoutDn?: boolean,
) {
  // 交叉渡线 → 站台两侧
  if (hasLeftCrossover) switches.push({ id: 'cw-left', x: X0 - 50, y1: Y_UP, y2: Y_DN, type: 'crossover', trackId1: 'up-in', trackId2: 'dn-in' });
  if (hasRightCrossover) switches.push({ id: 'cw-right', x: X1 + 60, y1: Y_UP, y2: Y_DN, type: 'crossover', trackId1: 'up-out', trackId2: 'dn-out' });
  // 单开道岔 → 从主线伸出一根斜线
  if (hasLeftTurnoutUp) switches.push({ id: 'to-left-up', x: X0 - 30, y1: Y_UP, y2: Y_UP + 50, type: 'turnout', trackId1: 'up-in', trackId2: 'no' });
  if (hasLeftTurnoutDn) switches.push({ id: 'to-left-dn', x: X0 - 30, y1: Y_DN, y2: Y_DN - 50, type: 'turnout', trackId1: 'dn-in', trackId2: 'no' });
  if (hasRightTurnoutUp) switches.push({ id: 'to-right-up', x: X1 + 30, y1: Y_UP, y2: Y_UP + 50, type: 'turnout', trackId1: 'up-out', trackId2: 'no' });
  if (hasRightTurnoutDn) switches.push({ id: 'to-right-dn', x: X1 + 30, y1: Y_DN, y2: Y_DN - 50, type: 'turnout', trackId1: 'dn-out', trackId2: 'no' });
}
void _addSwitches; */

// 科怡路 (KYL) — 无道岔中间站
export const kylInterlockingData = (): StationInterlockingData => {
  const Y_UP = 140, Y_DN = 220, X0 = 210, X1 = 350, W = 580;
  return {
    stationId: 'KYL', stationName: '科怡路', stationCode: 'KYL', lineId: '9', bounds: { width: W, height: 320 },
    tracks: [
      { id: 'up-in', label: 'seg 54 (854m)', y: Y_UP, x: 30, width: X0 - 30, dir: 'up', segmentIds: [54] },
      { id: 'up-plat', y: Y_UP, x: X0, width: X1 - X0, dir: 'up', segmentIds: [55] },
      { id: 'up-out', label: 'seg 56 (1196m)', y: Y_UP, x: X1, width: W - 30 - X1, dir: 'up', segmentIds: [56] },
      { id: 'dn-in', label: 'seg 68 (1196m)', y: Y_DN, x: 30, width: X0 - 30, dir: 'down', segmentIds: [68] },
      { id: 'dn-plat', y: Y_DN, x: X0, width: X1 - X0, dir: 'down', segmentIds: [69] },
      { id: 'dn-out', label: 'seg 70 (854m)', y: Y_DN, x: X1, width: W - 30 - X1, dir: 'down', segmentIds: [70] },
    ],
    platforms: [
      { id: 'P5', name: '上行站台', trackId: 'up-plat', x: (X0 + X1) / 2, width: X1 - X0 - 20, mileageM: 2448.61, segmentIds: [55], direction: '0xaa' },
      { id: 'P6', name: '下行站台', trackId: 'dn-plat', x: (X0 + X1) / 2, width: X1 - X0 - 20, mileageM: 2448.61, segmentIds: [69], direction: '0x55' },
    ],
    signals: [
      { id: 14, name: 'XC', type: 1, trackId: 'up-plat', x: X0 - 8, dir: 'up' },
      { id: 63, name: 'SC', type: 1, trackId: 'dn-plat', x: X1 + 8, dir: 'down' },
    ],
    switches: [],
    routes: [],
    labels: [
      { id: 'title', x: (X0 + X1) / 2, y: Y_UP - 65, text: '科怡路', fontSize: 14, color: '#dce8f8', align: 'center', font: 'sans-serif' },
      { id: 'code', x: (X0 + X1) / 2, y: Y_UP - 48, text: 'KYL · K2+448', fontSize: 10, color: '#6a7a90', align: 'center' },
      { id: 'seg-u1', x: 35, y: Y_UP + 28, text: 'seg 54 (854m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'seg-u2', x: X0 + 5, y: Y_UP + 28, text: 'seg 55 (129m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'seg-u3', x: X1 + 10, y: Y_UP + 28, text: 'seg 56 (1196m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'seg-d1', x: 35, y: Y_DN + 28, text: 'seg 68 (1196m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'seg-d2', x: X0 + 5, y: Y_DN + 28, text: 'seg 69 (129m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'seg-d3', x: X1 + 10, y: Y_DN + 28, text: 'seg 70 (854m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
    ],
    directionLabels: { up: '← 郭公庄', down: '国家图书馆 →' },
  };
};

// 丰台南路 (FTN) — 单开道岔 (seg 64: startDiv=65)
export const ftnInterlockingData = (): StationInterlockingData => {
  const Y_UP = 140, Y_DN = 220, X0 = 210, X1 = 350, W = 580;
  return {
    stationId: 'FTN', stationName: '丰台南路', stationCode: 'FTN', lineId: '9', bounds: { width: W, height: 320 },
    tracks: [
      { id: 'up-in', label: 'seg 57 (852m)', y: Y_UP, x: 30, width: X0 - 30, dir: 'up', segmentIds: [57] },
      { id: 'up-plat', y: Y_UP, x: X0, width: X1 - X0, dir: 'up', segmentIds: [58] },
      { id: 'up-out', label: 'seg 59 (1456m)', y: Y_UP, x: X1, width: W - 30 - X1, dir: 'up', segmentIds: [59] },
      { id: 'dn-in', label: 'seg 71 (1456m)', y: Y_DN, x: 30, width: X0 - 30, dir: 'down', segmentIds: [71] },
      { id: 'dn-plat', y: Y_DN, x: X0, width: X1 - X0, dir: 'down', segmentIds: [72] },
      { id: 'dn-out', label: 'seg 73 (852m)', y: Y_DN, x: X1, width: W - 30 - X1, dir: 'down', segmentIds: [73] },
    ],
    platforms: [
      { id: 'P7', name: '上行站台', trackId: 'up-plat', x: (X0 + X1) / 2, width: X1 - X0 - 20, mileageM: 3429.32, segmentIds: [58], direction: '0xaa' },
      { id: 'P8', name: '下行站台', trackId: 'dn-plat', x: (X0 + X1) / 2, width: X1 - X0 - 20, mileageM: 3429.32, segmentIds: [72], direction: '0x55' },
    ],
    signals: [
      { id: 15, name: 'XC', type: 1, trackId: 'up-plat', x: X0 - 8, dir: 'up' },
      { id: 64, name: 'SC', type: 1, trackId: 'dn-plat', x: X1 + 8, dir: 'down' },
    ],
    switches: [
      { id: 'cw-left', x: X0 - 50, y1: Y_UP, y2: Y_DN, type: 'crossover', trackId1: 'up-in', trackId2: 'dn-in' },
    ],
    routes: [],
    labels: [
      { id: 'title', x: (X0 + X1) / 2, y: Y_UP - 65, text: '丰台南路', fontSize: 14, color: '#dce8f8', align: 'center', font: 'sans-serif' },
      { id: 'code', x: (X0 + X1) / 2, y: Y_UP - 48, text: 'FTN · K3+429', fontSize: 10, color: '#6a7a90', align: 'center' },
      { id: 'seg-u1', x: 35, y: Y_UP + 28, text: 'seg 57 (852m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'seg-u2', x: X0 + 5, y: Y_UP + 28, text: 'seg 58 (129m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'seg-u3', x: X1 + 10, y: Y_UP + 28, text: 'seg 59 (1456m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'seg-d1', x: 35, y: Y_DN + 28, text: 'seg 71 (1456m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'seg-d2', x: X0 + 5, y: Y_DN + 28, text: 'seg 72 (129m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'seg-d3', x: X1 + 10, y: Y_DN + 28, text: 'seg 73 (852m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
    ],
    directionLabels: { up: '← 郭公庄', down: '国家图书馆 →' },
  };
};

// 丰台东大街 (FTD) — 单开道岔 (seg 64/78)
export const ftdInterlockingData = (): StationInterlockingData => {
  const Y_UP = 140, Y_DN = 220, Y_SID = 300, X0 = 210, X1 = 350, W = 580;
  return {
    stationId: 'FTD', stationName: '丰台东大街', stationCode: 'FTD', lineId: '9', bounds: { width: W, height: 320 },
    tracks: [
      { id: 'up-in', label: 'seg 61 (1196m)', y: Y_UP, x: 30, width: X0 - 30, dir: 'up', segmentIds: [61] },
      { id: 'up-plat', y: Y_UP, x: X0, width: X1 - X0, dir: 'up', segmentIds: [62] },
      { id: 'up-out', label: 'seg 63 (1456m)', y: Y_UP, x: X1, width: W - 30 - X1, dir: 'up', segmentIds: [63] },
      { id: 'dn-in', label: 'seg 76 (1456m)', y: Y_DN, x: 30, width: X0 - 30, dir: 'down', segmentIds: [76] },
      { id: 'dn-plat', y: Y_DN, x: X0, width: X1 - X0, dir: 'down', segmentIds: [77] },
      { id: 'dn-out', label: 'seg 78 (1196m)', y: Y_DN, x: X1, width: W - 30 - X1, dir: 'down', segmentIds: [78] },
      // 下行侧线：使 to-right-dn 道岔斜线终点能吸附到轨道上
      { id: 'sid-dn', y: Y_SID, x: X1 + 10, width: 80, dir: 'down', segmentIds: [] },
    ],
    platforms: [
      { id: 'P9', name: '上行站台', trackId: 'up-plat', x: (X0 + X1) / 2, width: X1 - X0 - 20, mileageM: 5014.46, segmentIds: [62], direction: '0xaa' },
      { id: 'P10', name: '下行站台', trackId: 'dn-plat', x: (X0 + X1) / 2, width: X1 - X0 - 20, mileageM: 5014.46, segmentIds: [77], direction: '0x55' },
    ],
    signals: [
      { id: 17, name: 'XC', type: 1, trackId: 'up-plat', x: X0 - 8, dir: 'up' },
      { id: 66, name: 'Z2', type: 1, trackId: 'dn-plat', x: X0 - 8, dir: 'down' },
      { id: 67, name: 'SC', type: 3, trackId: 'dn-plat', x: X1 + 8, dir: 'down' },
    ],
    switches: [
      // 单开道岔：下行→侧线，y2=Y_SID 使斜线终点吸附在 sid-dn 轨道上
      { id: 'to-right-dn', x: X1 + 30, y1: Y_DN, y2: Y_SID, type: 'turnout', trackId1: 'dn-out', trackId2: 'sid-dn' },
    ],
    routes: [],
    labels: [
      { id: 'title', x: (X0 + X1) / 2, y: Y_UP - 65, text: '丰台东大街', fontSize: 14, color: '#dce8f8', align: 'center', font: 'sans-serif' },
      { id: 'code', x: (X0 + X1) / 2, y: Y_UP - 48, text: 'FTD · K5+014', fontSize: 10, color: '#6a7a90', align: 'center' },
      { id: 'seg-u1', x: 35, y: Y_UP + 28, text: 'seg 61 (1196m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'seg-u2', x: X0 + 5, y: Y_UP + 28, text: 'seg 62 (129m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'seg-u3', x: X1 + 10, y: Y_UP + 28, text: 'seg 63 (1456m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'seg-d1', x: 35, y: Y_DN + 28, text: 'seg 76 (1456m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'seg-d2', x: X0 + 5, y: Y_DN + 28, text: 'seg 77 (129m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'seg-d3', x: X1 + 10, y: Y_DN + 28, text: 'seg 78 (1196m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'sid-dn-lbl', x: X1 + 35, y: Y_SID + 16, text: '侧线', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
    ],
    directionLabels: { up: '← 郭公庄', down: '国家图书馆 →' },
  };
};

// 七里庄 (QLZ) — 两个单开道岔 (seg 89/94)
export const qlzInterlockingData = (): StationInterlockingData => {
  const Y_UP = 140, Y_DN = 220, X0 = 210, X1 = 350, W = 580;
  return {
    stationId: 'QLZ', stationName: '七里庄', stationCode: 'QLZ', lineId: '9', bounds: { width: W, height: 320 },
    tracks: [
      { id: 'up-in', label: 'seg 83 (1650m)', y: Y_UP, x: 30, width: X0 - 30, dir: 'up', segmentIds: [83] },
      { id: 'up-plat', y: Y_UP, x: X0, width: X1 - X0, dir: 'up', segmentIds: [84] },
      { id: 'up-out', label: 'seg 85 (1181m)', y: Y_UP, x: X1, width: W - 30 - X1, dir: 'up', segmentIds: [85] },
      { id: 'dn-in', label: 'seg 97 (1181m)', y: Y_DN, x: 30, width: X0 - 30, dir: 'down', segmentIds: [97] },
      { id: 'dn-plat', y: Y_DN, x: X0, width: X1 - X0, dir: 'down', segmentIds: [98] },
      { id: 'dn-out', label: 'seg 99 (1650m)', y: Y_DN, x: X1, width: W - 30 - X1, dir: 'down', segmentIds: [99] },
    ],
    platforms: [
      { id: 'P11', name: '上行站台', trackId: 'up-plat', x: (X0 + X1) / 2, width: X1 - X0 - 20, mileageM: 6339.90, segmentIds: [84], direction: '0xaa' },
      { id: 'P12', name: '下行站台', trackId: 'dn-plat', x: (X0 + X1) / 2, width: X1 - X0 - 20, mileageM: 6339.90, segmentIds: [98], direction: '0x55' },
    ],
    signals: [
      { id: 21, name: 'XC', type: 1, trackId: 'up-plat', x: X0 - 8, dir: 'up' },
      { id: 69, name: 'SC', type: 1, trackId: 'dn-plat', x: X1 + 8, dir: 'down' },
    ],
    switches: [
      { id: 'cw-left', x: X0 - 55, y1: Y_UP, y2: Y_DN, type: 'crossover', trackId1: 'up-in', trackId2: 'dn-in' },
      { id: 'cw-right', x: X1 + 55, y1: Y_UP, y2: Y_DN, type: 'crossover', trackId1: 'up-out', trackId2: 'dn-out' },
    ],
    routes: [],
    labels: [
      { id: 'title', x: (X0 + X1) / 2, y: Y_UP - 65, text: '七里庄', fontSize: 14, color: '#dce8f8', align: 'center', font: 'sans-serif' },
      { id: 'code', x: (X0 + X1) / 2, y: Y_UP - 48, text: 'QLZ · K6+339', fontSize: 10, color: '#6a7a90', align: 'center' },
      { id: 'seg-u1', x: 35, y: Y_UP + 28, text: 'seg 83 (1650m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'seg-u2', x: X0 + 5, y: Y_UP + 28, text: 'seg 84 (129m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'seg-u3', x: X1 + 10, y: Y_UP + 28, text: 'seg 85 (1181m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'seg-d1', x: 35, y: Y_DN + 28, text: 'seg 97 (1181m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'seg-d2', x: X0 + 5, y: Y_DN + 28, text: 'seg 98 (129m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'seg-d3', x: X1 + 10, y: Y_DN + 28, text: 'seg 99 (1650m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
    ],
    directionLabels: { up: '← 郭公庄', down: '国家图书馆 →' },
  };
};

// 六里桥 (LLQ) — 两个单开道岔 (seg 89/94/105)
export const llqInterlockingData = (): StationInterlockingData => {
  const Y_UP = 140, Y_DN = 220, Y_SID = 300, X0 = 210, X1 = 350, W = 580;
  return {
    stationId: 'LLQ', stationName: '六里桥', stationCode: 'LLQ', lineId: '9', bounds: { width: W, height: 360 },
    tracks: [
      { id: 'up-in', label: 'seg 87 (1181m)', y: Y_UP, x: 30, width: X0 - 30, dir: 'up', segmentIds: [87] },
      { id: 'up-plat', y: Y_UP, x: X0, width: X1 - X0, dir: 'up', segmentIds: [88] },
      { id: 'up-out', label: 'seg 89 (1650m)', y: Y_UP, x: X1, width: W - 30 - X1, dir: 'up', segmentIds: [89] },
      { id: 'dn-in', label: 'seg 102 (1650m)', y: Y_DN, x: 30, width: X0 - 30, dir: 'down', segmentIds: [102] },
      { id: 'dn-plat', y: Y_DN, x: X0, width: X1 - X0, dir: 'down', segmentIds: [103] },
      { id: 'dn-out', label: 'seg 104 (1181m)', y: Y_DN, x: X1, width: W - 30 - X1, dir: 'down', segmentIds: [104] },
      // 上行侧线：延长覆盖道岔终点范围
      { id: 'sid-up', y: Y_SID, x: X1 - 10, width: 150, dir: 'up', segmentIds: [] },
    ],
    platforms: [
      { id: 'P13', name: '上行站台', trackId: 'up-plat', x: (X0 + X1) / 2, width: X1 - X0 - 20, mileageM: 8118.83, segmentIds: [88], direction: '0x55' },
      { id: 'P14', name: '下行站台', trackId: 'dn-plat', x: (X0 + X1) / 2, width: X1 - X0 - 20, mileageM: 8118.83, segmentIds: [103], direction: '0xaa' },
    ],
    signals: [
      { id: 23, name: 'XC', type: 1, trackId: 'up-plat', x: X0 - 8, dir: 'up' },
      { id: 71, name: 'Z2', type: 1, trackId: 'dn-plat', x: X0 - 8, dir: 'down' },
      { id: 72, name: 'SC', type: 3, trackId: 'dn-plat', x: X1 + 8, dir: 'down' },
    ],
    switches: [
      // 单开道岔：上行→侧线
      { id: 'to-right-up', x: X1 + 60, y1: Y_UP, y2: Y_SID, type: 'turnout', trackId1: 'up-out', trackId2: 'sid-up' },
    ],
    routes: [],
    labels: [
      { id: 'title', x: (X0 + X1) / 2, y: Y_UP - 65, text: '六里桥', fontSize: 14, color: '#dce8f8', align: 'center', font: 'sans-serif' },
      { id: 'code', x: (X0 + X1) / 2, y: Y_UP - 48, text: 'LLQ · K8+118', fontSize: 10, color: '#6a7a90', align: 'center' },
      { id: 'seg-u1', x: 35, y: Y_UP + 28, text: 'seg 87 (1181m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'seg-u2', x: X0 + 5, y: Y_UP + 28, text: 'seg 88 (129m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'seg-u3', x: X1 + 10, y: Y_UP + 28, text: 'seg 89 (1650m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'seg-d1', x: 35, y: Y_DN + 28, text: 'seg 102 (1650m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'seg-d2', x: X0 + 5, y: Y_DN + 28, text: 'seg 103 (129m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'seg-d3', x: X1 + 10, y: Y_DN + 28, text: 'seg 104 (1181m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'sid-up-lbl', x: X1 + 35, y: Y_SID + 16, text: '侧线', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
    ],
    directionLabels: { up: '← 郭公庄', down: '国家图书馆 →' },
  };
};

// 六里桥东 (LLE) — 单开道岔 (seg 131)
export const lleInterlockingData = (): StationInterlockingData => {
  const Y_UP = 140, Y_DN = 220, Y_SID = 300, X0 = 210, X1 = 350, W = 580;
  return {
    stationId: 'LLE', stationName: '六里桥东', stationCode: 'LLE', lineId: '9', bounds: { width: W, height: 360 },
    tracks: [
      { id: 'up-in', label: 'seg 125 (1041m)', y: Y_UP, x: 30, width: X0 - 30, dir: 'up', segmentIds: [125] },
      { id: 'up-plat', y: Y_UP, x: X0, width: X1 - X0, dir: 'up', segmentIds: [126] },
      { id: 'up-out', label: 'seg 127 (919m)', y: Y_UP, x: X1, width: W - 30 - X1, dir: 'up', segmentIds: [127] },
      { id: 'dn-in', label: 'seg 135 (919m)', y: Y_DN, x: 30, width: X0 - 30, dir: 'down', segmentIds: [135] },
      { id: 'dn-plat', y: Y_DN, x: X0, width: X1 - X0, dir: 'down', segmentIds: [136] },
      { id: 'dn-out', label: 'seg 137 (1041m)', y: Y_DN, x: X1, width: W - 30 - X1, dir: 'down', segmentIds: [137] },
      // 上行侧线：延长
      { id: 'sid-up', y: Y_SID, x: X1 - 10, width: 150, dir: 'up', segmentIds: [] },
    ],
    platforms: [
      { id: 'P15', name: '上行站台', trackId: 'up-plat', x: (X0 + X1) / 2, width: X1 - X0 - 20, mileageM: 9429.16, segmentIds: [126], direction: '0xaa' },
      { id: 'P16', name: '下行站台', trackId: 'dn-plat', x: (X0 + X1) / 2, width: X1 - X0 - 20, mileageM: 9429.16, segmentIds: [136], direction: '0x55' },
    ],
    signals: [
      { id: 29, name: 'XC', type: 1, trackId: 'up-plat', x: X0 - 8, dir: 'up' },
      { id: 78, name: 'SC', type: 1, trackId: 'dn-plat', x: X1 + 8, dir: 'down' },
    ],
    switches: [
      // 单开道岔：上行→侧线，右移30
      { id: 'to-right-up', x: X1 + 60, y1: Y_UP, y2: Y_SID, type: 'turnout', trackId1: 'up-out', trackId2: 'sid-up' },
    ],
    routes: [],
    labels: [
      { id: 'title', x: (X0 + X1) / 2, y: Y_UP - 65, text: '六里桥东', fontSize: 14, color: '#dce8f8', align: 'center', font: 'sans-serif' },
      { id: 'code', x: (X0 + X1) / 2, y: Y_UP - 48, text: 'LLE · K9+429', fontSize: 10, color: '#6a7a90', align: 'center' },
      { id: 'seg-u1', x: 35, y: Y_UP + 28, text: 'seg 125 (1041m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'seg-u2', x: X0 + 5, y: Y_UP + 28, text: 'seg 126 (129m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'seg-u3', x: X1 + 10, y: Y_UP + 28, text: 'seg 127 (919m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'seg-d1', x: 35, y: Y_DN + 28, text: 'seg 135 (919m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'seg-d2', x: X0 + 5, y: Y_DN + 28, text: 'seg 136 (129m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'seg-d3', x: X1 + 10, y: Y_DN + 28, text: 'seg 137 (1041m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'sid-up-lbl', x: X1 + 35, y: Y_SID + 16, text: '侧线', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
    ],
    directionLabels: { up: '← 郭公庄', down: '国家图书馆 →' },
  };
};

// 军事博物馆 (JBG) — 单渡线 + 侧线
export const jbgInterlockingData = (): StationInterlockingData => {
  const Y_UP = 140, Y_DN = 220, Y_SID = 300, X0 = 210, X1 = 350, W = 580;
  return {
    stationId: 'JBG', stationName: '军事博物馆', stationCode: 'JBG', lineId: '9', bounds: { width: W, height: 360 },
    tracks: [
      { id: 'up-in', label: 'seg 169 (632m)', y: Y_UP, x: 30, width: X0 - 30, dir: 'up', segmentIds: [169] },
      { id: 'up-plat', y: Y_UP, x: X0, width: X1 - X0, dir: 'up', segmentIds: [170] },
      { id: 'up-out', label: 'seg 171 (1781m)', y: Y_UP, x: X1, width: W - 30 - X1, dir: 'up', segmentIds: [171] },
      { id: 'dn-in', label: 'seg 179 (1781m)', y: Y_DN, x: 30, width: X0 - 30, dir: 'down', segmentIds: [179] },
      { id: 'dn-plat', y: Y_DN, x: X0, width: X1 - X0, dir: 'down', segmentIds: [180] },
      { id: 'dn-out', label: 'seg 181 (632m)', y: Y_DN, x: X1, width: W - 30 - X1, dir: 'down', segmentIds: [181] },
      // 侧线轨道：接收单渡线来车
      { id: 'sid', y: Y_SID, x: X1 - 40, width: 180, dir: 'up', segmentIds: [] },
    ],
    platforms: [
      { id: 'P19', name: '上行站台', trackId: 'up-plat', x: (X0 + X1) / 2, width: X1 - X0 - 20, mileageM: 11996.97, segmentIds: [170], direction: '0xaa' },
      { id: 'P20', name: '下行站台', trackId: 'dn-plat', x: (X0 + X1) / 2, width: X1 - X0 - 20, mileageM: 11996.97, segmentIds: [180], direction: '0x55' },
    ],
    signals: [
      { id: 38, name: 'XC', type: 1, trackId: 'up-plat', x: X0 - 8, dir: 'up' },
      { id: 87, name: 'SC', type: 1, trackId: 'dn-plat', x: X1 + 8, dir: 'down' },
    ],
    switches: [
      // 交叉渡线：连接上下行正线，右移避开站台区域（站台右缘=340）
      { id: 'xover', x: 435, y1: Y_UP, y2: Y_DN, type: 'crossover', trackId1: 'up-out', trackId2: 'dn-out' },
      // 下行→侧线单开道岔：DN正线引出接入侧线
      { id: 'to-sid', x: 385, y1: Y_DN, y2: Y_SID, type: 'turnout', trackId1: 'dn-out', trackId2: 'sid' },
    ],
    routes: [],
    labels: [
      { id: 'title', x: (X0 + X1) / 2, y: Y_UP - 65, text: '军事博物馆', fontSize: 14, color: '#dce8f8', align: 'center', font: 'sans-serif' },
      { id: 'code', x: (X0 + X1) / 2, y: Y_UP - 48, text: 'JBG · K11+996', fontSize: 10, color: '#6a7a90', align: 'center' },
      { id: 'seg-u1', x: 35, y: Y_UP + 28, text: 'seg 169 (632m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'seg-u2', x: X0 + 5, y: Y_UP + 28, text: 'seg 170 (129m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'seg-u3', x: X1 + 10, y: Y_UP + 28, text: 'seg 171 (1781m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'seg-d1', x: 35, y: Y_DN + 28, text: 'seg 179 (1781m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'seg-d2', x: X0 + 5, y: Y_DN + 28, text: 'seg 180 (129m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'seg-d3', x: X1 + 10, y: Y_DN + 28, text: 'seg 181 (632m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'sid-lbl', x: X1 + 50, y: Y_SID + 16, text: '侧线', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
    ],
    directionLabels: { up: '← 郭公庄', down: '国家图书馆 →' },
  };
};

// 白堆子 (BDZ) — 两个单开道岔 (seg 175/187) + 交叉渡线
export const bdzInterlockingData = (): StationInterlockingData => {
  const Y_UP = 140, Y_DN = 220, X0 = 210, X1 = 350, W = 580;
  return {
    stationId: 'BDZ', stationName: '白堆子', stationCode: 'BDZ', lineId: '9', bounds: { width: W, height: 320 },
    tracks: [
      { id: 'up-in', label: 'seg 173 (1781m)', y: Y_UP, x: 30, width: X0 - 30, dir: 'up', segmentIds: [173] },
      { id: 'up-plat', y: Y_UP, x: X0, width: X1 - X0, dir: 'up', segmentIds: [174] },
      { id: 'up-out', label: 'seg 178 (918m)', y: Y_UP, x: X1, width: W - 30 - X1, dir: 'up', segmentIds: [178] },
      { id: 'dn-in', label: 'seg 184 (918m)', y: Y_DN, x: 30, width: X0 - 30, dir: 'down', segmentIds: [184] },
      { id: 'dn-plat', y: Y_DN, x: X0, width: X1 - X0, dir: 'down', segmentIds: [185] },
      { id: 'dn-out', label: 'seg 186 (1781m)', y: Y_DN, x: X1, width: W - 30 - X1, dir: 'down', segmentIds: [186] },
    ],
    platforms: [
      { id: 'P21', name: '上行站台', trackId: 'up-plat', x: (X0 + X1) / 2, width: X1 - X0 - 20, mileageM: 13906.77, segmentIds: [174], direction: '0xaa' },
      { id: 'P22', name: '下行站台', trackId: 'dn-plat', x: (X0 + X1) / 2, width: X1 - X0 - 20, mileageM: 13906.77, segmentIds: [185], direction: '0x55' },
    ],
    signals: [
      { id: 40, name: 'XC', type: 1, trackId: 'up-plat', x: X0 - 8, dir: 'up' },
      { id: 89, name: 'Z2', type: 1, trackId: 'dn-plat', x: X0 - 8, dir: 'down' },
      { id: 90, name: 'SC', type: 3, trackId: 'dn-plat', x: X1 + 8, dir: 'down' },
    ],
    switches: [
      { id: 'cw-left', x: X0 - 50, y1: Y_UP, y2: Y_DN, type: 'crossover', trackId1: 'up-in', trackId2: 'dn-in' },
      { id: 'cw-right', x: X1 + 60, y1: Y_UP, y2: Y_DN, type: 'crossover', trackId1: 'up-out', trackId2: 'dn-out' },
    ],
    routes: [],
    labels: [
      { id: 'title', x: (X0 + X1) / 2, y: Y_UP - 65, text: '白堆子', fontSize: 14, color: '#dce8f8', align: 'center', font: 'sans-serif' },
      { id: 'code', x: (X0 + X1) / 2, y: Y_UP - 48, text: 'BDZ · K13+906', fontSize: 10, color: '#6a7a90', align: 'center' },
      { id: 'seg-u1', x: 35, y: Y_UP + 28, text: 'seg 173 (1781m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'seg-u2', x: X0 + 5, y: Y_UP + 28, text: 'seg 174 (129m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'seg-u3', x: X1 + 10, y: Y_UP + 28, text: 'seg 178 (918m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'seg-d1', x: 35, y: Y_DN + 28, text: 'seg 184 (918m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'seg-d2', x: X0 + 5, y: Y_DN + 28, text: 'seg 185 (129m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'seg-d3', x: X1 + 10, y: Y_DN + 28, text: 'seg 186 (1781m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
    ],
    directionLabels: { up: '← 郭公庄', down: '国家图书馆 →' },
  };
};

// 白石桥南 (BQS) — 三个单开道岔 (seg 208/211/214/222) + 交叉渡线
export const bqsInterlockingData = (): StationInterlockingData => {
  const Y_UP = 140, Y_DN = 220, X0 = 210, X1 = 350, W = 580;
  return {
    stationId: 'BQS', stationName: '白石桥南', stationCode: 'BQS', lineId: '9', bounds: { width: W, height: 320 },
    tracks: [
      { id: 'up-in', label: 'seg 203 (966m)', y: Y_UP, x: 30, width: X0 - 30, dir: 'up', segmentIds: [203] },
      { id: 'up-plat', y: Y_UP, x: X0, width: X1 - X0, dir: 'up', segmentIds: [204] },
      { id: 'up-out', label: 'seg 205 (862m)', y: Y_UP, x: X1, width: W - 30 - X1, dir: 'up', segmentIds: [205] },
      { id: 'dn-in', label: 'seg 218 (862m)', y: Y_DN, x: 30, width: X0 - 30, dir: 'down', segmentIds: [218] },
      { id: 'dn-plat', y: Y_DN, x: X0, width: X1 - X0, dir: 'down', segmentIds: [217] },
      { id: 'dn-out', label: 'seg 219 (867m)', y: Y_DN, x: X1, width: W - 30 - X1, dir: 'down', segmentIds: [219] },
    ],
    platforms: [
      { id: 'P23', name: '上行站台', trackId: 'up-plat', x: (X0 + X1) / 2, width: X1 - X0 - 20, mileageM: 14954.01, segmentIds: [204], direction: '0xaa' },
      { id: 'P24', name: '下行站台', trackId: 'dn-plat', x: (X0 + X1) / 2, width: X1 - X0 - 20, mileageM: 14954.01, segmentIds: [217], direction: '0x55' },
    ],
    signals: [
      { id: 45, name: 'XC', type: 1, trackId: 'up-plat', x: X0 - 8, dir: 'up' },
      { id: 93, name: 'SC', type: 1, trackId: 'dn-plat', x: X1 + 8, dir: 'down' },
    ],
    switches: [
      { id: 'cw-left', x: X0 - 50, y1: Y_UP, y2: Y_DN, type: 'crossover', trackId1: 'up-in', trackId2: 'dn-in' },
      { id: 'cw-right', x: X1 + 60, y1: Y_UP, y2: Y_DN, type: 'crossover', trackId1: 'up-out', trackId2: 'dn-out' },
    ],
    routes: [],
    labels: [
      { id: 'title', x: (X0 + X1) / 2, y: Y_UP - 65, text: '白石桥南', fontSize: 14, color: '#dce8f8', align: 'center', font: 'sans-serif' },
      { id: 'code', x: (X0 + X1) / 2, y: Y_UP - 48, text: 'BQS · K14+954', fontSize: 10, color: '#6a7a90', align: 'center' },
      { id: 'seg-u1', x: 35, y: Y_UP + 28, text: 'seg 203 (966m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'seg-u2', x: X0 + 5, y: Y_UP + 28, text: 'seg 204 (129m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'seg-u3', x: X1 + 10, y: Y_UP + 28, text: 'seg 205 (862m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'seg-d1', x: 35, y: Y_DN + 28, text: 'seg 218 (862m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'seg-d2', x: X0 + 5, y: Y_DN + 28, text: 'seg 217 (129m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
      { id: 'seg-d3', x: X1 + 10, y: Y_DN + 28, text: 'seg 219 (867m)', fontSize: 8, color: '#3a4a5a', font: 'monospace' },
    ],
    directionLabels: { up: '← 郭公庄', down: '国家图书馆 →' },
  };
};

/** 根据车站代码获取联锁图数据 */
export function getInterlockingData(stationCode: string): StationInterlockingData {
  switch (stationCode) {
    case 'BWR': return bwrInterlockingData();
    case 'GTG': return gtgInterlockingData();
    case 'GGZ': return ggzInterlockingData();
    case 'FSP': return fspInterlockingData();
    case 'KYL': return kylInterlockingData();
    case 'FTN': return ftnInterlockingData();
    case 'FTD': return ftdInterlockingData();
    case 'QLZ': return qlzInterlockingData();
    case 'LLQ': return llqInterlockingData();
    case 'LLE': return lleInterlockingData();
    case 'JBG': return jbgInterlockingData();
    case 'BDZ': return bdzInterlockingData();
    case 'BQS': return bqsInterlockingData();
    default: return bwrInterlockingData();
  }
}

/** 获取所有已绘制联锁站点列表 [code, name, mileageM] */
export function getInterlockingStations(): readonly (readonly [string, string, number])[] {
  return stationCatalog;
}

export function listInterlockingStations(): StationInterlockingData[] {
  return stationCatalog.map(([code]) => getInterlockingData(code)).filter(Boolean);
}

// 清理废弃的辅助函数引用
void 0;
