import React, { useState, useEffect } from 'react';
import BankrollSummary from '../components/BankrollSummary';
import { ArrowUpRight, RefreshCw } from 'lucide-react';
import Layout from '../components/ui/Layout';
import Panel from '../components/ui/Panel';
import DataGrid from '../components/ui/DataGrid';
import FilterBar from '../components/ui/FilterBar';
import { opportunities } from '../utils/api';

const ArbitragePage = () => {
    const [opps, setOpps] = useState([]);
    const [loading, setLoading] = useState(true);

    const fetchOpps = async () => {
        try {
            const data = await opportunities.arbitrage();
            // Transform to expected format
            const formatted = (data.opportunities || []).map(opp => ({
                id: opp.id,
                created_at: opp.detected_at,
                match_name: opp.event_id,
                market_type: opp.market,
                roi_pct: opp.profit_pct || 0,
                bets: [
                    { provider: opp.provider1, odds: opp.odds1, selection: opp.outcome1 || 'A' },
                    opp.provider2 && { provider: opp.provider2, odds: opp.odds2, selection: opp.outcome2 || 'B' },
                ].filter(Boolean),
            }));
            setOpps(formatted);
        } catch (err) {
            console.error("Failed to fetch opportunities", err);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        fetchOpps();
        const interval = setInterval(fetchOpps, 5000);
        return () => clearInterval(interval);
    }, []);

    const formatDateTime = (dateStr) => {
        if (!dateStr) return '-';
        const d = new Date(dateStr);
        return d.toLocaleTimeString('sv-SE', { hour: '2-digit', minute: '2-digit' });
    };

    const columns = [
        {
            header: "Time",
            accessor: "created_at",
            className: "w-28",
            cellClassName: "text-[#666666] font-mono text-[10px] whitespace-nowrap",
            render: (row) => formatDateTime(row.created_at)
        },
        {
            header: "Event",
            accessor: "match_name",
            cellClassName: "font-bold text-[#dddddd] group-hover:text-white",
            render: (row) => row.match_name?.substring(0, 35) + (row.match_name?.length > 35 ? '...' : '')
        },
        {
            header: "Market",
            accessor: "market_type",
            className: "w-24",
            cellClassName: "text-[#aaaaaa] text-[10px] uppercase font-bold"
        },
        {
            header: "ROI",
            accessor: "roi_pct",
            className: "w-16 text-right",
            cellClassName: "text-right font-mono font-bold text-green-400",
            render: (row) => `+${row.roi_pct?.toFixed(2) || 0}%`
        },
        {
            header: "Legs",
            accessor: "bets",
            render: (row) => (
                <div className="flex flex-wrap gap-1.5 py-1">
                    {row.bets?.map((b, i) => (
                        <span key={i} className="bg-[#111111] px-2 py-0.5 rounded border border-[#333333] text-[#cccccc] flex items-center gap-1.5 whitespace-nowrap">
                            <span className="text-sky-500 font-bold text-[9px] uppercase tracking-tighter">{b.provider}</span>
                            <span className="font-mono text-white font-bold">{b.odds?.toFixed(2)}</span>
                            <span className="text-[#555555] text-[8px] uppercase">{b.selection}</span>
                        </span>
                    ))}
                </div>
            )
        },
        {
            header: "Act",
            accessor: "id",
            className: "w-12 text-center",
            cellClassName: "text-center",
            render: () => (
                <button className="text-[#444444] hover:text-sky-400 transition-colors">
                    <ArrowUpRight size={14} />
                </button>
            )
        }
    ];

    return (
        <Layout>
            <BankrollSummary />

            <FilterBar>
                <button
                    onClick={fetchOpps}
                    className="px-3 py-0.5 mt-0.5 bg-[#333333] text-[#cccccc] text-[10px] uppercase font-bold border border-[#444444] rounded-t hover:bg-[#444444] flex items-center gap-1"
                >
                    <RefreshCw size={10} /> Refresh
                </button>
                <span className="px-3 py-0.5 mt-0.5 text-[#666666] text-[10px] uppercase font-bold">
                    {opps.length} Active
                </span>
            </FilterBar>

            <div className="flex-1 flex overflow-hidden">
                <Panel title="Arbitrage Scanner" count={`${opps.length} Events`}>
                    <DataGrid
                        columns={columns}
                        data={opps}
                        loading={loading}
                        emptyMessage="No active arbitrage opportunities. Run extraction to detect arbs."
                    />
                </Panel>

                <div className="w-64 bg-[#1e1e1e] border-l border-[#333333] hidden lg:flex flex-col">
                    <div className="qt-header">
                        <span>Stake Calculator</span>
                    </div>
                    <div className="p-4 space-y-3">
                        <div>
                            <label className="text-[10px] text-[#555555] uppercase font-bold block mb-1">Total Stake</label>
                            <input
                                type="number"
                                defaultValue={100}
                                className="w-full bg-[#111111] border border-[#333333] px-2 py-1 text-white font-mono text-sm"
                            />
                        </div>
                        <p className="text-[10px] text-[#444444]">
                            Select an arb to calculate stake distribution.
                        </p>
                    </div>
                </div>
            </div>

            <div className="bg-[#007acc] h-5 flex items-center px-2 text-[10px] text-white font-bold select-none shrink-0">
                ARBITRAGE SCANNER ACTIVE • POLLING EVERY 5s
            </div>
        </Layout>
    );
};

export default ArbitragePage;
