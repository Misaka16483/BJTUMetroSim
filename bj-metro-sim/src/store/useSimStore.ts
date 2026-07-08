import { create } from 'zustand';
import type { MetroLineData } from '../data/amapMetroApi';
import type { TrackMapData } from '../data/backendApi';

type ViewMode = 'macro' | 'micro' | 'interlocking';

interface SimState {
  // 仿真状态
  isRunning: boolean;
  speed: number;
  simTime: string;
  dayType: 'weekday' | 'friday' | 'saturday' | 'sunday';

  // 地铁线路数据
  metroLines: MetroLineData[];
  linesLoading: boolean;
  linesError: string | null;
  backendStatus: 'idle' | 'connected' | 'fallback' | 'error';
  hiddenLines: Set<string>;
  trackMap: TrackMapData | null;
  viewMode: ViewMode;

  // KPI
  punctuality: number;
  avgWaitTime: number;
  avgLoadRate: number;
  totalPassengers: number;
  totalBoarded: number;

  // 选中的列车ID
  selectedTrainId: string | null;

  // 选中的车站代码 (用于轨道级→联锁图跳转)
  selectedStationCode: string | null;

  // 动作
  toggleRunning: () => void;
  setSpeed: (speed: number) => void;
  setDayType: (dayType: 'weekday' | 'friday' | 'saturday' | 'sunday') => void;
  selectTrain: (id: string | null) => void;
  setSelectedStationCode: (code: string | null) => void;
  tick: () => void;

  // 线路管理
  setMetroLines: (lines: MetroLineData[]) => void;
  setLinesLoading: (loading: boolean) => void;
  setLinesError: (error: string | null) => void;
  setBackendStatus: (status: 'idle' | 'connected' | 'fallback' | 'error') => void;
  setTrackMap: (trackMap: TrackMapData | null) => void;
  setViewMode: (viewMode: ViewMode) => void;
  toggleLineVisibility: (lineId: string) => void;
  showAllLines: () => void;
  hideAllLines: () => void;
  showOnlyLines: (lineIds: string[]) => void;
}

export const useSimStore = create<SimState>((set, get) => ({
  isRunning: false,
  speed: 1,
  simTime: '07:00:00',
  dayType: 'weekday',
  metroLines: [],
  linesLoading: false,
  linesError: null,
  backendStatus: 'idle',
  hiddenLines: new Set<string>(),
  trackMap: null,
  viewMode: 'macro',
  punctuality: 98.5,
  avgWaitTime: 145,
  avgLoadRate: 68,
  totalPassengers: 0,
  totalBoarded: 0,
  selectedTrainId: null,
  selectedStationCode: null,

  toggleRunning: () => set((s) => ({ isRunning: !s.isRunning })),
  setSpeed: (speed: number) => set({ speed }),
  setDayType: (dayType) => set({ dayType }),
  selectTrain: (id: string | null) => set({ selectedTrainId: id }),
  setSelectedStationCode: (code: string | null) => set({ selectedStationCode: code }),

  setMetroLines: (lines) => set({ metroLines: lines, linesLoading: false, hiddenLines: new Set<string>() }),
  setLinesLoading: (loading) => set({ linesLoading: loading }),
  setLinesError: (error) => set({ linesError: error, linesLoading: false }),
  setBackendStatus: (status) => set({ backendStatus: status }),
  setTrackMap: (trackMap) => set({ trackMap }),
  setViewMode: (viewMode) => set({ viewMode }),

  toggleLineVisibility: (lineId) => set((s) => {
    const next = new Set(s.hiddenLines);
    if (next.has(lineId)) {
      next.delete(lineId);
    } else {
      next.add(lineId);
    }
    return { hiddenLines: next };
  }),

  showAllLines: () => set({ hiddenLines: new Set() }),

  hideAllLines: () => set((s) => {
    const all = new Set(s.metroLines.map((l) => l.id));
    return { hiddenLines: all };
  }),

  showOnlyLines: (lineIds) => set((s) => {
    const all = new Set(s.metroLines.map((l) => l.id));
    lineIds.forEach((id) => all.delete(id));
    return { hiddenLines: all };
  }),

  tick: () => {
    // 列车仿真逻辑保持不变
    const state = get();
    if (!state.isRunning) return;

    const speed = state.speed;
    const step = 0.005 * speed;

    const waitTime = 100 + Math.floor(Math.random() * 80);
    const punct = 96 + Math.random() * 3;

    // 更新仿真时间
    const parts = state.simTime.split(':');
    let h = parseInt(parts[0]);
    let m = parseInt(parts[1]);
    let s = parseInt(parts[2]);
    s += Math.floor(step * 600);
    m += Math.floor(s / 60);
    s = s % 60;
    h += Math.floor(m / 60);
    m = m % 60;
    if (h >= 24) h = 0;
    const newTime = `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;

    set({
      simTime: newTime,
      punctuality: Math.round(punct * 10) / 10,
      avgWaitTime: waitTime,
      avgLoadRate: 60 + Math.floor(Math.random() * 30),
      totalPassengers: state.totalPassengers + Math.floor(Math.random() * 100),
      totalBoarded: state.totalBoarded + Math.floor(Math.random() * 60),
    });
  },
}));
