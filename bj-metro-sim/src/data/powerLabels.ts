const POWER_STATUS_LABELS: Record<string, string> = {
  NORMAL: '正常',
  IN_SERVICE: '运行',
  WARNING: '预警',
  LIMITED: '限牵',
  OVERLOAD: '过载',
  OUTAGE: '停运',
  UNDERVOLTAGE: '欠压',
  OVERVOLTAGE: '过压',
  OVERVOLTAGE_WARNING: '过压预警',
  REGEN_LIMITED: '再生受限',
  OPEN: '断开',
  CLOSED: '闭合',
  ENERGIZED: '带电',
};

const SIM_STATE_LABELS: Record<string, string> = {
  IDLE: '待机',
  LOADED: '已加载',
  RUNNING: '运行中',
  PAUSED: '已暂停',
  STOPPED: '已停止',
};

const QUALITY_LABELS: Record<string, string> = {
  ENGINEERING_ESTIMATE: '工程估算',
  CALIBRATED: '已校准',
  MEASURED: '实测',
  SYNTHETIC: '合成数据',
};

const ALERT_TYPE_LABELS: Record<string, string> = {
  LIMITED: '列车牵引受限',
  UNDERVOLTAGE: '列车受电欠压',
  OVERVOLTAGE: '列车受电过压',
  OVERVOLTAGE_WARNING: '列车受电过压预警',
  REGEN_LIMITED: '再生制动受限',
  SUBSTATION_OVERLOAD: '牵引所过载',
  SUBSTATION_WARNING: '牵引所负载预警',
  SUBSTATION_OUTAGE: '牵引所停运',
  FEEDER_OVERLOAD: '馈电臂过载',
  FEEDER_WARNING: '馈电臂负载预警',
  REGEN_WASTED: '再生能量未利用',
};

const SUBSTATION_NAMES: Record<string, string> = {
  'TS-0901': '郭公庄牵引混合变电所',
  'TS-0902': '丰台科技园牵引变电所（V0）',
  'TS-0903': '丰台南路牵引变电所（V0）',
  'TS-0904': '丰台东大街牵引变电所（V0）',
  'TS-0905': '七里庄牵引变电所（V0）',
  'TS-0906': '六里桥牵引变电所（V0）',
  'TS-0907': '北京西站牵引变电所（V0）',
  'TS-0908': '军事博物馆牵引变电所（V0）',
  'TS-0909': '白堆子牵引变电所（V0）',
  'TS-0910': '国家图书馆牵引变电所（V0）',
};

export function powerStatusLabel(value: string | undefined | null) {
  if (!value) return '-';
  return POWER_STATUS_LABELS[value] ?? value;
}

export function simulationStateLabel(value: string | undefined | null) {
  if (!value) return '-';
  return SIM_STATE_LABELS[value] ?? value;
}

export function powerQualityLabel(value: string | undefined | null) {
  if (!value) return '-';
  return QUALITY_LABELS[value] ?? value;
}

export function powerAlertLabel(value: unknown) {
  const key = String(value ?? 'ALERT');
  return ALERT_TYPE_LABELS[key] ?? key;
}

export function substationDisplayName(substationId: string, fallback?: string) {
  return SUBSTATION_NAMES[substationId] ?? fallback ?? substationId;
}

export function switchTypeLabel(value: string | undefined | null) {
  if (!value) return '-';
  return value === 'TIE' ? '联络开关' : value;
}
