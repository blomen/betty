import React from 'react';

const Layout = ({ children }) => {
    return (
        <div className="flex flex-col h-full bg-[#1e1e1e] text-[#cccccc] font-sans">
            {children}
        </div>
    );
};

export default Layout;
