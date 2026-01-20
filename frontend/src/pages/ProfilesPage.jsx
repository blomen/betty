import React, { useState, useEffect } from 'react';
import Layout from '../components/ui/Layout';
import ProfileSidebar from '../components/profiles/ProfileSidebar';
import { Trash2, Save, UserCircle, Settings } from 'lucide-react';
import BankrollSummary from '../components/BankrollSummary';
import { profile as profileApi } from '../utils/api';

const ProfilesPage = () => {
    const [profileSettings, setProfileSettings] = useState({
        name: 'default',
        kelly_fraction: 0.25,
        min_edge_pct: 2.0,
        min_arb_pct: 0.5,
        max_stake_pct: 5.0,
    });
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [message, setMessage] = useState('');

    useEffect(() => {
        profileApi.get()
            .then(data => {
                setProfileSettings(data);
                setLoading(false);
            })
            .catch(err => {
                console.error("Failed to fetch profile", err);
                setLoading(false);
            });
    }, []);

    const handleSave = async (e) => {
        e.preventDefault();
        setSaving(true);
        try {
            await profileApi.update({
                kelly_fraction: profileSettings.kelly_fraction,
                min_edge_pct: profileSettings.min_edge_pct,
                min_arb_pct: profileSettings.min_arb_pct,
                max_stake_pct: profileSettings.max_stake_pct,
            });
            setMessage('Settings saved!');
            setTimeout(() => setMessage(''), 2000);
        } catch (err) {
            console.error("Failed to save profile", err);
            setMessage('Failed to save');
        }
        setSaving(false);
    };

    const handleChange = (field, value) => {
        setProfileSettings(prev => ({ ...prev, [field]: parseFloat(value) || 0 }));
    };

    if (loading) {
        return (
            <Layout>
                <div className="flex items-center justify-center h-full text-[#555555]">
                    Loading profile settings...
                </div>
            </Layout>
        );
    }

    return (
        <Layout>
            <BankrollSummary />

            <div className="flex-1 flex overflow-hidden">
                {/* Sidebar - can be simplified since we only have one profile now */}
                <div className="w-64 bg-[#1e1e1e] border-r border-[#333333] flex flex-col">
                    <div className="qt-header border-b border-[#333333]">
                        <Settings size={14} className="mr-2 text-sky-500" />
                        <span>Settings</span>
                    </div>
                    <div className="p-4">
                        <div className="text-xs text-[#666666] uppercase font-bold mb-2">Profile</div>
                        <div className="bg-[#252526] border border-sky-500 p-3 rounded text-white font-bold">
                            {profileSettings.name}
                        </div>
                    </div>
                </div>

                {/* Main Content */}
                <div className="flex-1 bg-[#1e1e1e] flex flex-col items-center justify-center p-8">
                    <div className="max-w-md w-full bg-[#252526] border border-[#333333] rounded-lg p-6 shadow-xl">
                        <div className="flex items-center gap-4 mb-6 border-b border-[#333333] pb-6">
                            <div className="w-16 h-16 rounded-full bg-sky-900/20 border-2 border-sky-500/50 flex items-center justify-center text-sky-500">
                                <UserCircle size={32} />
                            </div>
                            <div>
                                <h2 className="text-xl font-bold text-white">Profile Settings</h2>
                                <p className="text-[#666666] text-xs">Configure stake and threshold settings</p>
                            </div>
                        </div>

                        <form onSubmit={handleSave} className="space-y-4">
                            <div>
                                <label className="block text-[10px] uppercase font-bold text-[#888888] mb-1">
                                    Kelly Fraction
                                </label>
                                <input
                                    type="number"
                                    step="0.05"
                                    min="0.1"
                                    max="1"
                                    value={profileSettings.kelly_fraction}
                                    onChange={(e) => handleChange('kelly_fraction', e.target.value)}
                                    className="w-full bg-[#111111] border border-[#333333] text-white p-2 rounded focus:border-sky-500 outline-none font-mono"
                                />
                                <p className="text-[10px] text-[#555555] mt-1">0.25 = Quarter Kelly (recommended)</p>
                            </div>

                            <div>
                                <label className="block text-[10px] uppercase font-bold text-[#888888] mb-1">
                                    Min Edge % (Value Bets)
                                </label>
                                <input
                                    type="number"
                                    step="0.5"
                                    min="0"
                                    value={profileSettings.min_edge_pct}
                                    onChange={(e) => handleChange('min_edge_pct', e.target.value)}
                                    className="w-full bg-[#111111] border border-[#333333] text-white p-2 rounded focus:border-sky-500 outline-none font-mono"
                                />
                            </div>

                            <div>
                                <label className="block text-[10px] uppercase font-bold text-[#888888] mb-1">
                                    Min Arb % (Arbitrage)
                                </label>
                                <input
                                    type="number"
                                    step="0.1"
                                    min="0"
                                    value={profileSettings.min_arb_pct}
                                    onChange={(e) => handleChange('min_arb_pct', e.target.value)}
                                    className="w-full bg-[#111111] border border-[#333333] text-white p-2 rounded focus:border-sky-500 outline-none font-mono"
                                />
                            </div>

                            <div>
                                <label className="block text-[10px] uppercase font-bold text-[#888888] mb-1">
                                    Max Stake % of Bankroll
                                </label>
                                <input
                                    type="number"
                                    step="0.5"
                                    min="1"
                                    max="100"
                                    value={profileSettings.max_stake_pct}
                                    onChange={(e) => handleChange('max_stake_pct', e.target.value)}
                                    className="w-full bg-[#111111] border border-[#333333] text-white p-2 rounded focus:border-sky-500 outline-none font-mono"
                                />
                            </div>

                            <div className="pt-4 border-t border-[#333333] flex justify-between items-center">
                                {message && (
                                    <span className={`text-xs ${message.includes('Failed') ? 'text-red-400' : 'text-green-400'}`}>
                                        {message}
                                    </span>
                                )}
                                <button
                                    type="submit"
                                    disabled={saving}
                                    className="bg-sky-600 hover:bg-sky-500 text-white px-4 py-2 rounded flex items-center gap-2 transition-colors disabled:opacity-50"
                                >
                                    <Save size={16} />
                                    {saving ? 'Saving...' : 'Save Settings'}
                                </button>
                            </div>
                        </form>
                    </div>
                </div>
            </div>

            <div className="bg-[#007acc] h-5 flex items-center px-2 text-[10px] text-white font-bold select-none shrink-0">
                PROFILE: {profileSettings.name.toUpperCase()} • KELLY: {profileSettings.kelly_fraction}
            </div>
        </Layout>
    );
};

export default ProfilesPage;
