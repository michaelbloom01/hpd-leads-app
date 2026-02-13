import React, { useState, useEffect, useRef } from 'react';
import AgentChat from './AgentChat';

interface AgentPanelProps {
  isOpen: boolean;
  onClose: () => void;
  onSelectLead: (leadId: string) => void;
}

const AgentPanel: React.FC<AgentPanelProps> = ({ isOpen, onClose, onSelectLead }) => {
  const [isExpanded, setIsExpanded] = useState(false);
  const [conversationId, setConversationId] = useState<string | undefined>(undefined);
  const panelRef = useRef<HTMLDivElement>(null);

  // Escape key to close — but not if user is typing in the input
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && isOpen) {
        const active = document.activeElement;
        if (active && (active.tagName === 'TEXTAREA' || active.tagName === 'INPUT')) {
          (active as HTMLElement).blur();
        } else {
          onClose();
        }
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, onClose]);

  if (!isOpen) {
    // Floating button when closed
    return (
      <button
        onClick={() => {/* Parent handles toggle via Cmd+K or Sidebar */}}
        className="hidden"
        aria-hidden="true"
      />
    );
  }

  return (
    <>
      {/* Backdrop — blocks interaction on mobile, subtle shadow on desktop */}
      {!isExpanded && (
        <div
          className="fixed inset-0 z-[39] bg-black/40 md:bg-transparent md:pointer-events-none"
          onClick={onClose}
          style={{ boxShadow: window.innerWidth >= 768 ? 'inset -450px 0 60px -30px rgba(0,0,0,0.05)' : 'none' }}
        />
      )}

      {/* Panel — full-screen on mobile, 450px sidebar on desktop */}
      <div
        ref={panelRef}
        className={`fixed inset-y-0 right-0 z-40 flex flex-col bg-white border-l border-gray-200 shadow-xl transition-all duration-300 ease-in-out ${
          isExpanded ? 'left-0' : 'w-full md:w-[450px]'
        }`}
      >
        {/* Panel Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 bg-gray-50">
          <div className="flex items-center gap-3">
            <div className="w-7 h-7 bg-emerald-600 rounded-lg flex items-center justify-center">
              <svg className="w-4 h-4 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z"/>
              </svg>
            </div>
            <h2 className="text-sm font-semibold text-gray-900">Agent</h2>
          </div>

          <div className="flex items-center gap-1">
            {/* New Chat */}
            <button
              onClick={() => setConversationId(undefined)}
              className="p-1.5 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded-md transition-colors"
              title="New conversation"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 4v16m8-8H4"/>
              </svg>
            </button>

            {/* Expand/Collapse */}
            <button
              onClick={() => setIsExpanded(!isExpanded)}
              className="p-1.5 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded-md transition-colors"
              title={isExpanded ? 'Collapse' : 'Expand'}
            >
              {isExpanded ? (
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 9V4.5M9 9H4.5M9 9L3.5 3.5M15 9V4.5M15 9h4.5M15 9l5.5-5.5M9 15v4.5M9 15H4.5M9 15l-5.5 5.5M15 15h4.5M15 15v4.5m0-4.5l5.5 5.5"/>
                </svg>
              ) : (
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M4 8V4m0 0h4M4 4l5 5m11-1V4m0 0h-4m4 0l-5 5M4 16v4m0 0h4m-4 0l5-5m11 5l-5-5m5 5v-4m0 4h-4"/>
                </svg>
              )}
            </button>

            {/* Close */}
            <button
              onClick={onClose}
              className="p-1.5 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded-md transition-colors"
              title="Close (Esc)"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M6 18L18 6M6 6l12 12"/>
              </svg>
            </button>
          </div>
        </div>

        {/* Chat Content */}
        <AgentChat
          conversationId={conversationId}
          onConversationIdChange={setConversationId}
          onSelectLead={onSelectLead}
        />
      </div>
    </>
  );
};

export default AgentPanel;
