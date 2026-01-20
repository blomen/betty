import React from 'react';

const FilterBar = ({ children }) => {
    return (
        <div className="h-8 bg-[#252526] border-b border-[#333333] flex items-center px-2 gap-2 shrink-0">
            {children}
        </div>
    );
};

export default FilterBar;
