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
  '18': '#D22668',  // 18号线
  '19': '#D3ABE7',  // 19号线 - 淡紫色
  '22': '#D49C3D',  // 22号线（平谷线）
  '23': '#009B77',  // 23号线
  '24': '#DE82B2',  // 24号线(亦庄线)
  '25': '#EF7E21',  // 25号线(房山线)
  '27': '#FF5A93',  // 27号线(昌平线)
  'S1': '#A45A2A',  // S1线 - 棕色
  '昌平': '#DE82B2', // 昌平线 - 粉红色
  '亦庄': '#E5007F', // 亦庄线 - 玫红色
  '房山': '#EF7E21', // 房山线 - 橙色
  '燕房': '#EF7E21', // 燕房线
  '大兴机场': '#004A9F', // 大兴机场线 - 深蓝色
  '首都机场': '#A192B2', // 首都机场线 - 银紫色
  '西郊': '#CE3D3A', // 西郊线
};

import type { MetroLineData } from './metroApi';

// v3/bus/linename 返回类型
interface AmapBusStop {
  name: string;
  location: string;  // "lng,lat"
  id: string;
}

interface AmapBusLine {
  name: string;
  type: string;       // "地铁" or "公交" etc
  polyline: string;   // "lng,lat;lng,lat;..."
  busstops: AmapBusStop[];
  id: string;
}

interface AmapBusResponse {
  status: string;
  count: string;
  buslines: AmapBusLine[];
}

// ═══════════════════════════════════════════════
// GCJ-02 → WGS-84 坐标转换
// ═══════════════════════════════════════════════
const PI = Math.PI;
const EE = 0.00669342162296594323;
const A = 6378245.0;

function _transformLat(x: number, y: number): number {
  let ret = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * Math.sqrt(Math.abs(x));
  ret += (20.0 * Math.sin(6.0 * x * PI) + 20.0 * Math.sin(2.0 * x * PI)) * 2.0 / 3.0;
  ret += (20.0 * Math.sin(y * PI) + 40.0 * Math.sin(y / 3.0 * PI)) * 2.0 / 3.0;
  ret += (160.0 * Math.sin(y / 12.0 * PI) + 320 * Math.sin(y * PI / 30.0)) * 2.0 / 3.0;
  return ret;
}

function _transformLng(x: number, y: number): number {
  let ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * Math.sqrt(Math.abs(x));
  ret += (20.0 * Math.sin(6.0 * x * PI) + 20.0 * Math.sin(2.0 * x * PI)) * 2.0 / 3.0;
  ret += (20.0 * Math.sin(x * PI) + 40.0 * Math.sin(x / 3.0 * PI)) * 2.0 / 3.0;
  ret += (150.0 * Math.sin(x / 12.0 * PI) + 300.0 * Math.sin(x / 30.0 * PI)) * 2.0 / 3.0;
  return ret;
}

function gcj02ToWgs84(lng: number, lat: number): [number, number] {
  const dlat = _transformLat(lng - 105.0, lat - 35.0);
  const dlng = _transformLng(lng - 105.0, lat - 35.0);
  const radlat = lat / 180.0 * PI;
  let magic = Math.sin(radlat);
  magic = 1 - EE * magic * magic;
  const sqrtmagic = Math.sqrt(magic);
  const adjustedLat = (dlat * 180.0) / ((A * (1 - EE)) / (magic * sqrtmagic) * PI);
  const adjustedLng = (dlng * 180.0) / (A / sqrtmagic * Math.cos(radlat) * PI);
  return [lng - adjustedLng, lat - adjustedLat];
}

// ═══════════════════════════════════════════════
// Polyline 解码
// ═══════════════════════════════════════════════
function decodePolyline(polyline: string): [number, number][] {
  if (!polyline.includes(';')) return [];
  const coords: [number, number][] = [];
  for (const pair of polyline.split(';')) {
    const [lng, lat] = pair.split(',').map(Number);
    if (!isNaN(lng) && !isNaN(lat)) {
      coords.push([lat, lng]);
    }
  }
  return coords;
}

