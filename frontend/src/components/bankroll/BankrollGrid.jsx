import React, { useState } from 'react';
import DataGrid from '../ui/DataGrid';
import { RefreshCw, Lock, LogIn } from 'lucide-react';
import clsx from 'clsx';

const BankrollGrid = ({ accounts }) => {
    const [showPlayableOnly, setShowPlayableOnly] = useState(false);

    const filteredAccounts = showPlayableOnly
        ? accounts.filter(a => a.status === 'Logged In')
        : accounts;

    const totalEquity = accounts.reduce((sum, a) => sum + a.balance, 0);
    const playableEquity = accounts
        .filter(a => a.status === 'Logged In')
        .reduce((sum, a) => sum + a.balance, 0);

    const columns = [
        {
            header: "Provider",
            accessor: "provider",
            render: (row) => (
                <div className="font-bold text-[#dddddd]">{row.provider}</div>
            )
        },
        {
            header: "Status",
            accessor: "status",
            className: "w-24 text-center",
            cellClassName: "text-center",
            render: (row) => (
                <div className={clsx(
                    "inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[10px] uppercase font-bold tracking-wider border",
                    row.status === 'Logged In' ? "bg-green-900/30 border-green-800 text-green-400" :
                        row.status === 'Locked' ? "bg-red-900/30 border-red-800 text-red-400" :
                            "bg-gray-800 border-gray-700 text-gray-400"
                )}>
                    {row.status === 'Logged In' ? <LogIn size={10} /> :
                        row.status === 'Locked' ? <Lock size={10} /> : null}
                    {row.status}
                </div>
            )
        },
        {
            header: "Available Funds",
            accessor: "balance",
            className: "w-32 text-right",
            cellClassName: "text-right font-mono font-bold text-white",
            render: (row) => `${row.balance.toLocaleString()} SEK`
        },
        {
            header: "% of Pot",
            accessor: "pct",
            className: "w-24 text-right",
            cellClassName: "text-right text-[#666666] font-mono text-xs",
            render: (row) => {
                // If displaying "Playable Only", calc % relative to Playable Total? 
                // Usually % of Total Equity is more informative for risk.
                const pct = (row.balance / totalEquity) * 100;
                return `${pct.toFixed(1)}%`;
            }
        },
        {
            header: "Action",
            accessor: "act",
            className: "w-16 text-center",
            cellClassName: "text-center",
            render: (row) => (
                <button className="text-[#444444] hover:text-white transition-colors">
                    <RefreshCw size={12} />
                </button>
            )
        }
    ];

    return (
        <div className="flex flex-col h-full bg-[#1e1e1e]">
            <div className="qt-header justify-between">
                <span>Fund Distribution</span>
                <div className="flex items-center gap-2">
                    <span className="text-[10px] text-[#555555] uppercase font-bold mr-2">Show:</span>
                    <button
                        onClick={() => setShowPlayableOnly(false)}
                        className={clsx(
                            "text-[10px] px-2 py-0.5 rounded hover:text-white transition-colors",
                            !showPlayableOnly ? "bg-[#333333] text-white" : "text-[#666666]"
                        )}
                    >
                        ALL
                    </button>
                    <button
                        onClick={() => setShowPlayableOnly(true)}
                        className={clsx(
                            "text-[10px] px-2 py-0.5 rounded hover:text-white transition-colors",
                            showPlayableOnly ? "bg-[#333333] text-white" : "text-[#666666]"
                        )}
                    >
                        PLAYABLE
                    </button>
                </div>
            </div>

            <div className="flex-1 overflow-auto">
                <DataGrid
                    columns={columns}
                    data={filteredAccounts}
                    loading={false}
                />
            </div>

            {/* Summary Footer */}
            <div className="p-3 bg-[#252526] border-t border-[#333333] flex justify-between items-center text-xs">
                <div className="text-[#666666]">
                    Total Accounts: <span className="text-[#cccccc] font-bold">{accounts.length}</span>
                </div>
                <div className="flex gap-4">
                    <div className="text-right">
                        <span className="block text-[9px] uppercase font-bold text-[#555555]">Total Net Worth</span>
                        <span className="font-mono font-bold text-[#888888]">{totalEquity.toLocaleString()} SEK</span>
                    </div>
                    <div className="text-right border-l border-[#444444] pl-4">
                        <span className="block text-[9px] uppercase font-bold text-sky-500">Playable Funds</span>
                        <span className="font-mono font-bold text-white text-lg">{playableEquity.toLocaleString()} SEK</span>
                    </div>
                </div>
            </div>
        </div>
    );
};

export default BankrollGrid;
