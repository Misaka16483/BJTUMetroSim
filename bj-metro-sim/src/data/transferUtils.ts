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

/**
 * 按坐标距离匹配（300m 内视为同站），
 * 返回每个站点所属线路 + 可换乘的其他线路 ID 列表
 */
export function computeStationTransfers(lines: MetroLineData[]): StationWithTransfers[] {
  // 收集所有站点
  interface RawStation { name: string; lat: number; lng: number; lineId: string }
  const all: RawStation[] = [];
  for (const line of lines) {
    for (const s of line.stations) {
      all.push({ name: s.name, lat: s.lat, lng: s.lng, lineId: line.id });
    }
  }

  const THRESHOLD = 0.003; // ~300m
  const visited = new Set<number>();
  const groups: { indices: number[] }[] = [];

  for (let i = 0; i < all.length; i++) {
    if (visited.has(i)) continue;
    const indices = [i];
    for (let j = i + 1; j < all.length; j++) {
      if (visited.has(j)) continue;
      const dlat = all[i].lat - all[j].lat;
      const dlng = all[i].lng - all[j].lng;
      if (Math.sqrt(dlat * dlat + dlng * dlng) < THRESHOLD) {
        indices.push(j);
        visited.add(j);
      }
    }
    visited.add(i);
    groups.push({ indices });
  }

  // 对每个组，收纳所有线路 ID
  const byIndexLineIds = new Map<number, string[]>();
  for (const g of groups) {
    const lineIds = [...new Set(g.indices.map((k) => all[k].lineId))];
    for (const k of g.indices) {
      byIndexLineIds.set(k, lineIds);
    }
  }

  // 输出结果
  return all.map((s, i) => {
    const allLines = byIndexLineIds.get(i) ?? [s.lineId];
    return {
      name: s.name,
      lat: s.lat,
      lng: s.lng,
      lineId: s.lineId,
      transfers: allLines.filter((id) => id !== s.lineId),
    };
  });
}

/**
 * 返回换乘站（2条及以上线路共享的站点），用于地图标记
 */
export function computeTransferGroups(lines: MetroLineData[]): TransferStation[] {
  const stations = computeStationTransfers(lines);
  const map = new Map<string, TransferStation>();

  for (const s of stations) {
    const allLines = [s.lineId, ...s.transfers].sort();
    const key = `${s.lat.toFixed(4)},${s.lng.toFixed(3)}`;
    if (allLines.length >= 2) {
      if (!map.has(key)) {
        map.set(key, { name: s.name, lat: s.lat, lng: s.lng, lineIds: allLines });
      }
    }
  }

  return [...map.values()];
}
