import React, { useState } from 'react';
import Panel from '../ui/Panel';
import DataGrid from '../ui/DataGrid';
import AccountCard from './AccountCard';
import { Wallet, History, Radio } from 'lucide-react';
import clsx from 'clsx';
import { formatDateTime } from '../../utils/formatters';

const ProfileDetail = ({ profile }) => {
    const [activeTab, setActiveTab] = useState('overview');

    const historyColumns = [
        { header: "Time", accessor: "date", className: "w-32", render: row => formatDateTime(row.date) },
        { header: "Event", accessor: "event" },
        { header: "Type", accessor: "type", className: "w-20", render: row => <span className="text-[10px] px-1 bg-[#111111] border border-[#333333] text-[#999999] rounded">{row.type}</span> },
        { header: "Amount", accessor: "amount", className: "w-24 text-right", render: row => <span className={row.amount > 0 ? "text-green-400" : "text-red-400"}>{row.amount > 0 ? '+' : ''}{row.amount}</span> },
        { header: "Status", accessor: "status", className: "w-24 text-center text-[10px] uppercase" }
    ];

    return (
        <div className="flex-1 flex flex-col min-w-0">
            {/* Header / Stats */}
            <div className="bg-[#1e1e1e] border-b border-[#333333] p-4 flex justify-between items-center shrink-0">
                <div>
                    <h2 className="text-xl font-bold text-white mb-1">{profile.name}</h2>
                    <div className="flex gap-4 text-xs">
                        <span className="text-[#888888]">ID: <span className="font-mono text-[#cccccc]">{profile.id}</span></span>
                        <span className="text-[#888888]">Status: <span className="text-green-400 font-bold">{profile.status}</span></span>
                    </div>
                </div>
                <div className="text-right">
                    <div className="text-[10px] uppercase text-[#555555] font-bold">Total Net Worth</div>
                    <div className="text-2xl font-mono font-bold text-green-400">{profile.total_equity.toLocaleString()} SEK</div>
                </div>
            </div>

            {/* Tabs */}
            <div className="bg-[#252526] border-b border-[#333333] flex px-2 shrink-0">
                {[
                    { id: 'overview', label: 'Accounts Overview', icon: Wallet },
                    { id: 'history', label: 'Transaction History', icon: History },
                    { id: 'bets', label: 'Active Bets', icon: Radio },
                ].map(tab => (
                    <button
                        key={tab.id}
                        onClick={() => setActiveTab(tab.id)}
                        className={clsx(
                            "flex items-center gap-2 px-4 py-2 text-[10px] uppercase font-bold border-r border-[#333333] transition-colors",
                            activeTab === tab.id ? "bg-[#1e1e1e] text-white border-t-2 border-t-sky-500" : "text-[#666666] hover:bg-[#2a2a2b] hover:text-[#cccccc]"
                        )}
                    >
                        <tab.icon size={14} />
                        {tab.label}
                    </button>
                ))}
            </div>

            {/* Content Content */}
            <div className="flex-1 bg-[#111111] overflow-auto p-4">
                {activeTab === 'overview' && (
                    <div>
                        <h3 className="text-[#555555] text-[10px] uppercase font-bold mb-3 tracking-widest">Linked Accounts</h3>
                        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
                            {profile.bankroll.map((account, i) => (
                                <AccountCard key={i} account={account} />
                            ))}
                        </div>
                    </div>
                )}

                {activeTab === 'history' && (
                    <Panel title="Transaction Log">
                        <DataGrid
                            columns={historyColumns}
                            data={profile.history}
                            loading={false}
                            emptyMessage="No history found."
                        />
                    </Panel>
                )}

                {activeTab === 'bets' && (
                    <div className="p-8 text-center text-[#555555] italic text-xs">
                        No active bets running for this identity.
                    </div>
                )}
            </div>
        </div>
    );
};

export default ProfileDetail;
