
import React, { Component, useState, useCallback, useEffect, Suspense, lazy } from 'react';
import { BrowserRouter, Routes, Route, useLocation, useMatch, useNavigate, useSearchParams } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
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
const BuildingsPage = lazy(() => import('./components/BuildingsPage'));
const BuildingListsPage = lazy(() => import('./components/BuildingListsPage'));
const BuildingDetailPage = lazy(() => import('./components/BuildingDetailPage'));
const SettingsPage = lazy(() => import('./components/SettingsPage'));
const SmartListsPage = lazy(() => import('./components/SmartListsPage'));
const TargetsPage = lazy(() => import('./components/TargetsPage'));
const TargetDetailPage = lazy(() => import('./components/TargetDetailPage'));
const AlertsPage = lazy(() => import('./components/AlertsPage'));

class RouteErrorBoundary extends Component<{ children: React.ReactNode }, { hasError: boolean }> {
  state = { hasError: false };
  static getDerivedStateFromError() {
    return { hasError: true };
  }
  render() {
    if (this.state.hasError) {
      return (
        <div className="flex flex-col items-center justify-center h-64 gap-4">
          <p className="text-gray-600">Something went wrong loading this page.</p>
          <button
            onClick={() => {
              this.setState({ hasError: false });
              window.location.reload();
            }}
            className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700"
          >
            Reload
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30000,
      retry: 2,
      refetchOnWindowFocus: false,
    },
  },
});

const PAGE_TITLES: Record<string, string> = {
  '/': 'Lead Dashboard',
  '/leads': 'All Leads',
  '/buildings': 'Buildings',
  '/building-lists': 'Building Lists',
  '/targets': 'Targets',
  '/alerts': 'Alerts And Follow-Ups',
  '/settings': 'Settings',
  '/smart-lists': 'Smart Lists',
};

type SelectedLeadSource = 'route' | 'standalone' | null;

const AppContent: React.FC = () => {
  const { isAuthenticated, isLoading, logout } = useAuth();

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

  if (!isAuthenticated) {
    return <LoginPage />;
  }

  return <AuthenticatedApp onLogout={logout} />;
};

