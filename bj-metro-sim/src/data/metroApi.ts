// 北京地铁线路色（官方色值）
const LINE_COLORS: Record<string, string> = {
  '1': '#C23A30',   // 1号线/八通线 - 红色
  '2': '#006098',   // 2号线 - 蓝色
  '3': '#D86018',   // 3号线
  '4': '#008C95',   // 4号线/大兴线 - 青绿色
  '5': '#AA0061',   // 5号线 - 紫色
  '6': '#B58500',   // 6号线 - 土黄色
  '7': '#FFC56E',   // 7号线 - 淡橙色
  '8': '#009B6B',   // 8号线 - 绿色
  '9': '#8FC31F',   // 9号线 - 黄绿色
  '10': '#009BC0',  // 10号线 - 天蓝色
  '11': '#ED796B',  // 11号线
  '12': '#A567BE',  // 12号线
  '13': '#F9E701',  // 13号线 - 黄色
  '14': '#CA9A8E',  // 14号线 - 粉色
  '15': '#753BBD',  // 15号线
  '16': '#6BA539',  // 16号线 - 翠绿色
  '17': '#00B2A9',  // 17号线
  '19': '#D3ABE7',  // 19号线 - 淡紫色
  'S1': '#A45A2A',  // S1线 - 棕色
  '昌平': '#DE82B2', // 昌平线 - 粉红色
  '亦庄': '#E5007F', // 亦庄线 - 玫红色
  '房山': '#EF7E21', // 房山线 - 橙色
  '燕房': '#EF7E21', // 燕房线
  '大兴机场': '#004A9F', // 大兴机场线 - 深蓝色
  '首都机场': '#A192B2', // 首都机场线 - 银紫色
  '西郊': '#CE3D3A', // 西郊线
};

export interface MetroLineData {
  id: string;
  name: string;
  color: string;
  coordinates: [number, number][][]; // MultiLineString segments
  stations: { name: string; lat: number; lng: number }[];
}

// 从 Overpass API 单独查询带名称的地铁站节点
// 因为 `rel["route"="subway"]; out geom;` 展开的成员节点不包含 tags（站名）
// OSM 地铁站常见标签组合：
//   1. railway=station + station=subway（主站节点）
//   2. public_transport=stop_position + subway=yes（站台停靠点，通常作为 route 的 stop 成员）
//   3. public_transport=station + subway=yes
async function fetchStationNames(): Promise<{ name: string; lat: number; lng: number }[]> {
  const query = `[out:json][timeout:60];
(
  node["railway"="station"]["station"="subway"](39.4,116.0,40.2,116.8);
  node["public_transport"="stop_position"]["subway"="yes"](39.4,116.0,40.2,116.8);
  node["public_transport"="station"]["subway"="yes"](39.4,116.0,40.2,116.8);
  node["station"="subway"](39.4,116.0,40.2,116.8);
);
out;`;

  const url = `https://overpass-api.de/api/interpreter?data=${encodeURIComponent(query)}`;
  const response = await fetch(url);
  if (!response.ok) return [];
  const data = await response.json();
  const result: { name: string; lat: number; lng: number }[] = [];
  for (const el of data.elements) {
    if (el.type === 'node' && el.tags?.name && el.lat && el.lon) {
      result.push({ name: el.tags.name, lat: el.lat, lng: el.lon });
    }
  }
  return result;
}

// 归一化站名：去掉「站」后缀，统一比较
function normalizeStationName(name: string): string {
  return name.replace(/站$/, '').trim();
}

// 用坐标距离匹配站名（300m 内视为同一站）
function matchStationName(
  targetLat: number, targetLng: number,
  namedStations: { name: string; lat: number; lng: number }[]
): string {
  const THRESHOLD = 0.003; // ~300m
  let best = '';
  let bestDist = Infinity;
  for (const ns of namedStations) {
    const dlat = targetLat - ns.lat;
    const dlng = targetLng - ns.lng;
    const dist = dlat * dlat + dlng * dlng;
    if (dist < THRESHOLD * THRESHOLD && dist < bestDist) {
      bestDist = dist;
      best = ns.name;
    }
  }
  return best;
}

