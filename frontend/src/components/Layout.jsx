import React from 'react';
import { NavLink } from 'react-router-dom';
import { LayoutDashboard, Wallet, Menu, TrendingUp, Zap, Users, ArrowLeftRight, Home } from 'lucide-react';
import clsx from 'clsx';

const SidebarItem = ({ to, icon: Icon, label }) => (
    <NavLink
        to={to}
        title={label}
        className={({ isActive }) =>
            clsx(
                "flex flex-col items-center justify-center w-full h-12 mb-2 transition-all opacity-80 hover:opacity-100 hover:bg-[#333333]",
                isActive && "bg-[#333333] border-l-2 border-sky-500 opacity-100"
            )
        }
    >
        {({ isActive }) => (
            <Icon size={18} className={clsx(isActive ? "text-sky-500" : "text-[#999999]")} />
        )}
    </NavLink>
);

const Layout = ({ children }) => {
    return (
        <div className="flex h-screen bg-[#111111] text-[#cccccc] font-sans overflow-hidden">
            {/* Compact Sidebar */}
            <aside className="w-12 bg-[#1e1e1e] border-r border-[#333333] flex flex-col items-center py-2 z-20">
                <div className="mb-4">
                    <Menu size={20} className="text-[#666666]" />
                </div>

                <nav className="flex-1 w-full">
                    <SidebarItem to="/" icon={Home} label="Home Dashboard" />
                    <SidebarItem to="/arbitrage" icon={ArrowLeftRight} label="Arbitrage Scanner" />
                    <SidebarItem to="/valuebets" icon={TrendingUp} label="Value Bets" />
                    <SidebarItem to="/bonus" icon={Zap} label="Bonus Extraction" />
                    <SidebarItem to="/profiles" icon={Users} label="User Profiles" />
                    <SidebarItem to="/bankroll" icon={Wallet} label="Bankroll Control" />
                </nav>

                <div className="mt-auto pb-4">
                    {/* Status Indicator */}
                    <div className="w-2 h-2 rounded-full bg-green-500 mx-auto" title="Connection: Stable" />
                </div>
            </aside>

            {/* Main Workspace */}
            <main className="flex-1 flex flex-col min-w-0 overflow-hidden bg-[#111111]">
                {/* Top Bar / Header Area could go here if needed */}
                {children}
            </main>
        </div>
    );
};

export default Layout;
