import { useProfiles } from '@/hooks/useProfiles';
import { api } from '@/services/api';
import { useQueryClient } from '@tanstack/react-query';
import type { StatsRange } from '@/hooks/useStatsData';

const RANGES: StatsRange[] = ['all', '90d', '30d', '7d'];

export function StatsHeader({
  profileId, setProfileId, range, setRange,
}: {
  profileId: number | undefined;
  setProfileId: (id: number) => void;
  range: StatsRange;
  setRange: (r: StatsRange) => void;
}) {
  const { profiles } = useProfiles();
  const qc = useQueryClient();
  const selected = profiles.find((p) => p.id === profileId);
  const nextStyle = selected?.style === 'bonus_extraction' ? 'personal' : 'bonus_extraction';

  const toggleStyle = async () => {
    if (!selected) return;
    await api.updateProfile(selected.id, { style: nextStyle });
    qc.invalidateQueries({ queryKey: ['profiles'] });
  };

  return (
    <div className="flex items-center justify-between flex-wrap gap-2">
      <div className="flex items-center gap-2">
        <select
          className="px-2 py-1 text-xs bg-panel border border-border text-text"
          value={profileId ?? ''}
          onChange={(e) => setProfileId(Number(e.target.value))}
        >
          {profiles.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
        <button
          onClick={toggleStyle}
          title="Click to switch this profile's stats style"
          className={`px-2 py-0.5 text-[10px] uppercase tracking-wider rounded border ${
            selected?.style === 'bonus_extraction'
              ? 'bg-tabBankroll/20 text-tabBankroll border-tabBankroll/40'
              : 'bg-accent/15 text-accent border-accent/40'
          }`}
        >
          {selected?.style === 'bonus_extraction' ? 'Bonus Extraction' : 'Personal'}
        </button>
      </div>
      <div className="flex gap-1">
        {RANGES.map((r) => (
          <button key={r} onClick={() => setRange(r)}
            className={`px-2 py-0.5 text-[10px] rounded border ${
              range === r ? 'bg-tabBets/20 text-tabBets border-tabBets/40'
                          : 'bg-panel2 text-muted border-border hover:text-text'}`}>
            {r}
          </button>
        ))}
      </div>
    </div>
  );
}