// 按坐标距离去重：上下行分别引用不同的 stop_position 节点，坐标微有偏差
function deduplicateStations(
  stations: { name: string; lat: number; lng: number }[]
): { name: string; lat: number; lng: number }[] {
  const THRESHOLD = 0.0015; // ~150m，比站间距离小
  const groups: { name: string; lat: number; lng: number }[][] = [];

  for (const st of stations) {
    let found = false;
    for (const group of groups) {
      const rep = group[0];
      const dlat = st.lat - rep.lat;
      const dlng = st.lng - rep.lng;
      if (Math.sqrt(dlat * dlat + dlng * dlng) < THRESHOLD) {
        group.push(st);
        found = true;
        break;
      }
    }
    if (!found) {
      groups.push([st]);
    }
  }

  // 每组取最佳站名：优先有名字的，多个时取较长的（去掉「站」后缀后）
  return groups.map((group) => {
    const named = group.filter((s) => s.name && s.name.length > 0);
    if (named.length === 0) return group[0];
    // 选名字最长的（通常更完整）
    named.sort((a, b) => b.name.length - a.name.length);
    const canonical = normalizeStationName(named[0].name);
    // 用 named[0] 的坐标（通常是第一个 relation 中的原始坐标）
    return { name: canonical, lat: named[0].lat, lng: named[0].lng };
  });
}

// 按站点沿线路轨迹的顺序排序
function sortStationsAlongRoute(
  stations: { name: string; lat: number; lng: number }[],
  trackCoords: [number, number][]
): { name: string; lat: number; lng: number }[] {
  if (stations.length === 0 || trackCoords.length < 2) return stations;

  // 对每个站点，找它在轨迹上的最近投影位置（按索引），然后按索引排序
  const indexed = stations.map((st) => {
    let bestIdx = 0;
    let bestDist = Infinity;
    for (let i = 0; i < trackCoords.length; i++) {
      const dlat = st.lat - trackCoords[i][0];
      const dlng = st.lng - trackCoords[i][1];
      const dist = dlat * dlat + dlng * dlng;
      if (dist < bestDist) {
        bestDist = dist;
        bestIdx = i;
      }
    }
    return { ...st, _idx: bestIdx * 1000 + Math.round(Math.sqrt(bestDist) * 1e6) };
  });

  indexed.sort((a, b) => a._idx - b._idx);
  return indexed.map(({ _idx, ...st }) => st);
}

