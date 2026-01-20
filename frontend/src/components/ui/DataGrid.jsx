import React from 'react';

const DataGrid = ({ columns, data, loading, emptyMessage = "No data available." }) => {
    return (
        <table className="w-full border-collapse">
            <thead className="sticky top-0 bg-[#252526] z-10 shadow-sm">
                <tr>
                    {columns.map((col, i) => (
                        <th
                            key={i}
                            className={`qt-table-header ${col.className || ''}`}
                            style={col.style}
                        >
                            {col.header}
                        </th>
                    ))}
                </tr>
            </thead>
            <tbody>
                {loading ? (
                    <tr>
                        <td colSpan={columns.length} className="p-4 text-center text-[#555555] text-xs italic">
                            Loading data...
                        </td>
                    </tr>
                ) : data.length === 0 ? (
                    <tr>
                        <td colSpan={columns.length} className="p-4 text-center text-[#555555] text-xs">
                            {emptyMessage}
                        </td>
                    </tr>
                ) : (
                    data.map((row, rowIndex) => (
                        <tr key={row.id || rowIndex} className="qt-table-row group">
                            {columns.map((col, colIndex) => (
                                <td
                                    key={colIndex}
                                    className={`qt-table-cell ${col.cellClassName || ''}`}
                                >
                                    {col.render ? col.render(row) : row[col.accessor]}
                                </td>
                            ))}
                        </tr>
                    ))
                )}
            </tbody>
        </table>
    );
};

export default DataGrid;
