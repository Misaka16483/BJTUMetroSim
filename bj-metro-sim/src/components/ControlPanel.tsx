import { Button, Slider, Select, Tag } from 'antd';
import {
  PlayCircleOutlined,
  PauseCircleOutlined,
  ReloadOutlined,
} from '@ant-design/icons';
import { useSimStore } from '../store/useSimStore';
import { useEffect } from 'react';

export default function ControlPanel() {
  const {
    isRunning, toggleRunning, speed, setSpeed,
    simTime, dayType, setDayType, tick,
  } = useSimStore();

  useEffect(() => {
    if (!isRunning) return;
    const interval = setInterval(tick, 100);
    return () => clearInterval(interval);
  }, [isRunning, tick]);

  const dayOptions = [
    { value: 'weekday', label: '周一至周四' },
    { value: 'friday', label: '周五' },
    { value: 'saturday', label: '周六' },
    { value: 'sunday', label: '周日' },
  ];

  const dayColors: Record<string, string> = {
    weekday: '#58a6ff',
    friday: '#f0883e',
    saturday: '#3fb950',
    sunday: '#a371f7',
  };

  const dayLabel = dayOptions.find((d) => d.value === dayType)?.label || '';

  return (
    <div className="p-4 rounded-lg border border-[#21262d] bg-[#161b22]">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-base font-semibold text-[#c9d1d9] uppercase tracking-wider">
          仿真控制
        </h3>
        <Tag color={dayColors[dayType]} className="border-0 text-sm">
          {dayLabel}
        </Tag>
      </div>

      {/* 仿真时间 */}
      <div className="mb-5 text-center">
        <div className="text-4xl font-mono font-bold text-[#58a6ff] tracking-wider">
          {simTime}
        </div>
        <div className="text-xs text-[#484f58] mt-1 uppercase tracking-widest">
          Simulation Clock
        </div>
      </div>

      {/* 控制按钮 */}
      <div className="flex gap-3 justify-center mb-5">
        <Button
          type={isRunning ? 'default' : 'primary'}
          icon={isRunning ? <PauseCircleOutlined /> : <PlayCircleOutlined />}
          onClick={toggleRunning}
          size="large"
          className={isRunning ? '!bg-[#2d1515] !border-[#ff4444]/40 !text-[#ff6b6b] hover:!bg-[#3d1a1a]' : '!bg-[#1a3a5c] !border-[#58a6ff]/40 !text-[#58a6ff] hover:!bg-[#1e3e6e]'}
        >
          {isRunning ? '暂停' : '开始仿真'}
        </Button>
        <Button
          icon={<ReloadOutlined />}
          onClick={() => useSimStore.getState().showAllLines()}
          size="large"
          className="!bg-[#21262d] !border-[#30363d] !text-[#8b949e] hover:!text-[#c9d1d9]"
        >
          重置
        </Button>
      </div>

      {/* 速度 */}
      <div className="mb-4">
        <div className="text-sm text-[#8b949e] mb-1 uppercase tracking-wider">
          Simulation Speed
        </div>
        <Slider
          min={1}
          max={10}
          value={speed}
          onChange={setSpeed}
          marks={{
            1: <span className="text-[#484f58] text-xs">1x</span>,
            5: <span className="text-[#484f58] text-xs">5x</span>,
            10: <span className="text-[#484f58] text-xs">10x</span>,
          }}
          className="mb-0"
        />
      </div>

      {/* 日型 */}
      <div>
        <div className="text-sm text-[#8b949e] mb-1 uppercase tracking-wider">
          Day Type
        </div>
        <Select
          value={dayType}
          onChange={setDayType}
          options={dayOptions}
          className="w-full"
          size="small"
        />
      </div>
    </div>
  );
}
