
import React from 'react';

interface HeaderProps {
  searchQuery?: string;
  onSearchChange?: (query: string) => void;
}

const Header: React.FC<HeaderProps> = ({ searchQuery = '', onSearchChange }) => {
  return (
    <header className="h-20 bg-slate-950 border-b border-white/5 flex items-center justify-between px-8 sticky top-0 z-20">
      <div className="flex items-center gap-4 lg:hidden">
        <span className="text-xl font-bold text-white tracking-tighter uppercase">HPD Leads</span>
      </div>

      {/* Search removed - use filters in LeadTable instead */}
      <div className="hidden md:flex flex-1 max-w-2xl" />

      <div className="flex items-center gap-8">
        <div className="hidden lg:flex items-center gap-6 pr-6 border-r border-white/5">
           <div className="text-right">
             <p className="text-[10px] font-bold text-slate-600 uppercase tracking-widest">Market Status</p>
             <p className="text-[11px] font-mono font-bold text-emerald-400">NY-HPD: CONNECTED</p>
           </div>
        </div>

        <div className="flex items-center gap-6">
          <div className="flex items-center gap-4 pl-4">
            <div className="text-right">
              <p className="text-xs font-bold text-white leading-none">Michael Bloom</p>
            </div>
            <div className="relative">
               <img 
                src="https://picsum.photos/seed/bloom/96/96" 
                className="w-10 h-10 rounded-xl border border-white/10 object-cover" 
                alt="Profile"
                onError={(e) => {
                  // Fallback to initials if external image fails
                  const target = e.target as HTMLImageElement;
                  target.style.display = 'none';
                  target.parentElement?.classList.add('bg-blue-600', 'flex', 'items-center', 'justify-center');
                  const span = document.createElement('span');
                  span.className = 'text-white text-sm font-bold';
                  span.textContent = 'MB';
                  target.parentElement?.appendChild(span);
                }}
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