// 从 Overpass API 获取北京地铁真实数据
export async function fetchBeijingMetro(): Promise<MetroLineData[]> {
  const query = `[out:json][timeout:60];
rel["route"="subway"](39.4,116.0,40.2,116.8);
out geom;`;

  const url = `https://overpass-api.de/api/interpreter?data=${encodeURIComponent(query)}`;

  const response = await fetch(url);
  if (!response.ok) throw new Error(`Overpass API error: ${response.status}`);

  const data = await response.json();

  // 并行获取带站名的车站节点
  const namedStations = await fetchStationNames();

  // 按线路名称分组
  const lineMap = new Map<string, {
    name: string;
    ref: string;
    coordinates: [number, number][];
    stations: { name: string; lat: number; lng: number }[];
  }>();

  // 已收集坐标的线路（避免双向关系导致坐标重复叠加）
  const coordsCollected = new Set<string>();

  for (const element of data.elements) {
    if (element.type !== 'relation' || !element.tags) continue;

    const ref = element.tags.ref || '';
    const name = element.tags.name || ref + '号线';

    // 提取线路名称关键字
    let lineKey = ref.replace(/[号线]/g, '').trim();
    if (!lineKey && name) {
      lineKey = name.replace(/北京地铁|号线|\(.*\)/g, '').trim();
    }
    if (!lineKey) continue;

    // 标准化线路名
    const normalizedKey = normalizeLineName(lineKey);
    if (!normalizedKey) continue;

    if (!lineMap.has(normalizedKey)) {
      lineMap.set(normalizedKey, {
        name: name || `${normalizedKey}号线`,
        ref: normalizedKey,
        coordinates: [],
        stations: [],
      });
    }

    const line = lineMap.get(normalizedKey)!;

    // 提取坐标（路径）— 仅首条关系提取坐标，避免上下行叠加
    if (element.members) {
      const canExtractCoords = !coordsCollected.has(normalizedKey);

      for (const member of element.members) {
        if (member.type === 'way' && member.geometry && canExtractCoords) {
          const coords: [number, number][] = member.geometry.map(
            (p: { lat: number; lon: number }) => [p.lat, p.lon]
          );
          line.coordinates.push(...coords);
        }
        // 提取车站（每趟都收集，确保双向车站都收录）
        if (member.type === 'node' && member.role === 'stop' && member.lat && member.lon) {
          // 优先用 member.tags?.name；若为空则从独立查询的命名车站中匹配
          const stName = member.tags?.name || matchStationName(member.lat, member.lon, namedStations);
          // 避免重复车站
          if (!line.stations.some((s) => s.name === stName && s.lat === member.lat)) {
            line.stations.push({
              name: stName,
              lat: member.lat,
              lng: member.lon,
            });
          }
        }
      }
    }
    coordsCollected.add(normalizedKey);
  }

  // 转换为最终格式
  const result: MetroLineData[] = [];

  for (const [key, line] of lineMap) {
    if (line.coordinates.length < 2) continue;

    // 去重坐标并分段（当坐标跳跃过大时切分）
    const segments = splitIntoRouteSegments(line.coordinates);
    const color = LINE_COLORS[key] || getRandomColor(key);

    // 站点去重（上下行不同 stop_position 节点视为同一站）并按轨迹排序
    const dedupedStations = deduplicateStations(line.stations);
    const sortedStations = sortStationsAlongRoute(dedupedStations, line.coordinates);

    // 使用线路名（如果有官方名称）
    let displayName: string;
    const lower = line.name.toLowerCase();
    if (lower.includes('airport') || lower.includes('机场')) {
      displayName = key.includes('大兴') ? '大兴机场线' : '首都机场线';
    } else if (key === 'S1') {
      displayName = 'S1线';
    } else if (key.match(/^\d+$/)) {
      displayName = `${key}号线`;
    } else {
      displayName = `${key}线`;
    }

    result.push({
      id: key,
      name: displayName,
      color,
      coordinates: segments,
      stations: sortedStations,
    });
  }

  // 按线路号排序
  result.sort((a, b) => {
    const numA = parseInt(a.id);
    const numB = parseInt(b.id);
    if (!isNaN(numA) && !isNaN(numB)) return numA - numB;
    if (!isNaN(numA)) return -1;
    if (!isNaN(numB)) return 1;
    return a.id.localeCompare(b.id);
  });

  // 合并去重：1号线和八通线
  const merged = mergeRelatedLines(result);
  return merged;
}

function normalizeLineName(name: string): string {
  const map: Record<string, string> = {
    '1': '1', '八通': '1', 'batong': '1',
    '2': '2',
    '3': '3',
    '4': '4', '大兴': '4',
    '5': '5',
    '6': '6',
    '7': '7',
    '8': '8',
    '9': '9',
    '10': '10',
    '11': '11',
    '12': '12',
    '13': '13',
    '14': '14',
    '15': '15',
    '16': '16',
    '17': '17',
    '19': '19',
    'S1': 'S1', 's1': 'S1',
    '昌平': '昌平', 'changping': '昌平',
    '亦庄': '亦庄', 'yizhuang': '亦庄',
    '房山': '房山', 'fangshan': '房山',
    '燕房': '房山', 'yanfang': '房山',
    '大兴机场': '大兴机场', 'daxing airport': '大兴机场',
    '首都机场': '首都机场', 'capital airport': '首都机场', 'airport express': '首都机场',
    '西郊': '西郊', 'xijiao': '西郊',
  };

  const lower = name.toLowerCase().trim();
  // 直接匹配数字
  if (/^\d+$/.test(name)) return name;
  // 查表
  if (map[name]) return map[name];
  if (map[lower]) return map[lower];

  // 尝试提取数字
  const numMatch = name.match(/(\d+)/);
  if (numMatch) return numMatch[1];

  // 尝试中文匹配
  for (const [key, val] of Object.entries(map)) {
    if (lower.includes(key.toLowerCase())) return val;
  }

  return '';
}

