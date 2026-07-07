import type { MetroLineData } from './metroApi';

export interface TransferStation {
  name: string;
  lat: number;
  lng: number;
  lineIds: string[];
}

export interface StationWithTransfers {
  name: string;
  lat: number;
  lng: number;
  lineId: string;
  transfers: string[]; // 可换乘的其他线路 ID
}

/** 站名归一化：去末尾"站"字、去首尾空白 */
function normalizeName(name: string): string {
  return name.replace(/站$/, '').trim();
}

/**
 * 按站名分组识别换乘站（基于网络拓扑数据, 非坐标距离）
 *
 * 逻辑：同一站名出现在 2 条及以上不同线路 → 该站是换乘站
 * 这反映的是 OSM 路线关系中的真实网络结构：
 *   "建国门" 是 route 1 (1号线) 的 stop 成员，
 *   "建国门" 也是 route 2 (2号线) 的 stop 成员 → 换乘
 */
export function computeStationTransfers(lines: MetroLineData[]): StationWithTransfers[] {
  // 按归一化站名分组，收集每组的线路 ID
  const nameGroups = new Map<string, { lineIds: Set<string>; stations: { lat: number; lng: number; lineId: string }[] }>();

  for (const line of lines) {
    for (const s of line.stations) {
      const norm = normalizeName(s.name);
      if (!nameGroups.has(norm)) {
        nameGroups.set(norm, { lineIds: new Set(), stations: [] });
      }
      const group = nameGroups.get(norm)!;
      group.lineIds.add(line.id);
      group.stations.push({ lat: s.lat, lng: s.lng, lineId: line.id });
    }
  }

  // 输出结果
  const result: StationWithTransfers[] = [];
  for (const line of lines) {
    for (const s of line.stations) {
      const norm = normalizeName(s.name);
      const group = nameGroups.get(norm)!;
      const allLines = [...group.lineIds];
      result.push({
        name: s.name,
        lat: s.lat,
        lng: s.lng,
        lineId: line.id,
        transfers: allLines.filter((id) => id !== line.id),
      });
    }
  }

  return result;
}

/**
 * 返回换乘站（2条及以上线路共享的站点），用于地图标记
 * 
 * 按归一化站名去重，每个换乘站只返回一次，
 * 位置取同组中第一个站点的坐标
 */
export function computeTransferGroups(lines: MetroLineData[]): TransferStation[] {
  const nameGroups = new Map<string, { name: string; lineIds: Set<string>; lat: number; lng: number }>();

  for (const line of lines) {
    for (const s of line.stations) {
      const norm = normalizeName(s.name);
      if (!nameGroups.has(norm)) {
        nameGroups.set(norm, { name: s.name, lineIds: new Set(), lat: s.lat, lng: s.lng });
      }
      nameGroups.get(norm)!.lineIds.add(line.id);
    }
  }

  const result: TransferStation[] = [];
  for (const [, group] of nameGroups) {
    if (group.lineIds.size >= 2) {
      result.push({
        name: group.name,
        lat: group.lat,
        lng: group.lng,
        lineIds: [...group.lineIds].sort(),
      });
    }
  }

  return result;
}
