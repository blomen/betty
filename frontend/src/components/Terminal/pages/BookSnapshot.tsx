import type { StreamBookEvent, CandleData, ExpandedSession, VPLevel } from '@/types/market';

interface Props {
  book: StreamBookEvent | null;
  lastCandle: CandleData | null;
  session: ExpandedSession | null;
}

export function BookSnapshot({ session }: Props) {
  const s = session?.session;
  const profiles = session?.profiles;
  const pricePos = session?.price_position;

  return (
    <div className="flex flex-col h-full min-h-0 text-xs font-mono overflow-y-auto">

      {/* VWAP — verified */}
      {s?.vwap != null && (
        <Section label="VWAP">
          <div className="flex items-baseline justify-between">
            <span className="text-cyan-400 text-sm font-bold">{s.vwap.toFixed(2)}</span>
            {pricePos?.vwap_deviation_sd != null && (
              <span className={`text-[11px] ${
                Math.abs(pricePos.vwap_deviation_sd) > 2 ? 'text-red-400' :
                Math.abs(pricePos.vwap_deviation_sd) > 1 ? 'text-amber-400' : 'text-muted2'
              }`}>
                {pricePos.vwap_deviation_sd > 0 ? '+' : ''}{pricePos.vwap_deviation_sd.toFixed(2)} SD
              </span>
            )}
          </div>
          {s.vwap_1sd_upper != null && s.vwap_1sd_lower != null && (
            <div className="flex flex-col gap-0.5 mt-1">
              <div className="flex justify-between">
                <span className="text-[10px] text-muted2">+1SD {s.vwap_1sd_upper.toFixed(2)}</span>
                <span className="text-[10px] text-muted2">-1SD {s.vwap_1sd_lower.toFixed(2)}</span>
              </div>
              {s.vwap_2sd_upper != null && s.vwap_2sd_lower != null && (
                <div className="flex justify-between">
                  <span className="text-[10px] text-muted2">+2SD {s.vwap_2sd_upper.toFixed(2)}</span>
                  <span className="text-[10px] text-muted2">-2SD {s.vwap_2sd_lower.toFixed(2)}</span>
                </div>
              )}
              {s.vwap_3sd_upper != null && s.vwap_3sd_lower != null && (
                <div className="flex justify-between">
                  <span className="text-[10px] text-muted2">+3SD {s.vwap_3sd_upper.toFixed(2)}</span>
                  <span className="text-[10px] text-muted2">-3SD {s.vwap_3sd_lower.toFixed(2)}</span>
                </div>
              )}
            </div>
          )}
        </Section>
      )}

      {/* Session Levels — verified */}
      <Section label="Session">
        {s?.ib_high != null && s?.ib_low != null ? (
          <>
            <Row label="IBH" value={s.ib_high.toFixed(2)} color="text-amber-400" />
            <Row label="IBL" value={s.ib_low.toFixed(2)} color="text-amber-400" />
            <Row label="IB Range" value={(s.ib_high - s.ib_low).toFixed(2)} />
          </>
        ) : (
          <Placeholder text="Waiting for IB (09:30-10:30 ET)" />
        )}
        {s?.pdh != null && <Row label="PDH" value={s.pdh.toFixed(2)} color="text-orange-400" />}
        {s?.pdl != null && <Row label="PDL" value={s.pdl.toFixed(2)} color="text-orange-400" />}
        {s?.tokyo_high != null && <Row label="Tokyo H" value={s.tokyo_high.toFixed(2)} color="text-pink-400" />}
        {s?.tokyo_low != null && <Row label="Tokyo L" value={s.tokyo_low.toFixed(2)} color="text-pink-400" />}
        {s?.london_high != null && <Row label="London H" value={s.london_high.toFixed(2)} color="text-blue-400" />}
        {s?.london_low != null && <Row label="London L" value={s.london_low.toFixed(2)} color="text-blue-400" />}
      </Section>

      {/* Volume Profile — verified */}
      <Section label="Volume Profile">
        <VPRow label="Daily" vp={profiles?.session} color="text-purple-400" />
        {profiles?.developing_poc != null && (
          <Row label="devPOC" value={profiles.developing_poc.toFixed(2)} color="text-white" />
        )}
      </Section>

    </div>
  );
}

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="px-3 py-2 border-b border-border last:border-b-0">
      <div className="text-[10px] text-muted uppercase tracking-wider mb-2">{label}</div>
      {children}
    </div>
  );
}

function Row({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-muted2 text-[10px]">{label}</span>
      <span className={`text-[11px] ${color ?? 'text-text'}`}>{value}</span>
    </div>
  );
}

function VPRow({ label, vp, color, anchor }: {
  label: string;
  vp?: VPLevel | null;
  color: string;
  anchor?: string;
}) {
  if (!vp) return null;
  return (
    <div className="mb-1.5">
      <div className="flex items-center justify-between">
        <span className={`text-[10px] ${color} font-bold`}>{label}</span>
        {anchor && <span className="text-[9px] text-muted2">{anchor}</span>}
      </div>
      <div className="grid grid-cols-3 gap-x-1 text-[10px]">
        <span className="text-muted2">VAH <span className="text-text">{vp.vah.toFixed(0)}</span></span>
        <span className="text-muted2">POC <span className={color}>{vp.poc.toFixed(0)}</span></span>
        <span className="text-muted2">VAL <span className="text-text">{vp.val.toFixed(0)}</span></span>
      </div>
    </div>
  );
}

function Placeholder({ text }: { text: string }) {
  return <div className="text-muted2 text-center py-2 text-[10px]">{text}</div>;
}
