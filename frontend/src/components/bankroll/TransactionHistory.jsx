import React from 'react';
import DataGrid from '../ui/DataGrid';
import Panel from '../ui/Panel';
import { Plus, ArrowDownLeft, ArrowUpRight } from 'lucide-react';
import { formatDateTime } from '../../utils/formatters';

const TransactionHistory = ({ transactions, onAddClick }) => {

    const columns = [
        {
            header: "Date",
            accessor: "date",
            className: "w-24",
            cellClassName: "text-[10px] text-[#666666] font-mono",
            render: row => formatDateTime(row.date)
        },
        {
            header: "Type",
            accessor: "type",
            className: "w-20",
            render: row => (
                <div className="flex items-center gap-1">
                    {row.type === 'deposit' ? <ArrowDownLeft size={12} className="text-green-500" /> : <ArrowUpRight size={12} className="text-red-500" />}
                    <span className={row.type === 'deposit' ? "text-green-400" : "text-red-400"}>
                        {row.type === 'deposit' ? 'DEPOSIT' : 'WITHDRAW'}
                    </span>
                </div>
            )
        },
        {
            header: "Provider",
            accessor: "provider",
            cellClassName: "font-bold text-[#cccccc]"
        },
        {
            header: "Amount",
            accessor: "amount",
            className: "w-24 text-right",
            cellClassName: "text-right font-mono font-bold text-white",
            render: row => `${row.amount.toLocaleString()} SEK`
        }
    ];

    return (
        <Panel
            title="Recent Transactions"
            className="flex-1 flex flex-col min-h-0 border-t border-[#333333]"
            headerRight={
                <button
                    onClick={onAddClick}
                    className="text-[10px] bg-[#333333] hover:bg-[#444444] text-white px-2 py-0.5 rounded flex items-center gap-1 transition-colors"
                >
                    <Plus size={10} /> ADD
                </button>
            }
        >
            <DataGrid
                columns={columns}
                data={transactions}
                loading={false}
                emptyMessage="No transactions logged yet."
            />
        </Panel>
    );
};

export default TransactionHistory;