function mergeRelatedLines(lines: MetroLineData[]): MetroLineData[] {
  const result: MetroLineData[] = [];
  const merged = new Set<string>();

  for (const line of lines) {
    if (merged.has(line.id)) continue;

    if (line.id === '1') {
      // 合并八通线到1号线
      const batong = lines.find((l) => l.id === '八通' || l.name.includes('八通'));
      if (batong) {
        merged.add('八通');
        result.push({
          ...line,
          name: '1号线/八通线',
          coordinates: [...line.coordinates, ...batong.coordinates],
          stations: [...line.stations, ...batong.stations],
        });
        continue;
      }
    }

    if (line.id === '4') {
      const daxing = lines.find((l) => l.id === '大兴' || l.name.includes('大兴') && !l.name.includes('机场'));
      if (daxing) {
        merged.add(daxing.id);
        result.push({
          ...line,
          name: '4号线/大兴线',
          coordinates: [...line.coordinates, ...daxing.coordinates],
          stations: [...line.stations, ...daxing.stations],
        });
        continue;
      }
    }

    result.push(line);
  }

  return result;
}

// 将连续坐标切分为路线段（处理跳跃）
function splitIntoRouteSegments(coords: [number, number][]): [number, number][][] {
  if (coords.length === 0) return [];

  const segments: [number, number][][] = [];
  let currentSegment: [number, number][] = [coords[0]];

  for (let i = 1; i < coords.length; i++) {
    const prev = coords[i - 1];
    const curr = coords[i];
    // 计算两点间距离（大致，单位度）
    const dist = Math.sqrt(
      Math.pow((curr[0] - prev[0]) * 111000, 2) +
      Math.pow((curr[1] - prev[1]) * 111000 * Math.cos(prev[0] * Math.PI / 180), 2)
    );

    if (dist > 3000) {
      // 跳跃过大，开始新段
      if (currentSegment.length >= 2) {
        segments.push(currentSegment);
      }
      currentSegment = [curr];
    } else {
      currentSegment.push(curr);
    }
  }

  if (currentSegment.length >= 2) {
    segments.push(currentSegment);
  }

  return segments;
}

// 从 key 生成稳定的随机颜色
function getRandomColor(key: string): string {
  let hash = 0;
  for (let i = 0; i < key.length; i++) {
    hash = key.charCodeAt(i) + ((hash << 5) - hash);
  }
  const h = Math.abs(hash) % 360;
  return `hsl(${h}, 65%, 45%)`;
}

// 检查数据是否已缓存
const CACHE_KEY = 'bj_metro_data_v3';        // v3: 站名补全 + 去重 + 沿轨排序
const CACHE_TIMESTAMP_KEY = 'bj_metro_data_v3_ts';
const CACHE_TTL = 24 * 60 * 60 * 1000; // 24小时

export function getCachedMetroData(): MetroLineData[] | null {
  try {
    const ts = localStorage.getItem(CACHE_TIMESTAMP_KEY);
    if (!ts) return null;
    if (Date.now() - parseInt(ts) > CACHE_TTL) {
      localStorage.removeItem(CACHE_KEY);
      localStorage.removeItem(CACHE_TIMESTAMP_KEY);
      return null;
    }
    const data = localStorage.getItem(CACHE_KEY);
    if (!data) return null;
    return JSON.parse(data);
  } catch {
    return null;
  }
}

export function cacheMetroData(data: MetroLineData[]): void {
  try {
    localStorage.setItem(CACHE_KEY, JSON.stringify(data));
    localStorage.setItem(CACHE_TIMESTAMP_KEY, String(Date.now()));
  } catch {
    // localStorage full, ignore
  }
}
