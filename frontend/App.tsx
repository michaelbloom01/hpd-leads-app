
import React, { useState, useCallback, useEffect, Suspense, lazy } from 'react';
import { Toaster, toast } from 'react-hot-toast';
import Sidebar from './components/Sidebar';
import Header from './components/Header';
import ErrorBoundary from './components/ErrorBoundary';
import LoginPage from './components/LoginPage';
import { AuthProvider, useAuth } from './contexts/AuthContext';
import { ApiLead, refreshPipeline, fetchLead } from './services/api';
import type { LeadFilterPreset } from './components/LeadTable';

const Dashboard = lazy(() => import('./components/Dashboard'));
const LeadTable = lazy(() => import('./components/LeadTable'));
const LeadDetail = lazy(() => import('./components/LeadDetail'));
const AgentPanel = lazy(() => import('./components/AgentPanel'));

const AppContent: React.FC = () => {
  const { isAuthenticated, isLoading, logout } = useAuth();

  // Show loading spinner while checking auth
  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50">
        <div className="flex flex-col items-center gap-3">
          <svg className="animate-spin h-8 w-8 text-gray-400" viewBox="0 0 24 24">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
          </svg>
          <span className="text-sm text-gray-500">Loading...</span>
        </div>
      </div>
    );
  }

  // Show login page if not authenticated
  if (!isAuthenticated) {
    return <LoginPage />;
  }

  return <AuthenticatedApp onLogout={logout} />;
};

