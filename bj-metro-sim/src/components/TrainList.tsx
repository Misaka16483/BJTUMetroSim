import { Table, Tag } from 'antd';
import { useSimStore } from '../store/useSimStore';
import type { ColumnsType } from 'antd/es/table';

interface LineRow {
  id: string;
  name: string;
  stationCount: number;
  color: string;
  visible: boolean;
}

export default function TrainList() {
  const metroLines = useSimStore((s) => s.metroLines);
  const hiddenLines = useSimStore((s) => s.hiddenLines);
  const toggleLineVisibility = useSimStore((s) => s.toggleLineVisibility);

  const dataSource: LineRow[] = metroLines.map((line) => ({
    id: line.id,
    name: line.name,
    stationCount: line.stations.length,
    color: line.color,
    visible: !hiddenLines.has(line.id),
  }));

  const columns: ColumnsType<LineRow> = [
    {
      title: '线路',
      dataIndex: 'name',
      key: 'name',
      width: 110,
      render: (name: string, record: LineRow) => (
        <div className="flex items-center gap-2">
          <div
            className="w-2.5 h-2.5 rounded-full shrink-0 ring-1 ring-white/10"
            style={{ backgroundColor: record.color }}
          />
          <span className="text-sm font-medium">{name}</span>
        </div>
      ),
    },
    {
      title: '站',
      dataIndex: 'stationCount',
      key: 'stationCount',
      width: 35,
      align: 'center',
      render: (count: number) => (
        <span className="text-xs text-[#484f58]">{count}</span>
      ),
    },
    {
      title: '',
      key: 'visible',
      width: 45,
      align: 'center',
      render: (_: unknown, record: LineRow) => (
        <Tag
          color={record.visible ? 'green' : 'default'}
          className="cursor-pointer m-0 text-xs leading-none px-1.5 py-0.5 border-0"
          onClick={(e) => {
            e.stopPropagation();
            toggleLineVisibility(record.id);
          }}
        >
          {record.visible ? 'ON' : 'OFF'}
        </Tag>
      ),
    },
  ];

  return (
    <div className="p-4 rounded-lg border border-[#21262d] bg-[#161b22] h-full overflow-auto">
      <h3 className="text-sm font-semibold text-[#c9d1d9] uppercase tracking-wider mb-3">
        线路控制
        <Tag className="ml-2 border-0 text-xs bg-[#1a3a5c] text-[#58a6ff]">
          {metroLines.length} LINES
        </Tag>
      </h3>
      <Table<LineRow>
        dataSource={dataSource}
        columns={columns}
        rowKey="id"
        size="small"
        pagination={false}
        scroll={{ y: 'calc(100vh - 220px)' }}
        onRow={(record) => ({
          onClick: () => toggleLineVisibility(record.id),
          className: record.visible ? '' : 'opacity-30',
          style: { cursor: 'pointer' },
        })}
      />
    </div>
  );
}
