import React, { useState, useEffect } from 'react';
import BankrollSummary from '../components/BankrollSummary';
import { ArrowUpRight, RefreshCw } from 'lucide-react';
import Layout from '../components/ui/Layout';
import Panel from '../components/ui/Panel';
import DataGrid from '../components/ui/DataGrid';
import FilterBar from '../components/ui/FilterBar';
import { opportunities } from '../utils/api';

const ValueBetsPage = () => {
    const [bets, setBets] = useState([]);
    const [loading, setLoading] = useState(true);
    const [minEdge, setMinEdge] = useState(2.0);

    const fetchBets = async () => {
        try {
            const data = await opportunities.value();
            // Transform to expected format
            const formatted = (data.opportunities || []).map(opp => ({
                id: opp.id,
                start_time: opp.detected_at,
                match_name: opp.event_id,
                sport: opp.market?.includes('_') ? opp.market.split('_')[0] : 'SPORT',
                market_type: opp.market,
                selection: opp.outcome1 || 'Home',
                provider: opp.provider1,
                odds: opp.odds1,
                fair_odds: opp.odds2 || (opp.odds1 / (1 + (opp.edge_pct || 0) / 100)),
                edge_pct: opp.edge_pct || 0,
                num_books: 1,
            })).filter(b => b.edge_pct >= minEdge);
            setBets(formatted);
        } catch (err) {
            console.error("Failed to fetch value bets", err);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        fetchBets();
        const interval = setInterval(fetchBets, 5000);
        return () => clearInterval(interval);
    }, [minEdge]);

    const formatDateTime = (dateStr) => {
        if (!dateStr) return '-';
        const d = new Date(dateStr);
        return d.toLocaleTimeString('sv-SE', { hour: '2-digit', minute: '2-digit' });
    };

    const columns = [
        {
            header: "Time",
            accessor: "start_time",
            className: "w-20",
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
            header: "Sel",
            accessor: "selection",
            className: "w-16 text-center",
            cellClassName: "text-center",
            render: (row) => (
                <span className="px-1.5 py-0.5 bg-[#222222] border border-[#444444] rounded text-[#cccccc] text-[10px] font-bold">
                    {row.selection}
                </span>
            )
        },
        {
            header: "Value Offer",
            accessor: "odds",
            className: "w-28 text-right",
            cellClassName: "text-right",
            render: (row) => (
                <>
                    <span className="text-sky-500 text-[10px] uppercase font-bold mr-2">{row.provider}</span>
                    <span className="text-white font-mono font-bold text-sm bg-sky-900/30 px-1 rounded">
                        {row.odds?.toFixed(2)}
                    </span>
                </>
            )
        },
        {
            header: "Fair",
            accessor: "fair_odds",
            className: "w-16 text-right",
            cellClassName: "text-right font-mono text-[#888888] text-xs",
            render: (row) => row.fair_odds?.toFixed(2) || '-'
        },
        {
            header: "Edge",
            accessor: "edge_pct",
            className: "w-16 text-right",
            cellClassName: "text-right font-mono font-bold text-green-400",
            render: (row) => `+${row.edge_pct?.toFixed(1) || 0}%`
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

            <FilterBar>
                <button
                    onClick={fetchBets}
                    className="px-3 py-0.5 mt-0.5 bg-[#333333] text-[#cccccc] text-[10px] uppercase font-bold border border-[#444444] rounded-t hover:bg-[#444444] flex items-center gap-1"
                >
                    <RefreshCw size={10} /> Refresh
                </button>
                <select
                    value={minEdge}
                    onChange={(e) => setMinEdge(Number(e.target.value))}
                    className="px-2 py-0.5 mt-0.5 bg-[#252526] text-[#cccccc] text-[10px] uppercase font-bold border border-[#444444] rounded-t"
                >
                    <option value={1}>Min Edge: 1%</option>
                    <option value={2}>Min Edge: 2%</option>
                    <option value={3}>Min Edge: 3%</option>
                    <option value={5}>Min Edge: 5%</option>
                </select>
                <span className="px-3 py-0.5 mt-0.5 text-[#666666] text-[10px] uppercase font-bold">
                    {bets.length} Found
                </span>
            </FilterBar>

            <div className="flex-1 flex overflow-hidden">
                <Panel title="Value Bet Screener" count={`${bets.length} Opportunities`}>
                    <DataGrid
                        columns={columns}
                        data={bets}
                        loading={loading}
                        emptyMessage="No value bets found meeting criteria. Run extraction to find +EV bets."
                    />
                </Panel>
            </div>

            <div className="bg-[#007acc] h-5 flex items-center px-2 text-[10px] text-white font-bold select-none shrink-0">
                VALUE SCANNER ACTIVE • MIN EDGE: {minEdge}%
            </div>
        </Layout>
    );
};

export default ValueBetsPage;
