import { useState, useEffect, useMemo } from 'react';
import { usePersistedState } from '@/hooks/usePersistedState';
import { useProfiles } from '@/hooks/useProfiles';
import { useStatsData, type StatsRange, RANGE_DAYS } from '@/hooks/useStatsData';
import { toEquityPoints } from '@/components/stats/equity';
import { TabIcon, TAB_COLORS } from '@/components/TabBar';
import { StatsHeader } from '@/components/stats/StatsHeader';
import { KpiBlock } from '@/components/stats/KpiBlock';
import { BankrollChart, CLVChart } from '@/components/stats/charts';
import { StrategySplit } from '@/components/stats/StrategySplit';
import { EdgeAnalytics } from '@/components/stats/EdgeAnalytics';
import { BonusPanel } from '@/components/stats/BonusPanel';
import { BetHistory } from '@/components/stats/BetHistory';
import { ShadowCLVView } from '@/components/stats/ShadowCLV';

export function StatsPage() {
  const { activeProfile, profiles } = useProfiles();
  const [subTab, setSubTab] = usePersistedState<'profile' | 'shadow'>('bbq_stats_subTab_v2', 'profile');
  const [profileId, setProfileId] = useState<number | undefined>(undefined);
  const [range, setRange] = usePersistedState<StatsRange>('bbq_stats_range', '90d');

  useEffect(() => {
    if (profileId == null && activeProfile) setProfileId(activeProfile.id);
  }, [activeProfile, profileId]);

  const selected = profiles.find((p) => p.id === profileId);
  const { stats, equity, analytics, bets } = useStatsData(profileId, range);

  const equityPoints = equity.data ? toEquityPoints(equity.data.points, equity.data) : [];

  const historyBets = useMemo(() => {
    const all = bets.data?.bets ?? [];
    if (range === 'all') return all;
    const cutoff = Date.now() - RANGE_DAYS[range] * 86400_000;
    return all.filter((b) => new Date(b.placed_at).getTime() >= cutoff);
  }, [bets.data, range]);

  return (
    <div className="space-y-3 min-w-0 overflow-y-auto overflow-x-hidden flex-1 min-h-0">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-text flex items-center gap-2">
          <TabIcon name="stats" color={TAB_COLORS.stats} size={16} />
          Stats
        </h2>
      </div>

      <div className="flex items-center gap-1 -mx-1 -mt-1">
        <button onClick={() => setSubTab('profile')}
          className={`px-3 py-1 text-[11px] font-semibold uppercase tracking-wider rounded ${subTab === 'profile' ? 'bg-tabBets/30 text-tabBets border border-tabBets/40' : 'text-muted hover:text-text border border-transparent'}`}>
          Profile Stats
        </button>
        <button onClick={() => setSubTab('shadow')}
          title="Scanner CLV for every detected opp (profile-independent)"
          className={`px-3 py-1 text-[11px] font-semibold uppercase tracking-wider rounded ${subTab === 'shadow' ? 'bg-tabBets/30 text-tabBets border border-tabBets/40' : 'text-muted hover:text-text border border-transparent'}`}>
          Shadow CLV
        </button>
      </div>

      {subTab === 'shadow' && <ShadowCLVView />}

      {subTab === 'profile' && (
        <>
          <StatsHeader profileId={profileId} setProfileId={setProfileId} range={range} setRange={setRange} />

          {stats.data && <KpiBlock stats={stats.data} bankrollSek={equity.data?.current_bankroll_sek ?? 0} />}

          <div className="grid grid-cols-2 gap-[1px] bg-[#161b22]">
            {equityPoints.length >= 2 && (
              <BankrollChart points={equityPoints} totalStaked={equity.data?.total_staked_sek} />
            )}
            {bets.data && <CLVChart bets={bets.data.bets.filter((b) => !b.is_bonus)} />}
          </div>

          {selected?.style === 'bonus_extraction' ? (
            (analytics.data && profileId != null) ? <BonusPanel profileId={profileId} byProvider={analytics.data.by_provider} /> : null
          ) : (
            <>
              {analytics.data && <StrategySplit byStrategy={analytics.data.by_strategy} />}
              {analytics.data && <EdgeAnalytics analytics={analytics.data} />}
            </>
          )}

          {bets.data && (
            <BetHistory bets={historyBets} isLoading={bets.isLoading} refetch={() => bets.refetch()} />
          )}
        </>
      )}
    </div>
  );
}
