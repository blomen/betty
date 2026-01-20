import React, { useState } from 'react';
import { Settings, Shield, Zap, TrendingUp } from 'lucide-react';
import Panel from '../ui/Panel';

const RiskConfigPanel = ({ playableEquity }) => {
    // Value Bet Settings (Kelly)
    const [kellyFraction, setKellyFraction] = useState(0.30);
    const [maxStakePct, setMaxStakePct] = useState(2.0);

    // Arbitrage Settings (Liquidity)
    const [arbLiquidityPct, setArbLiquidityPct] = useState(95);

    // Calc Reference Values
    const maxStakeAmount = (playableEquity * (maxStakePct / 100)).toFixed(0);
    const arbDeployableAmount = (playableEquity * (arbLiquidityPct / 100)).toFixed(0);

    return (
        <Panel title="Risk Configuration">
            <div className="flex flex-col h-full divide-y divide-[#333333]">

                {/* Section 1: Value Bets (Kelly) */}
                <div className="p-6 space-y-6">
                    <div className="flex items-center gap-2 mb-2">
                        <TrendingUp size={16} className="text-orange-500" />
                        <h3 className="text-sm font-bold text-white uppercase tracking-wider">Value Bets (Kelly)</h3>
                    </div>

                    {/* Kelly Multiplier */}
                    <div>
                        <div className="flex justify-between items-end mb-2">
                            <label className="text-xs font-bold text-[#cccccc] flex items-center gap-2">
                                Multiplier
                            </label>
                            <span className="font-mono font-bold text-orange-400 text-sm">{kellyFraction.toFixed(2)}x</span>
                        </div>
                        <input
                            type="range"
                            min="0.05"
                            max="1.00"
                            step="0.01"
                            value={kellyFraction}
                            onChange={(e) => setKellyFraction(parseFloat(e.target.value))}
                            className="w-full accent-orange-500 h-1 bg-[#333333] rounded-lg appearance-none cursor-pointer"
                        />
                    </div>

                    {/* Max Stake Cap */}
                    <div>
                        <div className="flex justify-between items-end mb-2">
                            <label className="text-xs font-bold text-[#cccccc] flex items-center gap-2">
                                Max Stake Cap
                            </label>
                            <div className="text-right">
                                <span className="font-mono font-bold text-orange-400 text-sm">{maxStakePct.toFixed(1)}%</span>
                                <span className="ml-2 text-[10px] text-[#555555] font-mono">≈ {parseInt(maxStakeAmount).toLocaleString()} SEK</span>
                            </div>
                        </div>
                        <input
                            type="range"
                            min="0.5"
                            max="5.0"
                            step="0.1"
                            value={maxStakePct}
                            onChange={(e) => setMaxStakePct(parseFloat(e.target.value))}
                            className="w-full accent-orange-500 h-1 bg-[#333333] rounded-lg appearance-none cursor-pointer"
                        />
                    </div>
                </div>

                {/* Section 2: Arbitrage (Max Liquidity) */}
                <div className="p-6 space-y-6 bg-[#1e1e1e]/50">
                    <div className="flex items-center gap-2 mb-2">
                        <Zap size={16} className="text-sky-500" />
                        <h3 className="text-sm font-bold text-white uppercase tracking-wider">Arbitrage (Liquidity)</h3>
                    </div>

                    <div>
                        <div className="flex justify-between items-end mb-2">
                            <label className="text-xs font-bold text-[#cccccc] flex items-center gap-2">
                                Liquidity Utilization
                            </label>
                            <div className="text-right">
                                <span className="font-mono font-bold text-sky-400 text-sm">{arbLiquidityPct}%</span>
                            </div>
                        </div>
                        <input
                            type="range"
                            min="50"
                            max="100"
                            step="5"
                            value={arbLiquidityPct}
                            onChange={(e) => setArbLiquidityPct(parseFloat(e.target.value))}
                            className="w-full accent-sky-500 h-1 bg-[#333333] rounded-lg appearance-none cursor-pointer"
                        />
                        <div className="mt-2 text-[10px] text-[#666666] flex justify-between">
                            <span>Available to Arb:</span>
                            <span className="font-mono text-white font-bold">{parseInt(arbDeployableAmount).toLocaleString()} SEK</span>
                        </div>
                        <p className="mt-3 text-[10px] text-[#555555] italic">
                            Since Arbs are theoretically risk-free, we maximize capital efficiency.
                            {100 - arbLiquidityPct}% is kept as a buffer for rounding/fees.
                        </p>
                    </div>
                </div>

            </div>
        </Panel>
    );
};

export default RiskConfigPanel;
