import React from 'react';

const OpportunityCard = ({ opp }) => {
    return (
        <div className="glass-card p-6 flex flex-col gap-4 border-l-4 border-l-sky-500 hover:bg-slate-800/40 transition-colors cursor-pointer group">
            <div className="flex justify-between items-start">
                <div>
                    <h3 className="text-xl font-bold text-white tracking-tight group-hover:text-sky-400 transition-colors">{opp.match_name}</h3>
                    <p className="text-sm font-medium text-slate-400">{opp.market_type}</p>
                </div>
                <div className="flex flex-col items-end gap-2">
                    <span className="badge-arb">+{opp.roi_pct.toFixed(2)}% ROI</span>
                    <span className="text-xs font-mono text-slate-500">
                        {new Date(opp.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                    </span>
                </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
                {opp.bets.map((bet, i) => (
                    <div key={i} className="bg-slate-950/40 p-4 rounded-lg border border-slate-700/30 flex flex-col justify-between">
                        <div className="flex justify-between items-center mb-3">
                            <span className="text-[10px] font-black tracking-tighter text-sky-500 uppercase">{bet.provider}</span>
                            <span className="text-xl font-black text-white">{bet.odds.toFixed(2)}</span>
                        </div>
                        <div className="flex justify-between items-end">
                            <span className="text-xs font-bold text-slate-400">{bet.selection}</span>
                            <div className="text-right">
                                <p className="text-[10px] uppercase text-slate-500 font-bold">Stake</p>
                                <p className="text-sm font-bold text-slate-100">{bet.stake_pct.toFixed(1)}%</p>
                            </div>
                        </div>
                    </div>
                ))}
            </div>
        </div>
    );
};

export default OpportunityCard;
