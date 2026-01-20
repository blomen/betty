import React, { useState, useEffect } from 'react';
import BankrollSummary from '../components/BankrollSummary';
import { Zap, RefreshCw, ArrowUpRight } from 'lucide-react';
import Layout from '../components/ui/Layout';
import Panel from '../components/ui/Panel';
import DataGrid from '../components/ui/DataGrid';
import { providers as providersApi, opportunities } from '../utils/api';

const BonusExtractionPage = () => {
    const [providerList, setProviderList] = useState([]);
    const [matches, setMatches] = useState([]);
    const [loading, setLoading] = useState(true);
    const [anchorProvider, setAnchorProvider] = useState('');

    useEffect(() => {
        // Fetch providers
        providersApi.list()
            .then(data => {
                const list = data.providers || [];
                setProviderList(list);
                if (list.length > 0) {
                    setAnchorProvider(list[0].id);
                }
            })
            .catch(err => console.error("Failed to fetch providers", err));
    }, []);

    const fetchMatches = async () => {
        setLoading(true);
        try {
            const data = await opportunities.bonus();
            // Transform to expected format
            const formatted = (data.opportunities || []).map(opp => ({
                id: opp.id,
                start_time: opp.detected_at,
                match_name: opp.event_id,
                sport: 'SPORT',
                market_type: opp.market,
                anchor_odds: opp.odds1,
                anchor_selection: opp.outcome1 || 'A',
                legs: opp.provider2 ? [{
                    provider: opp.provider2,
                    odds: opp.odds2,
                    selection: opp.outcome2 || 'B',
                    has_bonus: false,
                }] : [],
                yield_pct: opp.profit_pct || opp.edge_pct || 0,
                bonus_count: 1,
            }));
            setMatches(formatted);
        } catch (err) {
            console.error("Failed to fetch bonus matches", err);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        fetchMatches();
    }, [anchorProvider]);

    const formatDateTime = (dateStr) => {
        if (!dateStr) return '-';
        const d = new Date(dateStr);
        return d.toLocaleTimeString('sv-SE', { hour: '2-digit', minute: '2-digit' });
    };

    const columns = [
        {
            header: "Time",
            accessor: "start_time",
            className: "w-28",
            cellClassName: "text-[#666666] font-mono text-[10px] whitespace-nowrap",
            render: (row) => formatDateTime(row.start_time)
        },
        {
            header: "Event",
            accessor: "match_name",
            render: (row) => (
                <>
                    <div className="font-bold text-[#dddddd] group-hover:text-white">
                        {row.match_name?.substring(0, 30)}...
                    </div>
                    <div className="text-[10px] text-[#555555] uppercase">{row.sport}</div>
                </>
            )
        },
        {
            header: "Market",
            accessor: "market_type",
            className: "w-24",
            cellClassName: "text-[#aaaaaa] text-[10px] uppercase font-bold"
        },
        {
            header: "Anchor (Bonus)",
            accessor: "anchor_selection",
            render: (row) => (
                <div className="flex items-center gap-2">
                    <span className="text-white font-bold">{anchorProvider}</span>
                    <span className="font-mono text-sm px-1.5 py-0.5 rounded bg-[#222222] border border-[#444444] text-[#cccccc]">
                        {row.anchor_odds?.toFixed(2)}
                    </span>
                    <span className="text-[#555555] text-[10px] font-bold">({row.anchor_selection})</span>
                </div>
            )
        },
        {
            header: "Counter Match",
            accessor: "legs",
            render: (row) => (
                <div className="flex flex-col gap-1">
                    {row.legs?.map((leg, i) => (
                        <div key={i} className="flex items-center gap-2">
                            <span className="text-sky-500 font-bold text-[10px] uppercase w-16 truncate">{leg.provider}</span>
                            <span className="font-mono text-white text-sm bg-sky-900/30 px-1 rounded">{leg.odds?.toFixed(2)}</span>
                            <span className="text-[#555555] text-[10px] font-bold">({leg.selection})</span>
                        </div>
                    ))}
                </div>
            )
        },
        {
            header: "Yield",
            accessor: "yield_pct",
            className: "w-16 text-right",
            cellClassName: "text-right font-mono font-bold",
            render: (row) => (
                <span className={row.yield_pct >= 0 ? 'text-green-400' : 'text-red-400'}>
                    {row.yield_pct?.toFixed(2)}%
                </span>
            )
        },
        {
            header: "Act",
            accessor: "act",
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

            {/* Control Bar */}
            <div className="bg-[#252526] border-b border-[#333333] px-4 py-3 flex items-center gap-6 flex-wrap shrink-0">
                <div className="flex items-center gap-2">
                    <span className="text-[10px] uppercase font-bold text-[#555555]">Anchor:</span>
                    <select
                        value={anchorProvider}
                        onChange={(e) => setAnchorProvider(e.target.value)}
                        className="bg-[#1e1e1e] border border-[#444444] text-white px-3 py-1.5 text-sm font-bold focus:outline-none focus:border-sky-500"
                    >
                        {providerList.map(p => (
                            <option key={p.id} value={p.id}>
                                {p.name} ({p.balance} SEK)
                            </option>
                        ))}
                    </select>
                </div>

                <button
                    onClick={fetchMatches}
                    className="px-3 py-1.5 bg-[#333333] text-[#cccccc] text-[10px] uppercase font-bold border border-[#444444] hover:bg-[#444444] flex items-center gap-1"
                >
                    <RefreshCw size={10} /> Refresh
                </button>

                <div className="flex items-center gap-2 px-3 py-1 bg-sky-900/30 border border-sky-700 rounded">
                    <Zap size={12} className="text-sky-400" />
                    <span className="text-xs font-bold text-sky-400">{matches.length} Matches</span>
                </div>
            </div>

            <div className="flex-1 flex overflow-hidden">
                <Panel title="Bonus Matcher" count={`${matches.length} Matches`}>
                    <DataGrid
                        columns={columns}
                        data={matches}
                        loading={loading}
                        emptyMessage="No bonus matching opportunities. Run extraction to find matches."
                    />
                </Panel>
            </div>

            <div className="bg-[#007acc] h-5 flex items-center px-2 text-[10px] text-white font-bold select-none shrink-0">
                BONUS ENGINE ACTIVE • ANCHOR: {anchorProvider.toUpperCase()}
            </div>
        </Layout>
    );
};

export default BonusExtractionPage;
