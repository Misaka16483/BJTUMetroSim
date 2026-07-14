import {
  AreaChart, Area, BarChart, Bar, LineChart, Line,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from 'recharts';

const tooltipContentStyle: React.CSSProperties = {
  background: '#161b22',
  border: '1px solid #30363d',
  borderRadius: 6,
  fontSize: 11,
  color: '#c9d1d9',
};
const COLORS = ['#58a6ff', '#3fb950', '#d29922', '#f85149', '#db6d28', '#8b5cf6', '#ec8c7a', '#79c0ff'];
const CHART_HEIGHT = 200;

function ChartCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div
      style={{
        background: '#0d1117',
        border: '1px solid #30363d',
        borderRadius: 8,
        padding: '14px 14px 8px',
        marginBottom: 14,
      }}
    >
      <div style={{ fontSize: 11, fontWeight: 600, color: '#c9d1d9', marginBottom: 10 }}>{title}</div>
      {children}
    </div>
  );
}

/* ═════════════════════════════════════════════════════
   动力性能图表
   ═════════════════════════════════════════════════════ */
export function DynamicsCharts({ charts }: { charts: {
  speedTimeSeries: Array<Record<string, number | string>>;
  energyCumulative: Array<Record<string, number | string>>;
  trainEnergyComparison: Array<{ trainId: string; energyKwh: number }>;
  trainIds: string[];
} }) {
  return (
    <>
      {/* 速度-时间曲线 */}
      <ChartCard title="速度-时间曲线">
        <ResponsiveContainer width="100%" height={CHART_HEIGHT}>
          <LineChart data={charts.speedTimeSeries}>
            <CartesianGrid strokeDasharray="3 3" stroke="#21262d" />
            <XAxis dataKey="time" tick={{ fontSize: 10, fill: '#8b949e' }} interval="preserveStartEnd" />
            <YAxis tick={{ fontSize: 10, fill: '#8b949e' }} unit=" km/h" />
            <Tooltip contentStyle={tooltipContentStyle} />
            <Legend wrapperStyle={{ fontSize: 10, color: '#8b949e' }} />
            {charts.trainIds.map((id, i) => (
              <Line key={id} type="monotone" dataKey={id} stroke={COLORS[i % COLORS.length]} dot={false} strokeWidth={1.5} name={id} />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </ChartCard>

      {/* 能耗累积曲线 */}
      <ChartCard title="能耗累积曲线">
        <ResponsiveContainer width="100%" height={CHART_HEIGHT}>
          <AreaChart data={charts.energyCumulative}>
            <CartesianGrid strokeDasharray="3 3" stroke="#21262d" />
            <XAxis dataKey="time" tick={{ fontSize: 10, fill: '#8b949e' }} />
            <YAxis tick={{ fontSize: 10, fill: '#8b949e' }} unit=" kWh" />
            <Tooltip contentStyle={tooltipContentStyle} />
            <Legend wrapperStyle={{ fontSize: 10, color: '#8b949e' }} />
            <Area type="monotone" dataKey="traction" stroke="#58a6ff" fill="#58a6ff" fillOpacity={0.15} name="牵引能耗" stackId="1" />
            <Area type="monotone" dataKey="auxiliary" stroke="#d29922" fill="#d29922" fillOpacity={0.15} name="辅助能耗" stackId="1" />
            <Area type="monotone" dataKey="regen" stroke="#3fb950" fill="#3fb950" fillOpacity={0.15} name="再生电能" stackId="2" />
          </AreaChart>
        </ResponsiveContainer>
      </ChartCard>

      {/* 列车能耗对比 */}
      <ChartCard title="列车能耗对比">
        <ResponsiveContainer width="100%" height={CHART_HEIGHT}>
          <BarChart data={charts.trainEnergyComparison}>
            <CartesianGrid strokeDasharray="3 3" stroke="#21262d" />
            <XAxis dataKey="trainId" tick={{ fontSize: 10, fill: '#8b949e' }} />
            <YAxis tick={{ fontSize: 10, fill: '#8b949e' }} unit=" kWh" />
            <Tooltip contentStyle={tooltipContentStyle} />
            <Bar dataKey="energyKwh" fill="#58a6ff" radius={[3, 3, 0, 0]} name="能耗 (kWh)" />
          </BarChart>
        </ResponsiveContainer>
      </ChartCard>
    </>
  );
}

/* ═════════════════════════════════════════════════════
   客流统计图表
   ═════════════════════════════════════════════════════ */
export function PassengerCharts({ charts }: { charts: {
  arrivalTimeSeries: Array<Record<string, number | string>>;
  stationPassengerRanking: Array<{ station: string; total: number }>;
  boardingAlightingComparison: Array<{ station: string; boarding: number; alighting: number }>;
} }) {
  const stationKeys = charts.arrivalTimeSeries.length > 0
    ? Object.keys(charts.arrivalTimeSeries[0]).filter((k) => k !== 'time')
    : [];

  return (
    <>
      {/* 进站人数趋势 */}
      <ChartCard title="进站人数趋势">
        <ResponsiveContainer width="100%" height={CHART_HEIGHT}>
          <LineChart data={charts.arrivalTimeSeries}>
            <CartesianGrid strokeDasharray="3 3" stroke="#21262d" />
            <XAxis dataKey="time" tick={{ fontSize: 10, fill: '#8b949e' }} interval="preserveStartEnd" />
            <YAxis tick={{ fontSize: 10, fill: '#8b949e' }} />
            <Tooltip contentStyle={tooltipContentStyle} />
            <Legend wrapperStyle={{ fontSize: 10, color: '#8b949e' }} />
            {stationKeys.map((key, i) => (
              <Line key={key} type="monotone" dataKey={key} stroke={COLORS[i % COLORS.length]} dot={false} strokeWidth={1.5} name={key} />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </ChartCard>

      {/* 站点客流排名 */}
      <ChartCard title="站点客流排名">
        <ResponsiveContainer width="100%" height={CHART_HEIGHT}>
          <BarChart data={[...charts.stationPassengerRanking].reverse()} layout="vertical">
            <CartesianGrid strokeDasharray="3 3" stroke="#21262d" />
            <XAxis type="number" tick={{ fontSize: 10, fill: '#8b949e' }} />
            <YAxis dataKey="station" type="category" tick={{ fontSize: 10, fill: '#8b949e' }} width={60} />
            <Tooltip contentStyle={tooltipContentStyle} />
            <Bar dataKey="total" fill="#58a6ff" radius={[0, 3, 3, 0]} name="总客流 (人)" />
          </BarChart>
        </ResponsiveContainer>
      </ChartCard>

      {/* 上下车对比 */}
      <ChartCard title="上下车对比">
        <ResponsiveContainer width="100%" height={CHART_HEIGHT}>
          <BarChart data={charts.boardingAlightingComparison}>
            <CartesianGrid strokeDasharray="3 3" stroke="#21262d" />
            <XAxis dataKey="station" tick={{ fontSize: 10, fill: '#8b949e' }} />
            <YAxis tick={{ fontSize: 10, fill: '#8b949e' }} />
            <Tooltip contentStyle={tooltipContentStyle} />
            <Legend wrapperStyle={{ fontSize: 10, color: '#8b949e' }} />
            <Bar dataKey="boarding" fill="#3fb950" radius={[3, 3, 0, 0]} name="上车" />
            <Bar dataKey="alighting" fill="#f85149" radius={[3, 3, 0, 0]} name="下车" />
          </BarChart>
        </ResponsiveContainer>
      </ChartCard>
    </>
  );
}

/* ═════════════════════════════════════════════════════
   供电性能图表
   ═════════════════════════════════════════════════════ */
export function PowerCharts({ charts }: { charts: {
  voltageTimeSeries: Array<Record<string, number | string | null>>;
  powerTimeSeries: Array<Record<string, number | string>>;
  substationLoad: Array<{ substation: string; avgLoad: number }>;
} }) {
  return (
    <>
      {/* 电压趋势 */}
      <ChartCard title="电压趋势">
        <ResponsiveContainer width="100%" height={CHART_HEIGHT}>
          <LineChart data={charts.voltageTimeSeries}>
            <CartesianGrid strokeDasharray="3 3" stroke="#21262d" />
            <XAxis dataKey="time" tick={{ fontSize: 10, fill: '#8b949e' }} interval="preserveStartEnd" />
            <YAxis tick={{ fontSize: 10, fill: '#8b949e' }} unit=" V" domain={['dataMin - 20', 'dataMax + 20']} />
            <Tooltip contentStyle={tooltipContentStyle} />
            <Legend wrapperStyle={{ fontSize: 10, color: '#8b949e' }} />
            <Line type="monotone" dataKey="max" stroke="#3fb950" dot={false} strokeWidth={1} name="最高" />
            <Line type="monotone" dataKey="avg" stroke="#58a6ff" dot={false} strokeWidth={2} name="平均" />
            <Line type="monotone" dataKey="min" stroke="#f85149" dot={false} strokeWidth={1} name="最低" />
          </LineChart>
        </ResponsiveContainer>
      </ChartCard>

      {/* 功率趋势 */}
      <ChartCard title="功率趋势">
        <ResponsiveContainer width="100%" height={CHART_HEIGHT}>
          <AreaChart data={charts.powerTimeSeries}>
            <CartesianGrid strokeDasharray="3 3" stroke="#21262d" />
            <XAxis dataKey="time" tick={{ fontSize: 10, fill: '#8b949e' }} interval="preserveStartEnd" />
            <YAxis tick={{ fontSize: 10, fill: '#8b949e' }} unit=" kW" />
            <Tooltip contentStyle={tooltipContentStyle} />
            <Legend wrapperStyle={{ fontSize: 10, color: '#8b949e' }} />
            <Area type="monotone" dataKey="traction" stroke="#58a6ff" fill="#58a6ff" fillOpacity={0.12} name="牵引功率" />
            <Area type="monotone" dataKey="regen" stroke="#3fb950" fill="#3fb950" fillOpacity={0.12} name="再生功率" />
            <Area type="monotone" dataKey="losses" stroke="#f85149" fill="#f85149" fillOpacity={0.12} name="损耗" />
          </AreaChart>
        </ResponsiveContainer>
      </ChartCard>

      {/* 变电站负载 */}
      <ChartCard title="变电站负载">
        <ResponsiveContainer width="100%" height={CHART_HEIGHT}>
          <BarChart data={charts.substationLoad}>
            <CartesianGrid strokeDasharray="3 3" stroke="#21262d" />
            <XAxis dataKey="substation" tick={{ fontSize: 10, fill: '#8b949e' }} />
            <YAxis tick={{ fontSize: 10, fill: '#8b949e' }} domain={[0, 1]} tickFormatter={(v: number) => `${(v * 100).toFixed(0)}%`} />
            <Tooltip contentStyle={tooltipContentStyle} formatter={(value: number) => `${(value * 100).toFixed(1)}%`} />
            <Bar dataKey="avgLoad" fill="#58a6ff" radius={[3, 3, 0, 0]} name="平均负载率" />
          </BarChart>
        </ResponsiveContainer>
      </ChartCard>
    </>
  );
}
