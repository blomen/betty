import React, { useState, useEffect } from 'react';
import BankrollSummary from '../components/BankrollSummary';
import { Activity, TrendingUp, Zap, Clock, Newspaper, ShieldCheck, RefreshCw, Loader } from 'lucide-react';
import { bankroll, opportunities, bets, extraction, events } from '../utils/api';

const HomePage = () => {
    const [stats, setStats] = useState({ total_bets: 0, wins: 0, losses: 0, total_profit: 0, roi_pct: 0 });
    const [opps, setOpps] = useState([]);
    const [recentBets, setRecentBets] = useState([]);
    const [loading, setLoading] = useState(true);
    const [extracting, setExtracting] = useState(false);
    const [extractionStatus, setExtractionStatus] = useState(null);
    const [eventCount, setEventCount] = useState(0);

    const fetchDashboard = async () => {
        try {
            const [statsData, oppsData, betsData, eventsData] = await Promise.all([
                bankroll.getStats(),
                opportunities.list(),
                bets.list(null, 5),
                events.list(null, 100),
            ]);
            setStats(statsData);
            setOpps(oppsData.opportunities || []);
            setRecentBets(betsData.bets || []);
            setEventCount(eventsData.count || 0);
            setLoading(false);
        } catch (err) {
            console.error("Dashboard fetch failed", err);
            setLoading(false);
        }
    };

    useEffect(() => {
        fetchDashboard();
    }, []);

    // Poll extraction status while running
    useEffect(() => {
        if (!extracting) return;

        const interval = setInterval(async () => {
            try {
                const status = await extraction.status();
                setExtractionStatus(status);
                if (!status.running) {
                    setExtracting(false);
                    fetchDashboard(); // Refresh after extraction completes
                }
            } catch (e) {
                console.error("Status poll failed", e);
            }
        }, 2000);

        return () => clearInterval(interval);
    }, [extracting]);

    const handleRunExtraction = async () => {
        try {
            setExtracting(true);
            await extraction.run('unibet,leovegas,casumo', 'football', 5);
        } catch (err) {
            console.error("Extraction failed", err);
            setExtracting(false);
        }
    };

    const fmt = (val) => new Intl.NumberFormat('sv-SE', {
        style: 'currency',
        currency: 'SEK',
        maximumFractionDigits: 0
    }).format(val);

    return (
        <div className="flex flex-col h-full bg-[#111111]">
            <BankrollSummary />

            <div className="flex-1 overflow-auto p-6 space-y-6">
                {/* Hero / Pulse Section */}
                <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                    <div className="lg:col-span-2 qt-panel p-6 bg-gradient-to-br from-[#1e1e1e] to-[#111111] border-l-4 border-l-sky-500">
                        <h2 className="text-xl font-bold text-white mb-2 uppercase tracking-tighter">System Pulse</h2>
                        <div className="flex items-center gap-2 mb-6">
                            <span className="w-2 h-2 rounded-full bg-green-500 animate-pulse"></span>
                            <span className="text-xs text-green-500 font-bold uppercase">Backend Online</span>
                        </div>

                        <div className="grid grid-cols-4 gap-4">
                            <div>
                                <p className="text-[10px] uppercase font-bold text-[#555555]">Total P&L</p>
                                <p className={`text-2xl font-mono font-bold ${stats.total_profit >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                                    {stats.total_profit >= 0 ? '+' : ''}{fmt(stats.total_profit)}
                                </p>
                            </div>
                            <div>
                                <p className="text-[10px] uppercase font-bold text-[#555555]">Active Opps</p>
                                <p className="text-2xl font-mono font-bold text-sky-400">{opps.length}</p>
                            </div>
                            <div>
                                <p className="text-[10px] uppercase font-bold text-[#555555]">Win Rate</p>
                                <p className="text-2xl font-mono font-bold text-white">
                                    {stats.total_bets > 0 ? ((stats.wins / stats.total_bets) * 100).toFixed(1) : 0}%
                                </p>
                            </div>
                            <div>
                                <p className="text-[10px] uppercase font-bold text-[#555555]">ROI</p>
                                <p className={`text-2xl font-mono font-bold ${stats.roi_pct >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                                    {stats.roi_pct.toFixed(1)}%
                                </p>
                            </div>
                        </div>
                    </div>

                    <div className="qt-panel p-6">
                        <h3 className="text-xs font-bold text-[#555555] uppercase mb-4 flex items-center gap-2">
                            <ShieldCheck size={14} className="text-sky-500" /> Quick Stats
                        </h3>
                        <div className="space-y-3">
                            <div className="flex justify-between text-xs">
                                <span className="text-[#888888]">Total Bets</span>
                                <span className="text-[#cccccc]">{stats.total_bets}</span>
                            </div>
                            <div className="flex justify-between text-xs">
                                <span className="text-[#888888]">Events in DB</span>
                                <span className="text-sky-400 font-mono">{eventCount}</span>
                            </div>
                            <div className="flex justify-between text-xs">
                                <span className="text-[#888888]">Wins / Losses</span>
                                <span className="text-[#cccccc]">
                                    <span className="text-green-500">{stats.wins}</span> / <span className="text-red-500">{stats.losses}</span>
                                </span>
                            </div>
                            <button
                                onClick={handleRunExtraction}
                                disabled={extracting}
                                className="w-full mt-2 py-2 bg-sky-600 hover:bg-sky-500 disabled:bg-[#333333] text-[10px] font-bold border border-sky-700 disabled:border-[#333333] transition-colors flex items-center justify-center gap-2"
                            >
                                {extracting ? (
                                    <>
                                        <Loader size={12} className="animate-spin" />
                                        EXTRACTING... {extractionStatus?.events || 0} events
                                    </>
                                ) : (
                                    <>
                                        <RefreshCw size={12} />
                                        RUN EXTRACTION
                                    </>
                                )}
                            </button>
                        </div>
                    </div>
                </div>

                {/* Main Dashboard Grid */}
                <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                    {/* Opportunities Feed */}
                    <div className="qt-panel overflow-hidden flex flex-col h-[400px]">
                        <div className="qt-header border-b border-[#333333]">
                            <TrendingUp size={14} className="mr-2 text-sky-500" /> Live Opportunities
                        </div>
                        <div className="flex-1 overflow-auto">
                            {opps.length === 0 ? (
                                <div className="p-4 text-center text-[#555555] text-xs">
                                    No opportunities detected. Run extraction to find arb/value bets.
                                </div>
                            ) : (
                                opps.slice(0, 10).map((opp, i) => (
                                    <div key={i} className="px-4 py-2 border-b border-[#252526] flex items-center gap-4 hover:bg-[#252526] transition-colors">
                                        <span className={`text-[10px] font-bold uppercase px-2 py-0.5 rounded ${opp.type === 'arb' ? 'bg-green-500/20 text-green-500' :
                                            opp.type === 'value' ? 'bg-sky-500/20 text-sky-500' :
                                                'bg-purple-500/20 text-purple-500'
                                            }`}>{opp.type}</span>
                                        <span className="text-xs text-[#cccccc] flex-1">{opp.event_id?.substring(0, 30)}...</span>
                                        <span className="text-xs font-mono text-green-400">
                                            +{(opp.profit_pct || opp.edge_pct || 0).toFixed(2)}%
                                        </span>
                                    </div>
                                ))
                            )}
                        </div>
                    </div>

                    {/* Recent Bets */}
                    <div className="qt-panel overflow-hidden flex flex-col h-[400px]">
                        <div className="qt-header border-b border-[#333333]">
                            <Activity size={14} className="mr-2 text-sky-500" /> Recent Bets
                        </div>
                        <div className="flex-1 overflow-auto">
                            {recentBets.length === 0 ? (
                                <div className="p-4 text-center text-[#555555] text-xs">
                                    No bets recorded yet.
                                </div>
                            ) : (
                                recentBets.map((bet, i) => (
                                    <div key={i} className="px-4 py-2 border-b border-[#252526] flex items-center gap-4 hover:bg-[#252526] transition-colors">
                                        <span className="text-[10px] font-mono text-[#555555]">{bet.provider}</span>
                                        <span className="text-xs text-[#cccccc] flex-1">@ {bet.odds?.toFixed(2)}</span>
                                        <span className={`text-xs font-mono ${bet.profit >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                                            {bet.profit >= 0 ? '+' : ''}{fmt(bet.profit || 0)}
                                        </span>
                                        <span className={`text-[10px] font-bold uppercase px-2 py-0.5 rounded ${bet.result === 'won' ? 'bg-green-500/20 text-green-500' :
                                            bet.result === 'lost' ? 'bg-red-500/20 text-red-500' :
                                                'bg-yellow-500/20 text-yellow-500'
                                            }`}>{bet.result}</span>
                                    </div>
                                ))
                            )}
                        </div>
                    </div>
                </div>
            </div>

            {/* Bottom Info Bar */}
            <div className="h-6 bg-[#007acc] flex items-center px-4 justify-between">
                <div className="flex items-center gap-4 text-[10px] text-white font-bold uppercase">
                    <span>Backend: localhost:8000</span>
                    <span>Providers: {loading ? '...' : 'Online'}</span>
                </div>
                <div className="text-[10px] text-sky-200 font-mono">
                    OddOpp v0.1.0
                </div>
            </div>
        </div>
    );
};

export default HomePage;
