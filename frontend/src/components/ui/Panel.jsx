import React from 'react';

const Panel = ({ title, count, headerRight, children, className = "flex-1" }) => {
    return (
        <div className={`flex flex-col min-w-0 bg-[#1e1e1e] ${className}`}>
            <div className="qt-header justify-between shrink-0">
                <span>{title}</span>
                <div className="flex items-center gap-2">
                    {count !== undefined && (
                        <span className="text-[10px] text-[#555555]">{count} items</span>
                    )}
                    {headerRight}
                </div>
            </div>

            <div className="flex-1 overflow-auto flex flex-col">
                {children}
            </div>
        </div>
    );
};

export default Panel;
