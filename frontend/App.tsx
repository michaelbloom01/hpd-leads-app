
import React, { useState, useCallback } from 'react';
import { Toaster, toast } from 'react-hot-toast';
import Sidebar from './components/Sidebar';
import Header from './components/Header';
import Dashboard from './components/Dashboard';
import LeadTable from './components/LeadTable';
import LeadDetail from './components/LeadDetail';
import { ApiLead, refreshPipeline } from './services/api';

const App: React.FC = () => {
  const [activeTab, setActiveTab] = useState<'dashboard' | 'leads'>('dashboard');
  const [selectedLead, setSelectedLead] = useState<ApiLead | null>(null);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);

  const handleRefresh = useCallback(async () => {
    const confirmed = window.confirm(
      'This will refresh ALL ~200k buildings from HPD. This runs in the background and may take several minutes. Continue?'
    );
    if (!confirmed) return;
    
    setIsRefreshing(true);
    try {
      await refreshPipeline(true); // Always do full refresh
      setRefreshKey(k => k + 1);
      toast.success('Refresh started in background. Check the dashboard for progress.');
    } catch (err) {
      console.error('Failed to refresh:', err);
      toast.error('Failed to start refresh. Make sure the backend is running.');
    } finally {
      setIsRefreshing(false);
    }
  }, []);

  return (
    <div className="flex h-screen bg-slate-950 text-slate-200 overflow-hidden font-sans">
      {/* Toast Notifications */}
      <Toaster 
        position="top-right"
        toastOptions={{
          duration: 4000,
          style: {
            background: '#1e293b',
            color: '#f1f5f9',
            border: '1px solid rgba(255,255,255,0.1)',
          },
          success: {
            iconTheme: {
              primary: '#10b981',
              secondary: '#f1f5f9',
            },
          },
          error: {
            iconTheme: {
              primary: '#ef4444',
              secondary: '#f1f5f9',
            },
          },
        }}
      />
      {/* Mobile Menu Button */}
      <button
        onClick={() => setIsMobileMenuOpen(!isMobileMenuOpen)}
        className="lg:hidden fixed top-4 left-4 z-50 p-2 bg-slate-800 rounded-lg border border-white/10"
      >
        <svg className="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          {isMobileMenuOpen ? (
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M6 18L18 6M6 6l12 12"/>
          ) : (
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M4 6h16M4 12h16M4 18h16"/>
          )}
        </svg>
      </button>

      {/* Mobile Menu Overlay */}
      {isMobileMenuOpen && (
        <div 
          className="lg:hidden fixed inset-0 bg-black/50 z-40"
          onClick={() => setIsMobileMenuOpen(false)}
        />
      )}

      {/* Sidebar Navigation */}
      <Sidebar 
        activeTab={activeTab} 
        onTabChange={(tab) => {
          setActiveTab(tab);
          setIsMobileMenuOpen(false);
        }}
        isMobileOpen={isMobileMenuOpen}
      />

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
              <button 
                onClick={handleRefresh}
                disabled={isRefreshing}
                className="flex items-center gap-3 px-6 py-3 bg-slate-800 hover:bg-slate-700 text-white rounded-xl font-bold text-[11px] uppercase tracking-widest transition-all shadow-xl disabled:opacity-50 disabled:cursor-not-allowed border border-white/5"
              >
                <svg className={`w-4 h-4 ${isRefreshing ? 'animate-spin' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>
                {isRefreshing ? 'Starting...' : 'Refresh Data'}
              </button>
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
