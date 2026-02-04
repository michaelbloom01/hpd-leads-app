
import React from 'react';

interface HeaderProps {
  searchQuery?: string;
  onSearchChange?: (query: string) => void;
}

const Header: React.FC<HeaderProps> = ({ searchQuery = '', onSearchChange }) => {
  return (
    <header className="h-20 bg-slate-950 border-b border-white/5 flex items-center justify-between px-8 sticky top-0 z-20">
      <div className="flex items-center gap-4 lg:hidden">
        <button className="p-2 text-slate-400" aria-label="Open menu">
          <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M4 6h16M4 12h16M4 18h16"/></svg>
        </button>
        <span className="text-xl font-bold text-white tracking-tighter uppercase">HPD Leads</span>
      </div>

      <div className="hidden md:flex flex-1 max-w-2xl">
        <div className="relative w-full group">
          <input 
            type="text" 
            placeholder="Search leads by name or address..."
            value={searchQuery}
            onChange={(e) => onSearchChange?.(e.target.value)}
            aria-label="Search leads"
            className="w-full pl-12 pr-4 py-3 bg-slate-900 border border-white/5 rounded-2xl text-xs text-slate-200 focus:outline-none focus:ring-1 focus:ring-blue-500/50 transition-all placeholder:text-slate-600"
          />
          <svg className="w-4 h-4 text-slate-600 absolute left-4 top-3.5 group-focus-within:text-blue-500 transition-colors" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/></svg>
        </div>
      </div>

      <div className="flex items-center gap-8">
        <div className="hidden lg:flex items-center gap-6 pr-6 border-r border-white/5">
           <div className="text-right">
             <p className="text-[10px] font-bold text-slate-600 uppercase tracking-widest">Market Status</p>
             <p className="text-[11px] font-mono font-bold text-emerald-400">NY-HPD: CONNECTED</p>
           </div>
        </div>

        <div className="flex items-center gap-6">
          <button className="relative text-slate-500 hover:text-white transition-colors">
            <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9"/></svg>
            <span className="absolute -top-1 -right-1 w-2.5 h-2.5 bg-rose-500 rounded-full border-2 border-slate-950"></span>
          </button>
          
          <div className="flex items-center gap-4 pl-4 border-l border-white/5">
            <div className="text-right">
              <p className="text-xs font-bold text-white leading-none">Michael Bloom</p>
            </div>
            <div className="relative">
               <img 
                src="https://picsum.photos/seed/bloom/96/96" 
                className="w-10 h-10 rounded-xl border border-white/10 object-cover" 
                alt="Profile"
              />
              <div className="absolute -bottom-1 -right-1 w-3 h-3 bg-emerald-500 rounded-full border-2 border-slate-950"></div>
            </div>
          </div>
        </div>
      </div>
    </header>
  );
};

export default Header;
