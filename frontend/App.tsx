
import React, { useState, useCallback } from 'react';
import Sidebar from './components/Sidebar';
import Header from './components/Header';
import Dashboard from './components/Dashboard';
import LeadTable from './components/LeadTable';
import PropertyAnalysis from './components/PropertyAnalysis';
import { BuildingLead } from './types';
import { refreshPipeline } from './services/api';

const App: React.FC = () => {
  const [activeTab, setActiveTab] = useState<'dashboard' | 'leads' | 'settings'>('dashboard');
  const [selectedLead, setSelectedLead] = useState<BuildingLead | null>(null);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);

  const handleRefresh = useCallback(async () => {
    setIsRefreshing(true);
    try {
      await refreshPipeline(5000);
      setRefreshKey(k => k + 1); // Force re-render of child components
    } catch (err) {
      console.error('Failed to refresh:', err);
      alert('Failed to refresh pipeline. Make sure the backend is running.');
    } finally {
      setIsRefreshing(false);
    }
  }, []);

  return (
    <div className="flex h-screen bg-slate-950 text-slate-200 overflow-hidden font-sans">
      {/* Sidebar Navigation */}
      <Sidebar activeTab={activeTab} onTabChange={setActiveTab} />

      {/* Primary Application Interface */}
      <div className="flex-1 flex flex-col min-w-0">
        <Header />
        
        <main className="flex-1 overflow-y-auto p-6 lg:p-12">
          <div className="max-w-[1600px] mx-auto space-y-12">
            <header className="flex flex-col md:flex-row justify-between items-start md:items-end gap-6">
              <div className="space-y-2">
                <div className="flex items-center gap-3">
                   <span className="px-2 py-0.5 bg-blue-600/20 text-blue-400 text-[9px] font-bold uppercase tracking-[0.2em] rounded">System v2.4</span>
                   <span className="text-slate-700">•</span>
                   <span className="text-[9px] text-slate-500 font-bold uppercase tracking-widest">Real-time Stream: Active</span>
                </div>
                <h1 className="text-4xl font-black text-white tracking-tighter uppercase">
                  {activeTab === 'dashboard' ? 'Market Overview' : 'Asset Inventory'}
                </h1>
                <p className="text-slate-500 text-sm max-w-xl leading-relaxed">
                  {activeTab === 'dashboard' 
                    ? 'Global risk assessment of the NYC residential landscape. Data aggregated from HPD, ACRIS, and building department feeds.' 
                    : 'Target acquisition pipeline. Advanced cleaned records with owner intelligence and high-resolution contact data.'}
                </p>
              </div>
              <div className="flex gap-4">
                <button className="flex items-center gap-3 px-6 py-3 bg-slate-900 border border-white/5 text-slate-300 rounded-xl font-bold text-[11px] uppercase tracking-widest hover:bg-slate-800 transition-all">
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"/></svg>
                  Export Data
                </button>
                <button 
                  onClick={handleRefresh}
                  disabled={isRefreshing}
                  className="flex items-center gap-3 px-8 py-3 bg-blue-600 text-white rounded-xl font-bold text-[11px] uppercase tracking-widest hover:bg-blue-500 transition-all shadow-xl shadow-blue-900/20 disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  <svg className={`w-4 h-4 ${isRefreshing ? 'animate-spin' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>
                  {isRefreshing ? 'Refreshing...' : 'Refresh from HPD'}
                </button>
              </div>
            </header>

            <div className="transition-all duration-500 ease-out" key={refreshKey}>
              {activeTab === 'dashboard' ? (
                <Dashboard />
              ) : (
                <LeadTable onSelectLead={setSelectedLead} />
              )}
            </div>
          </div>
        </main>
      </div>

      {/* Property Analysis Modal Overlay */}
      {selectedLead && (
        <PropertyAnalysis 
          lead={selectedLead} 
          onClose={() => setSelectedLead(null)} 
        />
      )}
    </div>
  );
};

export default App;
