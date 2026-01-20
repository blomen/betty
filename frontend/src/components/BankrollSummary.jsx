import React, { useState, useEffect } from 'react';
import { bankroll } from '../utils/api';

const BankrollSummary = () => {
    const [summary, setSummary] = useState({ total: 0, providers: [] });
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        bankroll.getSummary()
            .then(data => {
                setSummary(data);
                setLoading(false);
            })
            .catch(err => {
                console.error("Bankroll fetch failed", err);
                setLoading(false);
            });
    }, []);

    // Format currency helper
    const fmt = (val) => new Intl.NumberFormat('sv-SE', {
        style: 'currency',
        currency: 'SEK',
        maximumFractionDigits: 0
    }).format(val);

    if (loading) {
        return (
            <div className="bg-[#1e1e1e] border-b border-[#333333] h-8 flex items-center px-4 text-xs text-[#666666]">
                Loading bankroll...
            </div>
        );
    }

    return (
        <div className="bg-[#1e1e1e] border-b border-[#333333] h-8 flex items-center px-4 gap-6 text-xs select-none">
            <div className="flex items-center gap-2">
                <span className="text-[#666666] font-bold uppercase">Total:</span>
                <span className="text-white font-mono">{fmt(summary.total)}</span>
            </div>
            <div className="w-px h-4 bg-[#333333]" />
            {summary.providers.slice(0, 5).map((p, i) => (
                <React.Fragment key={p.id}>
                    <div className="flex items-center gap-2">
                        <span className="text-[#666666] font-bold uppercase">{p.name}:</span>
                        <span className="text-[#cccccc] font-mono">{fmt(p.balance)}</span>
                    </div>
                    {i < Math.min(summary.providers.length, 5) - 1 && (
                        <div className="w-px h-4 bg-[#333333]" />
                    )}
                </React.Fragment>
            ))}
        </div>
    );
};

export default BankrollSummary;
