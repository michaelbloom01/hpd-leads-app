
import React from 'react';
import { useAuth } from '../contexts/AuthContext';

interface HeaderProps {
  searchQuery?: string;
  onSearchChange?: (query: string) => void;
}

const Header: React.FC<HeaderProps> = ({ searchQuery = '', onSearchChange }) => {
  const { user } = useAuth();
  const displayName = user?.email?.split('@')[0] || 'User';
  const initials = displayName.slice(0, 2).toUpperCase();

  return (
    <header className="h-16 bg-white border-b border-gray-200 flex items-center justify-between px-6 sticky top-0 z-20">
      <div className="flex items-center gap-4 lg:hidden">
        <span className="text-lg font-bold text-gray-900 tracking-tight">HPD Leads</span>
      </div>

      <div className="hidden md:flex flex-1 max-w-2xl" />

      <div className="flex items-center gap-6">
        <div className="hidden lg:flex items-center gap-4 pr-4 border-r border-gray-200">
           <div className="text-right">
             <p className="text-[10px] font-medium text-gray-400 uppercase tracking-wider">Status</p>
             <p className="text-xs font-medium text-emerald-600">Connected</p>
           </div>
        </div>

        <div className="flex items-center gap-4">
          <div className="flex items-center gap-3">
            <div className="text-right">
              <p className="text-sm font-medium text-gray-900 leading-none">{user?.email || 'User'}</p>
            </div>
            <div className="relative">
               <div className="w-9 h-9 rounded-lg border border-gray-200 bg-gray-900 flex items-center justify-center">
                 <span className="text-white text-sm font-bold">{initials}</span>
               </div>
              <div className="absolute -bottom-0.5 -right-0.5 w-2.5 h-2.5 bg-emerald-500 rounded-full border-2 border-white"></div>
            </div>
          </div>
        </div>
      </div>
    </header>
  );
};

export default Header;
