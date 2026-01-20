import React, { useState } from 'react';
import { Settings, Shield, AlertTriangle } from 'lucide-react';
import Panel from '../ui/Panel';

const KellyConfigPanel = ({ playableEquity }) => {
    const [kellyFraction, setKellyFraction] = useState(0.30);
    const [maxStakePct, setMaxStakePct] = useState(2.0);

    // Calculated Reference Values
    const maxStakeAmount = (playableEquity * (maxStakePct / 100)).toFixed(0);

    return (
        <Panel title="Risk Configuration (Kelly)">
            <div className="p-6 space-y-8">

                {/* Kelly Multiplier Slider */}
                <div>
                    <div className="flex justify-between items-end mb-2">
                        <label className="text-xs uppercase font-bold text-[#cccccc] flex items-center gap-2">
                            <Settings size={12} className="text-sky-500" />
                            Kelly Multiplier
                        </label>
                        <span className="font-mono font-bold text-sky-400 text-lg">{kellyFraction.toFixed(2)}x</span>
                    </div>
                    <input
                        type="range"
                        min="0.05"
                        max="1.00"
                        step="0.01"
                        value={kellyFraction}
                        onChange={(e) => setKellyFraction(parseFloat(e.target.value))}
                        className="w-full accent-sky-500 h-1 bg-[#333333] rounded-lg appearance-none cursor-pointer"
                    />
                    <div className="flex justify-between text-[10px] text-[#555555] mt-1 font-mono">
                        <span>Low Risk (0.1)</span>
                        <span>Aggressive (1.0)</span>
                    </div>
                </div>

                {/* Max Stake Slider */}
                <div>
                    <div className="flex justify-between items-end mb-2">
                        <label className="text-xs uppercase font-bold text-[#cccccc] flex items-center gap-2">
                            <Shield size={12} className="text-green-500" />
                            Max Stake Cap
                        </label>
                        <div className="text-right">
                            <span className="font-mono font-bold text-green-400 text-lg">{maxStakePct.toFixed(1)}%</span>
                            <span className="block text-[10px] text-[#555555] font-mono">≈ {parseInt(maxStakeAmount).toLocaleString()} SEK</span>
                        </div>
                    </div>
                    <input
                        type="range"
                        min="0.5"
                        max="5.0"
                        step="0.1"
                        value={maxStakePct}
                        onChange={(e) => setMaxStakePct(parseFloat(e.target.value))}
                        className="w-full accent-green-500 h-1 bg-[#333333] rounded-lg appearance-none cursor-pointer"
                    />
                    <div className="flex justify-between text-[10px] text-[#555555] mt-1 font-mono">
                        <span>Conservative (0.5%)</span>
                        <span>Degen (5.0%)</span>
                    </div>
                </div>

                {/* Info / Warning Box */}
                <div className="bg-[#252526] border border-[#333333] p-3 rounded flex gap-3 items-start">
                    <AlertTriangle size={16} className="text-yellow-500 shrink-0 mt-0.5" />
                    <div>
                        <h4 className="text-[10px] uppercase font-bold text-[#dddddd] mb-1">Impact on Stakes</h4>
                        <p className="text-[10px] text-[#888888] leading-relaxed">
                            These settings apply globally to the Arbitrage and Value Bet scanners.
                            Your effective playable bankroll of <span className="text-[#cccccc] font-bold">{playableEquity.toLocaleString()} SEK</span> will be used to calculate optimal bet sizes.
                        </p>
                    </div>
                </div>

            </div>
        </Panel>
    );
};

export default KellyConfigPanel;