// ═══════════════════════════════════════════════
// 坐标段切分
// ═══════════════════════════════════════════════
function splitIntoRouteSegments(coords: [number, number][]): [number, number][][] {
  if (coords.length === 0) return [];
  const segments: [number, number][][] = [];
  let cur: [number, number][] = [coords[0]];
  for (let i = 1; i < coords.length; i++) {
    const prev = coords[i - 1];
    const curr = coords[i];
    const dist = Math.sqrt(
      ((curr[0] - prev[0]) * 111000) ** 2 +
      ((curr[1] - prev[1]) * 111000 * Math.cos(prev[0] * PI / 180)) ** 2
    );
    if (dist > 3000) {
      if (cur.length >= 2) segments.push(cur);
      cur = [curr];
    } else {
      cur.push(curr);
    }
  }
  if (cur.length >= 2) segments.push(cur);
  return segments;
}

// ═══════════════════════════════════════════════
// 查询单条公交线路
// ═══════════════════════════════════════════════
async function queryBusLine(apiKey: string, keyword: string): Promise<AmapBusLine[]> {
  const url = `/amap-api/v3/bus/linename?city=110000&keywords=${encodeURIComponent(keyword)}&offset=3&extensions=all&key=${apiKey}`;
  const resp = await fetch(url);
  if (!resp.ok) return [];
  const json: AmapBusResponse = await resp.json();
  // 检测 API 错误（如配额耗尽）
  if (json.status !== '1') {
    const errMsg = json.status === '0' ? `API错误(info=${json.count})` : `status=${json.status}`;
    throw new Error(`高德公交API返回异常: ${errMsg}`);
  }
  return json.buslines || [];
}

// 延时工具
function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

// ═══════════════════════════════════════════════
// 要查询的线路关键字列表
// ═══════════════════════════════════════════════
const LINE_QUERIES: { id: string; keywords: string[] }[] = [
  { id: '1', keywords: ['1号线', '地铁1号线'] },
  { id: '2', keywords: ['2号线'] },
  { id: '3', keywords: ['3号线', '地铁3号线'] },
  { id: '4', keywords: ['4号线', '地铁4号线'] },
  { id: '5', keywords: ['5号线', '地铁5号线'] },
  { id: '6', keywords: ['6号线', '地铁6号线'] },
  { id: '7', keywords: ['7号线', '地铁7号线'] },
  { id: '8', keywords: ['8号线', '地铁8号线'] },
  { id: '9', keywords: ['9号线', '地铁9号线'] },
  { id: '10', keywords: ['10号线'] },
  { id: '11', keywords: ['11号线', '地铁11号线'] },
  { id: '12', keywords: ['12号线', '地铁12号线'] },
  { id: '13', keywords: ['13号线', '地铁13号线'] },
  { id: '14', keywords: ['14号线', '地铁14号线'] },
  { id: '15', keywords: ['15号线', '地铁15号线'] },
  { id: '16', keywords: ['16号线', '地铁16号线'] },
  { id: '17', keywords: ['17号线', '地铁17号线'] },
  { id: '18', keywords: ['18号线', '地铁18号线'] },
  { id: '19', keywords: ['19号线', '地铁19号线'] },
  { id: '22', keywords: ['22号线', '平谷线'] },
  { id: '23', keywords: ['23号线'] },
  { id: '24', keywords: ['亦庄线', '地铁亦庄线'] },
  { id: '25', keywords: ['房山线', '地铁房山线'] },
  { id: '27', keywords: ['昌平线', '地铁昌平线'] },
  { id: 'S1', keywords: ['S1线', '地铁S1线'] },
  { id: '燕房', keywords: ['燕房线'] },
  { id: '西郊', keywords: ['西郊线'] },
  { id: '大兴机场', keywords: ['大兴机场线', '地铁大兴机场线'] },
  { id: '首都机场', keywords: ['首都机场线', '机场线'] },
];

/**
 * 从高德公交线路 API 获取北京地铁数据（逐条查询）
 */
