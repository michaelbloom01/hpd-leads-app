
import React, { useState, useCallback } from 'react';
import Sidebar from './components/Sidebar';
import Header from './components/Header';
import Dashboard from './components/Dashboard';
import LeadTable from './components/LeadTable';
import LeadDetail from './components/LeadDetail';
import { ApiLead, refreshPipeline } from './services/api';

const App: React.FC = () => {
  const [activeTab, setActiveTab] = useState<'dashboard' | 'leads' | 'settings'>('dashboard');
  const [selectedLead, setSelectedLead] = useState<ApiLead | null>(null);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const [refreshMode, setRefreshMode] = useState<'quick' | 'full'>('quick');

  const handleRefresh = useCallback(async () => {
    const confirmFull = refreshMode === 'full' && 
      !window.confirm('Full refresh will fetch ALL ~200k buildings from HPD. This may take several minutes. Continue?');
    if (confirmFull) return;
    
    setIsRefreshing(true);
    try {
      await refreshPipeline(refreshMode === 'full');
      setRefreshKey(k => k + 1);
    } catch (err) {
      console.error('Failed to refresh:', err);
      alert('Failed to refresh pipeline. Make sure the backend is running.');
    } finally {
      setIsRefreshing(false);
    }
  }, [refreshMode]);

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
                <h1 className="text-4xl font-black text-white tracking-tighter uppercase">
                  {activeTab === 'dashboard' ? 'Lead Dashboard' : 'All Leads'}
                </h1>
                <p className="text-slate-500 text-sm max-w-xl leading-relaxed">
                  {activeTab === 'dashboard' 
                    ? 'Property management companies in NYC ranked by portfolio size and acquisition potential.' 
                    : 'Browse and filter leads by score, borough, portfolio size, and outreach status.'}
                </p>
              </div>
              <div className="flex gap-3 items-center">
                {/* Refresh Mode Toggle */}
                <div className="flex items-center gap-2 bg-slate-900 border border-white/5 rounded-xl px-3 py-2">
                  <button
                    onClick={() => setRefreshMode('quick')}
                    className={`px-3 py-1 rounded-lg text-[10px] font-bold uppercase tracking-wider transition-colors ${
                      refreshMode === 'quick' 
                        ? 'bg-blue-600 text-white' 
                        : 'text-slate-500 hover:text-slate-300'
                    }`}
                  >
                    Quick (2k)
                  </button>
                  <button
                    onClick={() => setRefreshMode('full')}
                    className={`px-3 py-1 rounded-lg text-[10px] font-bold uppercase tracking-wider transition-colors ${
                      refreshMode === 'full' 
                        ? 'bg-amber-600 text-white' 
                        : 'text-slate-500 hover:text-slate-300'
                    }`}
                    title="Full refresh may timeout on free hosting"
                  >
                    Full (200k+)
                  </button>
                </div>
                
                <button 
                  onClick={handleRefresh}
                  disabled={isRefreshing}
                  className={`flex items-center gap-3 px-6 py-3 text-white rounded-xl font-bold text-[11px] uppercase tracking-widest transition-all shadow-xl disabled:opacity-50 disabled:cursor-not-allowed ${
                    refreshMode === 'full' 
                      ? 'bg-amber-600 hover:bg-amber-500 shadow-amber-900/20' 
                      : 'bg-blue-600 hover:bg-blue-500 shadow-blue-900/20'
                  }`}
                >
                  <svg className={`w-4 h-4 ${isRefreshing ? 'animate-spin' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>
                  {isRefreshing ? 'Refreshing...' : 'Refresh from HPD'}
                </button>
              </div>
            </header>

            <div className="transition-all duration-500 ease-out" key={refreshKey}>
              {activeTab === 'dashboard' ? (
                <Dashboard onSelectLead={setSelectedLead} />
              ) : (
                <LeadTable onSelectLead={setSelectedLead} />
              )}
            </div>
          </div>
        </main>
      </div>

      {/* Lead Detail Modal */}
      {selectedLead && (
        <LeadDetail 
          lead={selectedLead} 
          onClose={() => setSelectedLead(null)} 
        />
      )}
    </div>
  );
};

export default App;