const AuthenticatedApp: React.FC<{ onLogout: () => void }> = ({ onLogout }) => {
  const { user } = useAuth();
  const [activeTab, setActiveTab] = useState<'dashboard' | 'leads'>('dashboard');
  const [selectedLead, setSelectedLead] = useState<ApiLead | null>(null);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);
  const [isAgentOpen, setIsAgentOpen] = useState(false);
  const [leadFilterPreset, setLeadFilterPreset] = useState<LeadFilterPreset | null>(null);

  // Called when LeadDetail enriches or updates a lead — refreshes both the detail and the table
  const handleLeadUpdated = useCallback((updatedLead: ApiLead) => {
    setSelectedLead(updatedLead);       // Update the detail modal with fresh data
    setRefreshKey(k => k + 1);          // Trigger table reload so it reflects changes
  }, []);

  // Cmd/Ctrl + K shortcut to toggle agent panel
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        setIsAgentOpen(prev => !prev);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, []);

  const handleRefresh = useCallback(async () => {
    const confirmed = window.confirm(
      'This will refresh ALL ~200k buildings from HPD. This runs in the background and may take several minutes. Continue?'
    );
    if (!confirmed) return;
    
    setIsRefreshing(true);
    try {
      await refreshPipeline(true);
      setRefreshKey(k => k + 1);
      toast.success('Refresh started in background. Check the dashboard for progress.');
    } catch (err) {
      console.error('Failed to refresh:', err);
      toast.error('Failed to start refresh. Make sure the backend is running.');
    } finally {
      setIsRefreshing(false);
    }
  }, []);

  const sectionLoader = (
    <div className="py-16 text-center text-sm text-gray-500">Loading section...</div>
  );

  return (
    <ErrorBoundary>
    <div className="flex h-screen bg-white text-gray-800 overflow-hidden font-sans">
      {/* Toast Notifications */}
      <Toaster 
        position="top-right"
        toastOptions={{
          duration: 4000,
          style: {
            background: '#ffffff',
            color: '#111827',
            border: '1px solid #e5e7eb',
            boxShadow: '0 4px 6px -1px rgba(0,0,0,0.1)',
          },
          success: {
            iconTheme: {
              primary: '#059669',
              secondary: '#ffffff',
            },
          },
          error: {
            iconTheme: {
              primary: '#ef4444',
              secondary: '#ffffff',
            },
          },
        }}
      />
      {/* Mobile Menu Button */}
      <button
        onClick={() => setIsMobileMenuOpen(!isMobileMenuOpen)}
        className="lg:hidden fixed top-3 left-3 z-50 p-3 bg-white rounded-xl border border-gray-200 shadow-sm"
        aria-label={isMobileMenuOpen ? 'Close navigation menu' : 'Open navigation menu'}
      >
        <svg className="w-6 h-6 text-gray-700" fill="none" stroke="currentColor" viewBox="0 0 24 24">
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
          className="lg:hidden fixed inset-0 bg-black/40 z-40"
          onClick={() => setIsMobileMenuOpen(false)}
        />
      )}

      {/* Sidebar Navigation */}
      <Sidebar 
        activeTab={activeTab} 
        onLogout={onLogout}
        userEmail={user?.email}
        onTabChange={(tab) => {
          setActiveTab(tab);
          setIsMobileMenuOpen(false);
        }}
        isMobileOpen={isMobileMenuOpen}
        onToggleAgent={() => setIsAgentOpen(prev => !prev)}
      />

      {/* Primary Application Interface */}
      <div className="flex-1 flex flex-col min-w-0">
        <Header />
        
        <main className="flex-1 overflow-y-auto p-3 sm:p-6 lg:p-12 bg-gray-50">
          <div className="max-w-[1600px] mx-auto space-y-8">
            <header className="flex flex-col sm:flex-row justify-between items-start sm:items-end gap-4 sm:gap-6 pl-12 sm:pl-0">
              <div className="space-y-1">
                <h1 className="text-2xl sm:text-3xl font-extrabold text-gray-900 tracking-tight">
                  {activeTab === 'dashboard' ? 'Lead Dashboard' : 'All Leads'}
                </h1>
                <p className="text-gray-500 text-xs sm:text-sm max-w-xl leading-relaxed">
                  {activeTab === 'dashboard' 
                    ? 'NYC property management companies ranked by portfolio size and acquisition potential.' 
                    : 'Browse, filter, and take action on property management leads.'}
                </p>
              </div>
              <button 
                onClick={handleRefresh}
                disabled={isRefreshing}
                className="flex items-center gap-2 px-4 sm:px-5 py-2 sm:py-2.5 bg-white hover:bg-gray-50 text-gray-700 rounded-lg font-medium text-sm transition-all shadow-sm disabled:opacity-50 disabled:cursor-not-allowed border border-gray-200 self-start sm:self-auto"
              >
                <svg className={`w-4 h-4 ${isRefreshing ? 'animate-spin' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>
                {isRefreshing ? 'Refreshing...' : 'Refresh Data'}
              </button>
            </header>

            <Suspense fallback={sectionLoader}>
              <div className="transition-all duration-500 ease-out" key={refreshKey}>
                {activeTab === 'dashboard' ? (
                  <Dashboard
                    onSelectLead={setSelectedLead}
                    onNavigateToLeads={(filters) => {
                      if (filters) setLeadFilterPreset(filters);
                      setActiveTab('leads');
                    }}
                  />
                ) : (
                  <LeadTable
                    onSelectLead={setSelectedLead}
                    filterPreset={leadFilterPreset}
                    onFilterPresetConsumed={() => setLeadFilterPreset(null)}
                  />
                )}
              </div>
            </Suspense>
          </div>
        </main>
      </div>

      {/* Agent Panel */}
      <Suspense fallback={null}>
        <AgentPanel
          isOpen={isAgentOpen}
          onClose={() => setIsAgentOpen(false)}
          onSelectLead={(leadId) => {
            fetchLead(leadId)
              .then(lead => { if (lead) setSelectedLead(lead); })
              .catch(err => { console.error('Failed to fetch lead:', err); toast.error('Could not load lead details'); });
          }}
        />
      </Suspense>

      {/* Lead Detail Modal (z-50) */}
      {selectedLead && (
        <Suspense fallback={null}>
          <LeadDetail 
            lead={selectedLead} 
            onClose={() => setSelectedLead(null)}
            onLeadUpdated={handleLeadUpdated}
          />
        </Suspense>
      )}
    </div>
    </ErrorBoundary>
  );
};

const App: React.FC = () => (
  <AuthProvider>
    <AppContent />
  </AuthProvider>
);

export default App;
