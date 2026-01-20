import React, { useState, useEffect } from 'react';
import Layout from '../components/ui/Layout';
import BankrollGrid from '../components/bankroll/BankrollGrid';
import RiskConfigPanel from '../components/bankroll/RiskConfigPanel';
import TransactionHistory from '../components/bankroll/TransactionHistory';
import TransactionModal from '../components/bankroll/TransactionModal';
import BankrollSummary from '../components/BankrollSummary';
import { providers, bets } from '../utils/api';

const BankrollPage = () => {
    const [accounts, setAccounts] = useState([]);
    const [transactions, setTransactions] = useState([]);
    const [loading, setLoading] = useState(true);
    const [isTransactionModalOpen, setIsTransactionModalOpen] = useState(false);

    useEffect(() => {
        Promise.all([
            providers.list(),
            bets.list(null, 20),
        ])
            .then(([providersData, betsData]) => {
                // Convert providers to accounts format
                const accountsList = (providersData.providers || []).map(p => ({
                    provider: p.name,
                    providerId: p.id,
                    balance: p.balance,
                    status: p.is_enabled ? 'Logged In' : 'Logged Out',
                }));
                setAccounts(accountsList);

                // Convert bets to transactions
                const txList = (betsData.bets || []).map(b => ({
                    date: b.placed_at,
                    type: b.result === 'won' ? 'win' : b.result === 'lost' ? 'loss' : 'bet',
                    provider: b.provider,
                    amount: b.result === 'won' ? b.payout : b.stake,
                    note: `${b.outcome} @ ${b.odds?.toFixed(2)}`,
                }));
                setTransactions(txList);
                setLoading(false);
            })
            .catch(err => {
                console.error("Bankroll page fetch failed", err);
                setLoading(false);
            });
    }, []);

    // Calculate Playable (Logged In) Equity
    const playableEquity = accounts
        .filter(a => a.status === 'Logged In')
        .reduce((sum, a) => sum + a.balance, 0);

    const handleAddTransaction = (tx) => {
        setTransactions([tx, ...transactions]);
        // TODO: Call API to update provider balance
    };

    if (loading) {
        return (
            <Layout>
                <div className="flex items-center justify-center h-full text-[#555555]">
                    Loading bankroll data...
                </div>
            </Layout>
        );
    }

    return (
        <Layout>
            <BankrollSummary />

            <div className="flex-1 flex overflow-hidden">
                {/* Left Panel: Fund Distribution (60%) */}
                <div className="flex-[0.60] flex flex-col border-r border-[#333333]">
                    <BankrollGrid accounts={accounts} />
                </div>

                {/* Right Panel: Risk & History (40%) */}
                <div className="flex-[0.40] flex flex-col bg-[#1e1e1e]">
                    <RiskConfigPanel playableEquity={playableEquity} />
                    <TransactionHistory
                        transactions={transactions}
                        onAddClick={() => setIsTransactionModalOpen(true)}
                    />
                </div>
            </div>

            {/* Modal */}
            {isTransactionModalOpen && (
                <TransactionModal
                    accounts={accounts}
                    onClose={() => setIsTransactionModalOpen(false)}
                    onSave={handleAddTransaction}
                />
            )}

            <div className="bg-[#007acc] h-5 flex items-center px-2 text-[10px] text-white font-bold select-none shrink-0">
                MONEY MANAGER ACTIVE • PLAYABLE LIQUIDITY: {playableEquity.toLocaleString()} SEK
            </div>
        </Layout>
    );
};

export default BankrollPage;
