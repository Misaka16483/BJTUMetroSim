const DEMO_URL = 'http://127.0.0.1:8000/api/phase2/member-c/demo?api=http%3A%2F%2F127.0.0.1%3A8000';

export default function MemberCInterlockingDemo() {
  return (
    <section
      className="h-full flex flex-col overflow-hidden"
      style={{ background: '#040810', borderRadius: 6, border: '1px solid #172436' }}
    >
      <div
        className="h-10 shrink-0 flex items-center px-4"
        style={{ borderBottom: '1px solid #172436', background: '#0d1424' }}
      >
        <span className="text-[12px] font-medium" style={{ color: '#d7ebff' }}>INTERLOCKING TEST</span>
        <span className="ml-3 text-[10px] font-mono" style={{ color: '#20c997' }}>MEMBER C / INDEPENDENT SESSION</span>
      </div>
      <iframe
        title="Member C interlocking test"
        src={DEMO_URL}
        className="w-full flex-1 border-0"
      />
    </section>
  );
}
