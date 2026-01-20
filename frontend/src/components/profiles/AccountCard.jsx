import React from 'react';
import clsx from 'clsx';
import { RefreshCw, ExternalLink } from 'lucide-react';

const AccountCard = ({ account }) => {
    return (
        <div className="bg-[#1e1e1e] border border-[#333333] p-3 rounded group hover:border-[#555555] transition-colors relative">
            <div className="flex justify-between items-start">
                <span className="font-bold text-[#dddddd] text-sm">{account.provider}</span>
                <span className={clsx(
                    "text-[9px] px-1.5 py-0.5 rounded font-bold uppercase tracking-wider",
                    account.status === 'Logged In' ? "bg-green-900/30 text-green-400" :
                        account.status === 'Locked' ? "bg-red-900/30 text-red-400" : "bg-gray-800 text-gray-400"
                )}>
                    {account.status}
                </span>
            </div>

            <div className="mt-3">
                <div className="text-[10px] uppercase text-[#555555] font-bold">Balance</div>
                <div className="text-lg font-mono font-bold text-white">{account.balance.toLocaleString()} SEK</div>
            </div>

            <div className="mt-3 pt-2 border-t border-[#333333] flex justify-between opacity-0 group-hover:opacity-100 transition-opacity">
                <button className="text-[10px] text-[#666666] hover:text-white flex items-center gap-1">
                    <RefreshCw size={10} /> Sync
                </button>
                <button className="text-[10px] text-[#666666] hover:text-sky-400 flex items-center gap-1">
                    <ExternalLink size={10} /> Open
                </button>
            </div>
        </div>
    );
};

export default AccountCard;
