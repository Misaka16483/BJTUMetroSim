import { Card, Statistic, Progress, Row, Col } from 'antd';
import {
  CheckCircleOutlined,
  ClockCircleOutlined,
  UserOutlined,
  DashboardOutlined,
} from '@ant-design/icons';
import { useSimStore } from '../store/useSimStore';

export default function KPIPanel() {
  const {
    punctuality, avgWaitTime, avgLoadRate,
    totalBoarded, simTime, metroLines,
  } = useSimStore();

  const punctColor = punctuality >= 95 ? '#3fb950' : punctuality >= 90 ? '#d29922' : '#f85149';
  const loadColor = avgLoadRate > 120 ? '#f85149' : avgLoadRate > 100 ? '#d29922' : '#3fb950';
  const waitColor = avgWaitTime < 180 ? '#3fb950' : '#d29922';

  return (
    <div className="p-4 rounded-lg border border-[#21262d] bg-[#161b22] h-full overflow-auto">
      <h3 className="text-sm font-semibold text-[#c9d1d9] uppercase tracking-wider mb-4">
        运营指标
      </h3>

      <Row gutter={[10, 10]}>
        <Col span={12}>
          <Card size="small" className="text-center !bg-[#0d1117] !border-[#21262d]">
            <Statistic
              title={<span className="text-xs text-[#8b949e]">准点率</span>}
              value={punctuality}
              suffix="%"
              styles={{ content: { color: punctColor, fontSize: 26 } }}
              prefix={<CheckCircleOutlined style={{ color: punctColor }} />}
              precision={1}
            />
            <Progress
              percent={punctuality}
              showInfo={false}
              strokeColor={punctColor}
              railColor="#21262d"
              size="small"
            />
          </Card>
        </Col>

        <Col span={12}>
          <Card size="small" className="text-center !bg-[#0d1117] !border-[#21262d]">
            <Statistic
              title={<span className="text-xs text-[#8b949e]">平均等待</span>}
              value={avgWaitTime}
              suffix="s"
              styles={{ content: { color: waitColor, fontSize: 26 } }}
              prefix={<ClockCircleOutlined style={{ color: waitColor }} />}
            />
            <div className="text-[12px] text-[#484f58] mt-1">高峰目标 &lt;180s</div>
          </Card>
        </Col>

        <Col span={12}>
          <Card size="small" className="text-center !bg-[#0d1117] !border-[#21262d]">
            <Statistic
              title={<span className="text-xs text-[#8b949e]">满载率</span>}
              value={avgLoadRate}
              suffix="%"
              styles={{ content: { color: loadColor, fontSize: 26 } }}
              prefix={<DashboardOutlined style={{ color: loadColor }} />}
            />
            <Progress
              percent={avgLoadRate}
              showInfo={false}
              strokeColor={loadColor}
              railColor="#21262d"
              size="small"
            />
          </Card>
        </Col>

        <Col span={12}>
          <Card size="small" className="text-center !bg-[#0d1117] !border-[#21262d]">
            <Statistic
              title={<span className="text-xs text-[#8b949e]">客运量</span>}
              value={totalBoarded}
              styles={{ content: { color: '#8b949e', fontSize: 24 } }}
              prefix={<UserOutlined style={{ color: '#8b949e' }} />}
            />
            <div className="text-[12px] text-[#484f58] mt-1">仿真实时累计</div>
          </Card>
        </Col>
      </Row>

      <div className="mt-4 pt-3 border-t border-[#21262d]">
        <div className="flex justify-between text-[13px] text-[#484f58]">
          <span>已加载线路 <span className="text-[#58a6ff] font-bold">{metroLines.length}</span> 条</span>
          <span>仿真时钟 <span className="text-[#58a6ff] font-bold">{simTime}</span></span>
        </div>
      </div>
    </div>
  );
}
