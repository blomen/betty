import React, { useState } from 'react';
import { X, ArrowDownLeft, ArrowUpRight, ArrowRight } from 'lucide-react';
import clsx from 'clsx';

const TransactionModal = ({ onClose, accounts, onSave }) => {
    const [type, setType] = useState('deposit'); // deposit, withdraw, transfer
    const [amount, setAmount] = useState('');
    const [providerId, setProviderId] = useState(accounts[0]?.provider || '');
    const [note, setNote] = useState('');

    const handleSubmit = (e) => {
        e.preventDefault();
        onSave({
            type,
            amount: parseFloat(amount),
            provider: providerId,
            note,
            date: new Date().toISOString()
        });
        onClose();
    };

    return (
        <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50">
            <div className="bg-[#1e1e1e] border border-[#333333] rounded w-96 shadow-2xl">
                <div className="flex justify-between items-center p-4 border-b border-[#333333]">
                    <h3 className="font-bold text-white">Log Transaction</h3>
                    <button onClick={onClose} className="text-[#666666] hover:text-white"><X size={18} /></button>
                </div>

                <form onSubmit={handleSubmit} className="p-4 space-y-4">
                    {/* Type Selector */}
                    <div className="flex gap-2">
                        <button
                            type="button"
                            onClick={() => setType('deposit')}
                            className={clsx(
                                "flex-1 py-2 text-xs font-bold uppercase rounded border transition-colors flex items-center justify-center gap-2",
                                type === 'deposit' ? "bg-green-900/30 border-green-800 text-green-400" : "bg-[#111111] border-[#333333] text-[#666666]"
                            )}
                        >
                            <ArrowDownLeft size={14} /> Deposit
                        </button>
                        <button
                            type="button"
                            onClick={() => setType('withdraw')}
                            className={clsx(
                                "flex-1 py-2 text-xs font-bold uppercase rounded border transition-colors flex items-center justify-center gap-2",
                                type === 'withdraw' ? "bg-red-900/30 border-red-800 text-red-400" : "bg-[#111111] border-[#333333] text-[#666666]"
                            )}
                        >
                            <ArrowUpRight size={14} /> Withdraw
                        </button>
                    </div>

                    {/* Amount */}
                    <div>
                        <label className="block text-[10px] uppercase font-bold text-[#888888] mb-1">Amount (SEK)</label>
                        <input
                            type="number"
                            required
                            value={amount}
                            onChange={(e) => setAmount(e.target.value)}
                            className="w-full bg-[#111111] border border-[#333333] text-white p-2 rounded focus:border-sky-500 outline-none font-mono font-bold"
                            placeholder="0"
                        />
                    </div>

                    {/* Provider */}
                    <div>
                        <label className="block text-[10px] uppercase font-bold text-[#888888] mb-1">Bookmaker / Wallet</label>
                        <select
                            value={providerId}
                            onChange={(e) => setProviderId(e.target.value)}
                            className="w-full bg-[#111111] border border-[#333333] text-[#cccccc] p-2 rounded focus:border-sky-500 outline-none text-sm"
                        >
                            {accounts.map(a => (
                                <option key={a.provider} value={a.provider}>{a.provider}</option>
                            ))}
                        </select>
                    </div>

                    {/* Note */}
                    <div>
                        <label className="block text-[10px] uppercase font-bold text-[#888888] mb-1">Note (Optional)</label>
                        <input
                            type="text"
                            value={note}
                            onChange={(e) => setNote(e.target.value)}
                            className="w-full bg-[#111111] border border-[#333333] text-[#cccccc] p-2 rounded focus:border-sky-500 outline-none text-sm"
                            placeholder="e.g. Monthly Top-up"
                        />
                    </div>

                    <button
                        type="submit"
                        className="w-full bg-sky-600 hover:bg-sky-500 text-white font-bold py-2 rounded transition-colors text-sm mt-2"
                    >
                        Save Transaction
                    </button>
                </form>
            </div>
        </div>
    );
};

export default TransactionModal;