export async function fetchAmapBeijingMetro(apiKey: string): Promise<MetroLineData[]> {
  console.log('[AmapMetro] 开始逐条查询北京地铁线路...');
  const result: MetroLineData[] = [];
  const seenNames = new Set<string>();

  for (let qi = 0; qi < LINE_QUERIES.length; qi++) {
    const q = LINE_QUERIES[qi];
    let bestLine: AmapBusLine | null = null;

    // 尝试不同关键字
    for (const kw of q.keywords) {
      if (qi > 0) await sleep(800); // 限流延时（QPS限制约1-2次/秒）
      const lines = await queryBusLine(apiKey, kw);

      // 筛选地铁类型，排除支线/环线（优先选主线）
      const subwayLines = lines.filter((l) => l.type === '地铁');
      if (subwayLines.length === 0) continue;

      // 首选非支线/非环线的
      const mainLine = subwayLines.find(
        (l) => !l.name.includes('支线') && !l.name.includes('内环') && !l.name.includes('外环')
      );
      bestLine = mainLine || subwayLines[0];
      break;
    }

    if (!bestLine) {
      console.warn(`[AmapMetro] 未找到线路: ${q.keywords[0]}`);
      continue;
    }

    // 提取线路名中括号内的方向信息去掉，取主名称
    let displayName = bestLine.name.replace(/\(.*\)/g, '').trim();
    // 确保带「号线」
    if (!displayName.includes('号线') && !displayName.includes('线') && !displayName.includes('机场')) {
      if (/^\d+$/.test(q.id)) {
        displayName = `${q.id}号线`;
      } else {
        displayName = `${displayName}线`;
      }
    }

    // 合并特殊线路（1号线并入八通线、4号线并入大兴线）
    const isBatong = bestLine.name.includes('八通');
    const isDaxing = bestLine.name.includes('大兴') && !bestLine.name.includes('机场');
    let actualId = q.id;
    if (isBatong) actualId = '1';
    if (isDaxing) actualId = '4';

    // 颜色
    const color = LINE_COLORS[actualId] || LINE_COLORS[q.id] || getRandomColor(actualId);

    // 解析坐标
    const coordsGcj02 = decodePolyline(bestLine.polyline);
    if (coordsGcj02.length < 2) {
      console.warn(`[AmapMetro] 线路 "${displayName}" 坐标不足, 跳过`);
      continue;
    }

    // GCJ-02 → WGS-84
    const coords: [number, number][] = coordsGcj02.map(([lat, lng]) => {
      const [wlng, wlat] = gcj02ToWgs84(lng, lat);
      return [wlat, wlng];
    });
    const segments = splitIntoRouteSegments(coords);

    // 站点
    const stations = bestLine.busstops.map((st) => {
      const [lng, lat] = st.location.split(',').map(Number);
      const [wlng, wlat] = gcj02ToWgs84(lng, lat);
      return { name: st.name, lat: wlat, lng: wlng };
    });

    console.log(`[AmapMetro]   ${actualId}: ${displayName} (${stations.length} 站, ${segments.length} 段)`);

    // 去重（同一条线可能被多个关键字命中）
    const dedupKey = `${actualId}_${displayName}`;
    if (seenNames.has(dedupKey)) {
      // 合并站点（取站更多的）
      const existing = result.find((r) => r.id === actualId);
      if (existing && stations.length > existing.stations.length) {
        existing.stations = stations;
        existing.coordinates = segments;
      }
      continue;
    }
    seenNames.add(dedupKey);

    result.push({ id: actualId, name: displayName, color, coordinates: segments, stations });
  }

  // 合并 1号线 + 八通线
  result.sort((a, b) => {
    const na = parseInt(a.id), nb = parseInt(b.id);
    if (!isNaN(na) && !isNaN(nb)) return na - nb;
    if (!isNaN(na)) return -1;
    if (!isNaN(nb)) return 1;
    return a.id.localeCompare(b.id);
  });

  console.log(`[AmapMetro] 完成, 共 ${result.length} 条线路`);
  return result;
}

function getRandomColor(key: string): string {
  let hash = 0;
  for (let i = 0; i < key.length; i++) hash = key.charCodeAt(i) + ((hash << 5) - hash);
  return `hsl(${Math.abs(hash) % 360}, 70%, 55%)`;
}

// ═══════════════════════════════════════════════
// 本地缓存
// ═══════════════════════════════════════════════
const CACHE_KEY = 'bj_metro_amap_v2';
const CACHE_TS_KEY = 'bj_metro_amap_v2_ts';
const CACHE_TTL = 24 * 60 * 60 * 1000;

export function getCachedAmapData(): MetroLineData[] | null {
  try {
    const ts = localStorage.getItem(CACHE_TS_KEY);
    if (!ts || Date.now() - parseInt(ts) > CACHE_TTL) {
      localStorage.removeItem(CACHE_KEY);
      localStorage.removeItem(CACHE_TS_KEY);
      return null;
    }
    const data = localStorage.getItem(CACHE_KEY);
    return data ? JSON.parse(data) : null;
  } catch {
    return null;
  }
}

export function cacheAmapData(data: MetroLineData[]): void {
  try {
    localStorage.setItem(CACHE_KEY, JSON.stringify(data));
    localStorage.setItem(CACHE_TS_KEY, String(Date.now()));
  } catch { /* ignore */ }
}