const AuthenticatedApp: React.FC<{ onLogout: () => void }> = ({ onLogout }) => {
  const { user } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const leadRouteMatch = useMatch('/leads/:leadId');
  const leadPathId = leadRouteMatch?.params.leadId;
  const [searchParams, setSearchParams] = useSearchParams();
  const [selectedLead, setSelectedLead] = useState<ApiLead | null>(null);
  const [selectedLeadSource, setSelectedLeadSource] = useState<SelectedLeadSource>(null);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);
  const [isAgentOpen, setIsAgentOpen] = useState(false);
  const [leadFilterPreset, setLeadFilterPreset] = useState<LeadFilterPreset | null>(null);

  const replaceLeadSearchParam = useCallback((leadId: string | null) => {
    const nextParams = new URLSearchParams(searchParams);
    if (leadId) {
      nextParams.set('lead', leadId);
    } else {
      nextParams.delete('lead');
    }
    setSearchParams(nextParams, { replace: true });
  }, [searchParams, setSearchParams]);

  const handleLeadsSelectLead = useCallback((lead: ApiLead) => {
    setSelectedLead(lead);
    setSelectedLeadSource('route');
    replaceLeadSearchParam(lead.lead_id);
  }, [replaceLeadSearchParam]);

  const clearRouteSelectedLead = useCallback(() => {
    if (leadPathId) {
      const nextSearch = searchParams.toString();
      navigate(
        {
          pathname: '/leads',
          search: nextSearch ? `?${nextSearch}` : '',
        },
        { replace: true },
      );
      return;
    }
    replaceLeadSearchParam(null);
  }, [leadPathId, navigate, replaceLeadSearchParam, searchParams]);

  const handleLeadUpdated = useCallback((updatedLead: ApiLead) => {
    setSelectedLead(updatedLead);
    setRefreshKey(k => k + 1);
  }, []);

  useEffect(() => {
    const isLeadsRoute = location.pathname === '/leads' || location.pathname.startsWith('/leads/');
    if (!isLeadsRoute) {
      if (selectedLeadSource === 'route') {
        setSelectedLead(null);
        setSelectedLeadSource(null);
      }
      return;
    }

    const leadId = leadPathId ? decodeURIComponent(leadPathId) : searchParams.get('lead');
    if (!leadId) {
      if (selectedLeadSource === 'route') {
        setSelectedLead(null);
        setSelectedLeadSource(null);
      }
      return;
    }

    if (selectedLead?.lead_id === leadId && selectedLeadSource === 'route') {
      return;
    }

    let cancelled = false;
    fetchLead(leadId)
      .then((lead) => {
        if (cancelled) return;
        setSelectedLead(lead);
        setSelectedLeadSource('route');
      })
      .catch((err) => {
        if (cancelled) return;
        console.error('Failed to fetch lead from route:', err);
        toast.error('Could not load lead details');
        clearRouteSelectedLead();
      });

    return () => {
      cancelled = true;
    };
  }, [clearRouteSelectedLead, leadPathId, location.pathname, searchParams, selectedLead?.lead_id, selectedLeadSource]);

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
      'This will refresh ALL ~200k buildings from NYC Open Data. This runs in the background and may take several minutes. Continue?'
    );
    if (!confirmed) return;
    
    setIsRefreshing(true);
    try {
      const started = await refreshPipeline(true);
      setRefreshKey(k => k + 1);
      if (started.dispatch_mode === 'in_process') {
        toast('Refresh started in local fallback mode. Check dashboard job status.');
      } else {
        toast.success('Refresh started in background. Check the dashboard for progress.');
      }
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

  const basePath = '/' + location.pathname.split('/').filter(Boolean)[0] || '/';
  const pageTitle = PAGE_TITLES[basePath] || PAGE_TITLES[location.pathname] || 'Double Edge';
  const pageDescription = basePath === '/' 
    ? 'NYC property management companies ranked by portfolio size and acquisition potential.'
    : basePath === '/leads'
    ? 'Browse, filter, and take action on property management leads.'
    : basePath === '/buildings'
    ? 'Find buildings with high churn probability for PM operator outreach.'
    : basePath === '/smart-lists'
    ? 'Saved filter segments that track lead changes over time.'
    : basePath === '/targets'
    ? 'Import and work curated PM acquisition targets.'
    : basePath === '/alerts'
    ? 'Review follow-ups and workflow alerts across the platform.'
    : basePath === '/settings'
    ? 'Configure scoring weights and monitor data health.'
    : '';

  const showRefresh = basePath === '/' || basePath === '/leads';

  return (
    <ErrorBoundary>
    <div className="flex h-screen bg-white text-gray-800 overflow-hidden font-sans">
      <Toaster 
        position="top-right"
        toastOptions={{
          duration: 4000,
          style: { background: '#ffffff', color: '#111827', border: '1px solid #e5e7eb', boxShadow: '0 4px 6px -1px rgba(0,0,0,0.1)' },
          success: { iconTheme: { primary: '#059669', secondary: '#ffffff' } },
          error: { iconTheme: { primary: '#ef4444', secondary: '#ffffff' } },
        }}
      />

      <button
        onClick={() => setIsMobileMenuOpen(!isMobileMenuOpen)}
        className="lg:hidden fixed top-3 left-3 z-50 p-3 bg-white rounded-xl border border-gray-200 shadow-sm"
        aria-label={isMobileMenuOpen ? 'Close navigation menu' : 'Open navigation menu'}
        aria-expanded={isMobileMenuOpen}
        aria-controls="mobile-nav"
      >
        <svg className="w-6 h-6 text-gray-700" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          {isMobileMenuOpen ? (
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M6 18L18 6M6 6l12 12"/>
          ) : (
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M4 6h16M4 12h16M4 18h16"/>
          )}
        </svg>
      </button>

      {isMobileMenuOpen && (
        <div className="lg:hidden fixed inset-0 bg-black/40 z-40" onClick={() => setIsMobileMenuOpen(false)} />
      )}

      <Sidebar 
        onLogout={onLogout}
        userEmail={user?.email}
        isMobileOpen={isMobileMenuOpen}
        onToggleAgent={() => setIsAgentOpen(prev => !prev)}
      />

      <div className="flex-1 flex flex-col min-w-0">
        <Header />
        
        <main className="flex-1 overflow-y-auto p-3 sm:p-6 lg:p-12 bg-gray-50">
          <div className="max-w-[1600px] mx-auto space-y-8">
            <header className="flex flex-col sm:flex-row justify-between items-start sm:items-end gap-4 sm:gap-6 pl-12 sm:pl-0">
              <div className="space-y-1">
                <h1 className="text-2xl sm:text-3xl font-extrabold text-gray-900 tracking-tight">
                  {pageTitle}
                </h1>
                <p className="text-gray-500 text-xs sm:text-sm max-w-xl leading-relaxed">
                  {pageDescription}
                </p>
              </div>
              {showRefresh && (
                <button 
                  onClick={handleRefresh}
                  disabled={isRefreshing}
                  className="flex items-center gap-2 px-4 sm:px-5 py-2 sm:py-2.5 bg-white hover:bg-gray-50 text-gray-700 rounded-lg font-medium text-sm transition-all shadow-sm disabled:opacity-50 disabled:cursor-not-allowed border border-gray-200 self-start sm:self-auto"
                >
                  <svg className={`w-4 h-4 ${isRefreshing ? 'animate-spin' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>
                  {isRefreshing ? 'Refreshing...' : 'Refresh Data'}
                </button>
              )}
            </header>

            <Suspense fallback={sectionLoader}>
              <div className="transition-all duration-500 ease-out" key={refreshKey}>
                <Routes>
                  <Route path="/" element={
                    <RouteErrorBoundary>
                      <Dashboard
                        onSelectLead={(lead) => {
                          navigate(`/leads/${encodeURIComponent(lead.lead_id)}`);
                        }}
                        onNavigateToLeads={(filters) => {
                          if (filters) setLeadFilterPreset(filters);
                          navigate('/leads');
                        }}
                      />
                    </RouteErrorBoundary>
                  } />
                  <Route path="/leads" element={
                    <RouteErrorBoundary>
                      <LeadTable
                        onSelectLead={handleLeadsSelectLead}
                        filterPreset={leadFilterPreset}
                        onFilterPresetConsumed={() => setLeadFilterPreset(null)}
                      />
                    </RouteErrorBoundary>
                  } />
                  <Route path="/leads/:leadId" element={
                    <RouteErrorBoundary>
                      <LeadTable
                        onSelectLead={handleLeadsSelectLead}
                        filterPreset={leadFilterPreset}
                        onFilterPresetConsumed={() => setLeadFilterPreset(null)}
                      />
                    </RouteErrorBoundary>
                  } />
                  <Route path="/buildings" element={
                    <RouteErrorBoundary>
                      <BuildingsPage />
                    </RouteErrorBoundary>
                  } />
                  <Route path="/building-lists" element={
                    <RouteErrorBoundary>
                      <BuildingListsPage />
                    </RouteErrorBoundary>
                  } />
                  <Route path="/buildings/:bbl" element={
                    <RouteErrorBoundary>
                      <BuildingDetailPage />
                    </RouteErrorBoundary>
                  } />
                  <Route path="/smart-lists" element={
                    <RouteErrorBoundary>
                      <SmartListsPage />
                    </RouteErrorBoundary>
                  } />
                  <Route path="/targets" element={
                    <RouteErrorBoundary>
                      <TargetsPage />
                    </RouteErrorBoundary>
                  } />
                  <Route path="/targets/:targetItemId" element={
                    <RouteErrorBoundary>
                      <TargetDetailPage />
                    </RouteErrorBoundary>
                  } />
                  <Route path="/alerts" element={
                    <RouteErrorBoundary>
                      <AlertsPage />
                    </RouteErrorBoundary>
                  } />
                  <Route path="/settings" element={
                    <RouteErrorBoundary>
                      <SettingsPage />
                    </RouteErrorBoundary>
                  } />
                  <Route path="*" element={
                    <div className="flex flex-col items-center justify-center py-32 text-center">
                      <div className="text-6xl font-bold text-gray-200 mb-4">404</div>
                      <h2 className="text-xl font-bold text-gray-900 mb-2">Page Not Found</h2>
                      <p className="text-sm text-gray-500 mb-6 max-w-sm">The page you're looking for doesn't exist or has been moved.</p>
                      <a href="/" className="px-5 py-2.5 bg-gray-900 text-white text-sm font-medium rounded-lg hover:bg-gray-800 transition-colors">Back to Dashboard</a>
                    </div>
                  } />
                </Routes>
              </div>
            </Suspense>
          </div>
        </main>
      </div>

      <Suspense fallback={null}>
        <RouteErrorBoundary>
          <AgentPanel
            isOpen={isAgentOpen}
            onClose={() => setIsAgentOpen(false)}
            onSelectLead={(leadId) => {
              setIsAgentOpen(false);
              navigate(`/leads/${encodeURIComponent(leadId)}`);
            }}
          />
        </RouteErrorBoundary>
      </Suspense>

      {selectedLead && (
        <Suspense fallback={null}>
          <RouteErrorBoundary>
            <LeadDetail 
              lead={selectedLead} 
              onClose={() => {
                if (selectedLeadSource === 'route' && (location.pathname === '/leads' || location.pathname.startsWith('/leads/'))) {
                  clearRouteSelectedLead();
                } else {
                  setSelectedLead(null);
                  setSelectedLeadSource(null);
                }
              }}
              onLeadUpdated={handleLeadUpdated}
            />
          </RouteErrorBoundary>
        </Suspense>
      )}
    </div>
    </ErrorBoundary>
  );
};

const App: React.FC = () => (
  <QueryClientProvider client={queryClient}>
    <BrowserRouter>
      <AuthProvider>
        <AppContent />
      </AuthProvider>
    </BrowserRouter>
  </QueryClientProvider>
);

export default App;
