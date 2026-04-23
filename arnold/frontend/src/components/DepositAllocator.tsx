import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/services/api';
import { Card } from '@/components/Card';
import { ProviderName } from '@/components/ProviderName';
import { useDebouncedValue } from '@/hooks/useDebouncedValue';
import type { AllocationEnvelope } from '@/types';

const TIER_LABELS: Record<1 | 2 | 3, string> = {
  1: 'bonus',
  2: 'top-up',
  3: 'valuebets',
};

export function DepositAllocator() {
  const [raw, setRaw] = useState<string>('');
  const debounced = useDebouncedValue(raw, 300);
  const numericInput = debounced.trim() === '' ? null : Number(debounced);
  const validInput =
    numericInput === null || (!Number.isNaN(numericInput) && numericInput >= 0);

  const { data, isLoading } = useQuery<AllocationEnvelope>({
    queryKey: ['bankroll', 'allocate', numericInput],
    queryFn: () => api.allocate(numericInput),
    enabled: validInput,
    staleTime: 15_000,
  });

  if (!data) {
    return (
      <Card title="Deposit Allocator">
        <div className="text-muted text-sm py-2">
          {isLoading ? 'Computing…' : 'No data.'}
        </div>
      </Card>
    );
  }

  const {
    current_liquid, withdrawals, effective_budget, deposits, keep_liquid, recommended_total,
  } = data;
  const isBlank = numericInput === null;

  return (
    <Card title="Deposit Allocator">
      <div className="space-y-3 text-xs">
        <div className="flex items-center gap-2">
          <label className="text-muted">I want to deposit:</label>
          <input
            type="number"
            min={0}
            value={raw}
            placeholder={recommended_total.toFixed(0)}
            onChange={(e) => setRaw(e.target.value)}
            className="w-28 px-2 py-1 bg-panel2 border border-border text-text"
          />
          <span className="text-muted">kr</span>
          <span className="text-muted2 ml-auto">
            Current liquid: {current_liquid.toFixed(0)} kr
          </span>
        </div>

        {withdrawals.length > 0 && (
          <section>
            <div className="text-muted uppercase tracking-wider text-[10px] mb-1">
              Withdraw from idle
            </div>
            <table className="sq">
              <tbody>
                {withdrawals.map((w) => (
                  <tr key={w.provider_id}>
                    <td className="text-text"><ProviderName name={w.provider_name} /></td>
                    <td className="text-right text-warning">−{w.amount_sek.toFixed(0)} kr</td>
                    <td className="text-right text-muted2">{w.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        )}

        {!isBlank && effective_budget !== null && (
          <div className="text-muted">
            Effective budget: <span className="text-text">{effective_budget.toFixed(0)} kr</span>
          </div>
        )}

        <section>
          <div className="text-muted uppercase tracking-wider text-[10px] mb-1">
            {isBlank ? 'Recommended allocation' : 'Allocate'}
          </div>
          {deposits.length === 0 ? (
            <div className="text-muted2 py-2">Nothing to allocate right now.</div>
          ) : (
            <table className="sq">
              <tbody>
                {deposits.map((d) => (
                  <tr key={`${d.priority}-${d.provider_id}`}>
                    <td className="w-6 text-muted2">{d.priority}</td>
                    <td className="text-text"><ProviderName name={d.provider_name} /></td>
                    <td className="text-right text-tabBankroll font-medium">
                      {d.amount.toFixed(0)} kr
                    </td>
                    <td className="text-right text-muted">{d.unlocks}</td>
                    <td className="text-right text-success">
                      {d.expected_ev > 0 ? `+${d.expected_ev.toFixed(0)} kr EV` : ''}
                    </td>
                    <td className="text-right text-muted2">{TIER_LABELS[d.priority]}</td>
                  </tr>
                ))}
                {keep_liquid > 0 && (
                  <tr>
                    <td className="w-6 text-muted2">–</td>
                    <td className="text-muted">keep liquid</td>
                    <td className="text-right text-muted">{keep_liquid.toFixed(0)} kr</td>
                    <td colSpan={3}></td>
                  </tr>
                )}
              </tbody>
            </table>
          )}
        </section>
      </div>
    </Card>
  );
}
