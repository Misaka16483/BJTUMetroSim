/** 站场联锁图数据接口 */

/** 轨道方向 */
export type TrackDir = 'up' | 'down';

/** 信号机类型：1=主信号 2=调车 3=预告 */
export type SignalType = 1 | 2 | 3;

/** 轨道段定义 */
export interface InterlockingTrack {
  id: string;           // 唯一标识, 如 "up-main"
  label?: string;       // 显示标签, 如 "seg 128"
  y: number;            // 垂直位置 (相对站场坐标系)
  x: number;            // 起始 x
  width: number;        // 宽度
  dir: TrackDir;        // 上下行方向
  segmentIds: number[]; // 关联的后端 segment ID 列表
}

/** 站台定义 */
export interface InterlockingPlatform {
  id: string;           // 如 "P1"
  name: string;         // 站台名
  trackId: string;      // 附着在哪条轨道上
  x: number;            // 中心 x
  width: number;        // 宽度
  mileageM?: number;    // 里程
  segmentIds?: number[];// 关联的后端 segment ID
  direction?: string;   // 方向
}

/** 信号机定义 */
export interface InterlockingSignal {
  id: number;           // 后端信号机 ID
  name: string;         // 名称, 如 "XC"
  type: SignalType;
  trackId: string;      // 附着在哪条轨道上
  x: number;            // 在轨道上的位置 (相对站场坐标系)
  dir: TrackDir;        // 朝向
  offsetM?: number;     // 在后端 segment 上的偏移量
}

/** 道岔/渡线定义 */
export interface InterlockingSwitch {
  id: string;           // 如 "cw-left"
  label?: string;       // 显示标签
  x: number;
  y1: number;           // 岔尖 y
  y2: number;           // 岔心 y (另一条轨道的 y)
  type: 'crossover' | 'turnout';
  trackId1: string;
  trackId2: string;
}

/** 进路定义 */
export interface InterlockingRoute {
  id: number;           // 后端进路 ID
  name: string;         // 如 "F1→XC"
  startSignalId: number;
  endSignalId: number;
  /** 进路经过的路径点 (x, y) 序列, 用于绘制高亮线 */
  path: { x: number; y: number }[];
  color?: string;
}

/** 标签 */
export interface InterlockingLabel {
  id: string;
  x: number;
  y: number;
  text: string;
  fontSize?: number;
  color?: string;
  align?: 'left' | 'center' | 'right';
  font?: string;        // "monospace" | "sans-serif"
}

/** 完整站场联锁图数据 */
export interface StationInterlockingData {
  stationId: string;
  stationName: string;
  stationCode: string;
  lineId: string;

  /** 坐标系范围 (用于缩放时计算) */
  bounds: { width: number; height: number };

  tracks: InterlockingTrack[];
  platforms: InterlockingPlatform[];
  signals: InterlockingSignal[];
  switches: InterlockingSwitch[];
  routes: InterlockingRoute[];
  labels: InterlockingLabel[];

  /** 方向文字 */
  directionLabels?: {
    up?: string;   // 上行方向名称, 如 "郭公庄方向"
    down?: string; // 下行方向名称, 如 "国家图书馆方向"
  };
}
