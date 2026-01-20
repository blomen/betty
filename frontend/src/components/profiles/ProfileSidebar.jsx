import React from 'react';
import { User, Plus, Check } from 'lucide-react';
import Panel from '../ui/Panel';
import clsx from 'clsx';

const ProfileSidebar = ({ profiles, activeProfileId, onSelect, onCreate }) => {
    return (
        <Panel
            title="User Identities"
            count={profiles.length}
            className="w-80 border-r border-[#333333]"
            headerRight={
                <button
                    onClick={onCreate}
                    className="bg-[#333333] hover:bg-[#444444] text-white p-1 rounded transition-colors"
                    title="Create New Profile"
                >
                    <Plus size={14} />
                </button>
            }
        >
            <div className="flex-1 overflow-y-auto p-2 space-y-2">
                {profiles.map(p => (
                    <div
                        key={p.id}
                        onClick={() => onSelect(p.id)}
                        className={clsx(
                            "p-3 cursor-pointer rounded border transition-all group flex items-center justify-between",
                            activeProfileId === p.id
                                ? "bg-[#252526] border-sky-500 shadow-sm"
                                : "bg-[#1e1e1e] border-[#333333] hover:border-[#555555]"
                        )}
                    >
                        <div className="flex items-center gap-3">
                            <div className={clsx(
                                "w-8 h-8 rounded flex items-center justify-center border",
                                activeProfileId === p.id ? "bg-sky-900/30 border-sky-700 text-sky-400" : "bg-[#111111] border-[#333333] text-[#666666]"
                            )}>
                                <User size={16} />
                            </div>
                            <div>
                                <h3 className={clsx("font-bold text-sm", activeProfileId === p.id ? "text-white" : "text-[#dddddd]")}>{p.name}</h3>
                                <div className="flex items-center gap-2">
                                    <span className="text-[10px] text-[#555555] font-mono">{p.id}</span>
                                    {activeProfileId === p.id && (
                                        <span className="text-[9px] bg-sky-900/50 text-sky-400 px-1 rounded uppercase font-bold">Active</span>
                                    )}
                                </div>
                            </div>
                        </div>

                        {activeProfileId === p.id && <Check size={16} className="text-sky-500" />}
                    </div>
                ))}
            </div>
        </Panel>
    );
};

export default ProfileSidebar;
