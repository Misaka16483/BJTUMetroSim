export interface InterlockingSignal {
  id: string;
  label: string;
  direction: 'UP' | 'DOWN';
  aspect: 'GREEN' | 'YELLOW' | 'RED';
  x: number;
}

export interface InterlockingRoute {
  id: string;
  from: string;
  to: string;
  direction: 'UP' | 'DOWN';
  state: 'LOCKED' | 'AVAILABLE' | 'OCCUPIED';
}

export interface InterlockingSwitch {
  id: string;
  position: 'NORMAL' | 'REVERSE';
  locked: boolean;
  x: number;
}

export interface StationInterlockingData {
  stationCode: string;
  stationName: string;
  mileageM: number;
  platformSegmentIds: number[];
  signals: InterlockingSignal[];
  switches: InterlockingSwitch[];
  routes: InterlockingRoute[];
}

const stationCatalog = [
  ['GGZ', '郭公庄', 313],
  ['FSP', '丰台科技园', 1660],
  ['KYL', '科怡路', 2560],
  ['FTN', '丰台南路', 3650],
  ['FTD', '丰台东大街', 5020],
  ['QLZ', '七里庄', 6420],
  ['LLQ', '六里桥', 8160],
  ['LLE', '六里桥东', 9350],
  ['BWR', '白堆子', 11230],
  ['JBG', '军事博物馆', 12690],
  ['BDZ', '北京西站', 14300],
  ['BQS', '白石桥南', 15980],
  ['GTG', '国家图书馆', 17390],
] as const;

function buildStation(code: string, name: string, mileageM: number, index: number): StationInterlockingData {
  const baseSignal = 900 + index * 10;
  const baseSegment = 100 + index * 4;
  const hasCrossover = index === 0 || index === stationCatalog.length - 1 || index % 4 === 2;

  return {
    stationCode: code,
    stationName: name,
    mileageM,
    platformSegmentIds: [baseSegment + 1, baseSegment + 2],
    signals: [
      { id: `S${baseSignal + 1}`, label: '上行进站', direction: 'UP', aspect: 'GREEN', x: 23 },
      { id: `S${baseSignal + 2}`, label: '上行出站', direction: 'UP', aspect: 'RED', x: 70 },
      { id: `S${baseSignal + 3}`, label: '下行进站', direction: 'DOWN', aspect: 'GREEN', x: 77 },
      { id: `S${baseSignal + 4}`, label: '下行出站', direction: 'DOWN', aspect: 'YELLOW', x: 30 },
    ],
    switches: hasCrossover
      ? [
          { id: `W${baseSignal + 1}`, position: 'NORMAL', locked: true, x: 42 },
          { id: `W${baseSignal + 2}`, position: 'NORMAL', locked: true, x: 58 },
        ]
      : [],
    routes: [
      { id: `R-${code}-UP`, from: `S${baseSignal + 1}`, to: `S${baseSignal + 2}`, direction: 'UP', state: 'LOCKED' },
      { id: `R-${code}-DOWN`, from: `S${baseSignal + 3}`, to: `S${baseSignal + 4}`, direction: 'DOWN', state: index % 3 === 0 ? 'OCCUPIED' : 'AVAILABLE' },
    ],
  };
}

const interlockingData: Map<string, StationInterlockingData> = new Map(
  stationCatalog.map(([code, name, mileageM], index) => [
    code,
    buildStation(code, name, mileageM, index),
  ]),
);

export function getInterlockingData(stationCode: string): StationInterlockingData {
  return interlockingData.get(stationCode) ?? interlockingData.get('BWR')!;
}

/** 获取所有联锁站点列表 [code, name, mileageM] */
export function getInterlockingStations(): readonly (readonly [string, string, number])[] {
  return stationCatalog;
}

export function listInterlockingStations(): StationInterlockingData[] {
  return [...interlockingData.values()];
}
