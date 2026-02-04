
import React from 'react';

interface Props {
  activeTab: 'dashboard' | 'leads' | 'settings';
  onTabChange: (tab: 'dashboard' | 'leads' | 'settings') => void;
}

const Sidebar: React.FC<Props> = ({ activeTab, onTabChange }) => {
  const navItems = [
    { id: 'dashboard' as const, label: 'Dashboard', icon: (
      <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M13 10V3L4 14h7v7l9-11h-7z"/></svg>
    )},
    { id: 'leads' as const, label: 'Leads', icon: (
      <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4M4 7c0-2.21 3.582-4 8-4s8 1.79 8 4m0 5c0 2.21-3.582 4-8 4s-8-1.79-8-4"/></svg>
    )},
    { id: 'settings' as const, label: 'Settings', icon: (
      <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"/><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"/></svg>
    )},
  ];

  return (
    <div className="hidden lg:flex flex-col w-72 bg-slate-950 border-r border-white/5 p-8 space-y-12" role="complementary">
      <div className="flex items-center gap-4 px-2">
        <div className="w-10 h-10 bg-blue-600 rounded-xl flex items-center justify-center text-white shadow-lg shadow-blue-900/40">
           <svg className="w-6 h-6" fill="currentColor" viewBox="0 0 20 20"><path d="M10.707 2.293a1 1 0 00-1.414 0l-7 7a1 1 0 001.414 1.414L4 10.414V17a1 1 0 001 1h2a1 1 0 001-1v-2a1 1 0 011-1h2a1 1 0 011 1v2a1 1 0 001 1h2a1 1 0 001-1v-6.586l.293.293a1 1 0 001.414-1.414l-7-7z"></path></svg>
        </div>
        <div className="flex flex-col">
          <span className="text-lg font-black text-white tracking-tighter uppercase leading-none">HPD Leads</span>
          <span className="text-[9px] font-bold text-slate-500 tracking-[0.2em] mt-1">PM Acquisition</span>
        </div>
      </div>

      <nav className="flex-1 space-y-2">
        {navItems.map((item) => (
          <button
            key={item.id}
            onClick={() => onTabChange(item.id)}
            className={`w-full flex items-center gap-4 px-5 py-4 rounded-2xl text-[11px] font-bold uppercase tracking-widest transition-all ${
              activeTab === item.id 
              ? 'bg-blue-600/10 text-blue-400 border border-blue-500/20' 
              : 'text-slate-500 hover:text-slate-200 hover:bg-white/5'
            }`}
          >
            <div className={`${activeTab === item.id ? 'text-blue-400' : 'text-slate-600'}`}>{item.icon}</div>
            {item.label}
          </button>
        ))}
      </nav>

      <div className="space-y-6">
        <div className="flex items-center gap-3 px-2">
           <div className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></div>
           <span className="text-[9px] font-bold text-slate-600 uppercase tracking-widest">Connected to HPD</span>
        </div>
      </div>
    </div>
  );
};

export default Sidebar;
