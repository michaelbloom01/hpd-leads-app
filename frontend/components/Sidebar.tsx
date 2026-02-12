
import React from 'react';

interface Props {
  activeTab: 'dashboard' | 'leads';
  onTabChange: (tab: 'dashboard' | 'leads') => void;
  isMobileOpen?: boolean;
  onToggleAgent?: () => void;
  onLogout?: () => void;
  userEmail?: string;
}

const Sidebar: React.FC<Props> = ({ activeTab, onTabChange, isMobileOpen = false, onToggleAgent, onLogout, userEmail }) => {
  const navItems = [
    { id: 'dashboard' as const, label: 'Dashboard', icon: (
      <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M13 10V3L4 14h7v7l9-11h-7z"/></svg>
    )},
    { id: 'leads' as const, label: 'Leads', icon: (
      <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4M4 7c0-2.21 3.582-4 8-4s8 1.79 8 4m0 5c0 2.21-3.582 4-8 4s-8-1.79-8-4"/></svg>
    )},
  ];

  return (
    <div 
      className={`
        ${isMobileOpen ? 'translate-x-0' : '-translate-x-full lg:translate-x-0'}
        fixed lg:relative inset-y-0 left-0 z-40
        flex flex-col w-64 bg-white border-r border-gray-200 p-6 space-y-10
        transition-transform duration-300 ease-in-out
      `} 
      role="complementary"
    >
      <div className="flex items-center gap-3 px-2">
        <div className="w-9 h-9 bg-emerald-600 rounded-lg flex items-center justify-center text-white shadow-sm">
           <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 20 20"><path d="M10.707 2.293a1 1 0 00-1.414 0l-7 7a1 1 0 001.414 1.414L4 10.414V17a1 1 0 001 1h2a1 1 0 001-1v-2a1 1 0 011-1h2a1 1 0 011 1v2a1 1 0 001 1h2a1 1 0 001-1v-6.586l.293.293a1 1 0 001.414-1.414l-7-7z"></path></svg>
        </div>
        <div className="flex flex-col">
          <span className="text-base font-bold text-gray-900 tracking-tight leading-none">HPD Leads</span>
          <span className="text-[10px] font-medium text-gray-400 tracking-wide mt-0.5">Deal Sourcing</span>
        </div>
      </div>

      <nav className="flex-1 space-y-1">
        {navItems.map((item) => (
          <button
            key={item.id}
            onClick={() => onTabChange(item.id)}
            className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-all ${
              activeTab === item.id 
              ? 'bg-gray-100 text-gray-900 font-semibold' 
              : 'text-gray-500 hover:text-gray-700 hover:bg-gray-50'
            }`}
          >
            <div className={`${activeTab === item.id ? 'text-emerald-600' : 'text-gray-400'}`}>{item.icon}</div>
            {item.label}
          </button>
        ))}
        
        {/* Agent toggle */}
        {onToggleAgent && (
          <button
            onClick={onToggleAgent}
            className="w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium text-gray-500 hover:text-gray-700 hover:bg-gray-50 transition-all"
          >
            <div className="text-gray-400">
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z"/>
              </svg>
            </div>
            Agent
            <span className="ml-auto text-[10px] text-gray-400 bg-gray-100 px-1.5 py-0.5 rounded">Ctrl+K</span>
          </button>
        )}
      </nav>

      <div className="space-y-3">
        <div className="flex items-center gap-2 px-2">
          <div className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></div>
          <span className="text-xs text-gray-400">Connected to HPD</span>
        </div>
        {userEmail && (
          <div className="px-2">
            <p className="text-xs text-gray-400 truncate" title={userEmail}>{userEmail}</p>
          </div>
        )}
        {onLogout && (
          <button
            onClick={onLogout}
            className="w-full flex items-center gap-2 px-3 py-2 rounded-lg text-xs text-gray-500 hover:text-red-600 hover:bg-red-50 transition-all"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1" />
            </svg>
            Sign out
          </button>
        )}
      </div>
    </div>
  );
};

export default Sidebar;
