import React, { useState, useRef, useEffect, useCallback } from 'react';
import {
  agentChat,
  AgentSSEEvent,
  AgentLeadRow,
  AgentScriptRow,
  AgentBriefingPreview,
  AgentRentComparison,
  AgentConfirmation,
} from '../services/api';
import AgentMessage from './AgentMessage';

interface AgentChatProps {
  conversationId?: string;
  onConversationIdChange: (id: string | undefined) => void;
  onSelectLead: (leadId: string) => void;
}

interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  text: string;
  leads?: AgentLeadRow[];
  scripts?: AgentScriptRow[];
  briefing?: AgentBriefingPreview;
  rentComparisons?: AgentRentComparison[];
  confirmation?: AgentConfirmation;
  actions?: string[];
  error?: string;
  filtersApplied?: Record<string, unknown>;
}

const EXAMPLE_PROMPTS = [
  "Find 50-150 unit Manhattan PM companies with high unit/building ratios",
  "Which of my top 100 leads still need enrichment?",
  "Show me distressed companies with the highest revenue potential",
  "Prepare cold call scripts for my best Brooklyn leads",
  "What does my pipeline look like right now?",
  "Email me a brief of all leads in first_contact stage",
];

// Friendly tool name mapping
const TOOL_LABELS: Record<string, string> = {
  query_leads: 'Searching leads',
  get_lead_details: 'Looking up lead details',
  get_stats: 'Pulling database stats',
  generate_cold_call_scripts: 'Writing cold call scripts',
  compile_email_briefing: 'Compiling email briefing',
  refine_rent_estimates: 'Researching rent data',
  update_leads_batch: 'Preparing lead updates',
  enrich_leads_batch: 'Starting enrichment',
};

// Elapsed time display
function formatElapsed(seconds: number): string {
  if (seconds < 5) return '';
  if (seconds < 60) return `${seconds}s`;
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

const AgentChat: React.FC<AgentChatProps> = ({
  conversationId,
  onConversationIdChange,
  onSelectLead,
}) => {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [isStreaming, setIsStreaming] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [elapsedSecs, setElapsedSecs] = useState(0);
  const [lastFailedMsg, setLastFailedMsg] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Auto-scroll to bottom on new messages
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, status]);

  // Focus input when panel opens
  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  // Abort SSE stream on unmount to prevent memory leaks
  useEffect(() => {
    return () => { abortRef.current?.abort(); };
  }, []);

  // Reset messages when conversation changes
  useEffect(() => {
    if (!conversationId) {
      setMessages([]);
      setLastFailedMsg(null);
    }
  }, [conversationId]);

  // Elapsed time ticker
  useEffect(() => {
    if (isStreaming) {
      setElapsedSecs(0);
      timerRef.current = setInterval(() => {
        setElapsedSecs(prev => prev + 1);
      }, 1000);
    } else {
      if (timerRef.current) {
        clearInterval(timerRef.current);
        timerRef.current = null;
      }
      setElapsedSecs(0);
    }
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [isStreaming]);

  const handleCancel = useCallback(() => {
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
    setIsStreaming(false);
    setStatus(null);
  }, []);

  const sendMessage = useCallback((text: string, confirmation?: { action_id: string; confirmed: boolean }) => {
    if (!text.trim() && !confirmation) return;
    if (isStreaming) return;
    setLastFailedMsg(null);

    // Add user message to UI
    if (text.trim()) {
      const userMsg: ChatMessage = {
        id: `user-${Date.now()}`,
        role: 'user',
        text: text.trim(),
      };
      setMessages(prev => [...prev, userMsg]);
    }

    setInput('');
    setIsStreaming(true);
    setStatus('Connecting...');

    // Prepare assistant message accumulator
    const assistantId = `assistant-${Date.now()}`;
    let accumulatedText = '';
    let accumulatedLeads: AgentLeadRow[] | undefined;
    let accumulatedScripts: AgentScriptRow[] | undefined;
    let accumulatedBriefing: AgentBriefingPreview | undefined;
    let accumulatedRents: AgentRentComparison[] | undefined;
    let accumulatedConfirmation: AgentConfirmation | undefined;
    let accumulatedActions: string[] | undefined;
    let accumulatedError: string | undefined;
    let accumulatedFilters: Record<string, unknown> | undefined;

    const updateAssistant = () => {
      setMessages(prev => {
        const existing = prev.find(m => m.id === assistantId);
        const assistantMsg: ChatMessage = {
          id: assistantId,
          role: 'assistant',
          text: accumulatedText,
          leads: accumulatedLeads,
          scripts: accumulatedScripts,
          briefing: accumulatedBriefing,
          rentComparisons: accumulatedRents,
          confirmation: accumulatedConfirmation,
          actions: accumulatedActions,
          error: accumulatedError,
          filtersApplied: accumulatedFilters,
        };
        if (existing) {
          return prev.map(m => m.id === assistantId ? assistantMsg : m);
        }
        return [...prev, assistantMsg];
      });
    };

    const handleEvent = (event: AgentSSEEvent) => {
      switch (event.type) {
        case 'status':
          setStatus(event.data);
          break;
        case 'partial':
          accumulatedText += event.data;
          setStatus(null);
          updateAssistant();
          break;
        case 'leads':
          accumulatedLeads = event.data;
          setStatus(null);
          updateAssistant();
          break;
        case 'scripts':
          accumulatedScripts = event.data;
          setStatus(null);
          updateAssistant();
          break;
        case 'briefing_preview':
          accumulatedBriefing = event.data;
          setStatus(null);
          updateAssistant();
          break;
        case 'rent_comparison':
          accumulatedRents = event.data;
          setStatus(null);
          updateAssistant();
          break;
        case 'needs_confirmation':
          accumulatedConfirmation = event.data;
          setStatus(null);
          updateAssistant();
          break;
        case 'actions':
          accumulatedActions = event.data;
          setStatus(null);
          updateAssistant();
          break;
        case 'error':
          accumulatedError = event.data;
          setStatus(null);
          setLastFailedMsg(text.trim());
          updateAssistant();
          break;
        case 'tool_call': {
          const toolLabel = TOOL_LABELS[event.data.name] || `Running ${event.data.name}`;
          setStatus(toolLabel + '...');
          break;
        }
        case 'keepalive':
          break;
        case 'done':
          setIsStreaming(false);
          setStatus(null);
          if (event.data.conversation_id) {
            onConversationIdChange(event.data.conversation_id);
          }
          break;
      }
    };

    // Safety timeout: if no events received for 180 seconds, surface an error.
    // Re-armed on every event (including keepalives) so a mid-stream hang is also caught.
    let safetyTimer: ReturnType<typeof setTimeout>;
    const armSafetyTimeout = () => {
      clearTimeout(safetyTimer);
      safetyTimer = setTimeout(() => {
        if (abortRef.current) {
          abortRef.current.abort();
          abortRef.current = null;
          setIsStreaming(false);
          setStatus(null);
          setLastFailedMsg(text.trim());
          accumulatedError = 'No response received after 180 seconds. The server may be waking up or processing a large request. Please retry.';
          updateAssistant();
        }
      }, 180_000);
    };
    armSafetyTimeout();

    abortRef.current = agentChat(
      {
        message: text.trim(),
        conversation_id: conversationId,
        confirmation,
      },
      (event) => {
        armSafetyTimeout(); // Re-arm on each event
        handleEvent(event);
      },
      (err) => {
        clearTimeout(safetyTimer);
        setIsStreaming(false);
        setStatus(null);
        setLastFailedMsg(text.trim());
        // Provide user-friendly error messages
        let errorMsg = err.message;
        if (err.message.includes('Failed to fetch') || err.message.includes('NetworkError') || err.message.includes('Load failed')) {
          errorMsg = 'Could not reach the server. Check your connection and try again.';
        } else if (err.message.includes('timeout') || err.message.includes('Timeout')) {
          errorMsg = 'Request timed out. The server may be busy - try again in a moment.';
        }
        accumulatedError = errorMsg;
        updateAssistant();
      },
    );
  }, [isStreaming, conversationId, onConversationIdChange]);

  const handleConfirm = useCallback((actionId: string, confirmed: boolean) => {
    sendMessage('', { action_id: actionId, confirmed });
  }, [sendMessage]);

  const handleRetry = useCallback(() => {
    if (lastFailedMsg) {
      // Remove the error message and its preceding user message in one atomic update
      setMessages(prev => {
        let trimmed = [...prev];
        // Remove trailing assistant error
        if (trimmed.length > 0 && trimmed[trimmed.length - 1]?.role === 'assistant' && trimmed[trimmed.length - 1]?.error) {
          trimmed = trimmed.slice(0, -1);
        }
        // Remove trailing user message (the one that caused the error)
        if (trimmed.length > 0 && trimmed[trimmed.length - 1]?.role === 'user') {
          trimmed = trimmed.slice(0, -1);
        }
        return trimmed;
      });
      const msg = lastFailedMsg;
      setLastFailedMsg(null);
      // Small delay so state updates propagate
      setTimeout(() => sendMessage(msg), 50);
    }
  }, [lastFailedMsg, sendMessage]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage(input);
    }
  };

  // Contextual quick-action chips
  const getQuickActions = (): string[] => {
    if (messages.length === 0) return [];
    const lastAssistant = [...messages].reverse().find(m => m.role === 'assistant');
    if (!lastAssistant) return [];
    if (lastAssistant.error) return []; // Don't show actions after an error
    if (lastAssistant.scripts) return ['Start Calling', 'Email me these', 'Narrow down'];
    if (lastAssistant.leads) return ['Enrich these', 'Generate scripts', 'Show details'];
    if (lastAssistant.actions) return ["What's next?", 'Show updated pipeline'];
    return [];
  };

  const quickActions = getQuickActions();
  const showEmptyState = messages.length === 0 && !isStreaming;
  const elapsedDisplay = formatElapsed(elapsedSecs);

  return (
    <div className="flex-1 flex flex-col min-h-0">
      {/* Messages area */}
      <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
        {showEmptyState ? (
          <div className="flex flex-col items-center justify-center h-full text-center px-4">
            <div className="w-12 h-12 bg-emerald-50 rounded-xl flex items-center justify-center mb-4">
              <svg className="w-6 h-6 text-emerald-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z"/>
              </svg>
            </div>
            <h3 className="text-lg font-semibold text-gray-900 mb-1">Ask me anything about your leads</h3>
            <p className="text-sm text-gray-500 mb-6 max-w-sm">
              I can search, analyze, enrich, and take action on 102k+ property management leads.
            </p>
            <div className="space-y-2 w-full max-w-sm">
              {EXAMPLE_PROMPTS.map((prompt, i) => (
                <button
                  key={i}
                  onClick={() => sendMessage(prompt)}
                  className="w-full text-left px-4 py-3 text-sm text-gray-700 bg-gray-50 hover:bg-gray-100 rounded-lg transition-colors border border-gray-100"
                >
                  {prompt}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <>
            {messages.map((msg) => (
              <div key={msg.id}>
                {msg.role === 'user' ? (
                  <div className="flex justify-end">
                    <div className="max-w-[85%] bg-gray-100 rounded-2xl px-4 py-2.5 text-sm text-gray-900">
                      {msg.text}
                    </div>
                  </div>
                ) : (
                  <AgentMessage
                    message={msg}
                    onSelectLead={onSelectLead}
                    onConfirm={handleConfirm}
                  />
                )}
              </div>
            ))}

            {/* Streaming status indicator */}
            {isStreaming && status && (
              <div className="flex items-start gap-3 px-1 py-2">
                <div className="flex-shrink-0 mt-1">
                  {/* Animated spinner */}
                  <div className="w-5 h-5 border-2 border-emerald-200 border-t-emerald-600 rounded-full animate-spin" />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-gray-700">{status}</span>
                    {elapsedDisplay && (
                      <span className="text-xs text-gray-400">{elapsedDisplay}</span>
                    )}
                  </div>
                  {elapsedSecs >= 5 && elapsedSecs < 30 && (
                    <p className="text-xs text-gray-400 mt-0.5">
                      AI is analyzing your data...
                    </p>
                  )}
                  {elapsedSecs >= 30 && elapsedSecs < 90 && (
                    <p className="text-xs text-amber-500 mt-0.5">
                      Taking longer than usual. Complex queries can take up to 2 minutes.
                    </p>
                  )}
                  {elapsedSecs >= 90 && (
                    <p className="text-xs text-amber-600 mt-0.5">
                      Still processing. If this is the first request after idle, the backend may be warming up.
                    </p>
                  )}
                </div>
              </div>
            )}

            {/* Retry button after error */}
            {lastFailedMsg && !isStreaming && (
              <div className="flex justify-center py-2">
                <button
                  onClick={handleRetry}
                  className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-emerald-700 bg-emerald-50 hover:bg-emerald-100 rounded-lg border border-emerald-200 transition-colors"
                >
                  <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/>
                  </svg>
                  Try again
                </button>
              </div>
            )}

            <div ref={messagesEndRef} />
          </>
        )}
      </div>

      {/* Quick action chips */}
      {quickActions.length > 0 && !isStreaming && (
        <div className="flex gap-2 px-4 pb-2 flex-wrap">
          {quickActions.map((action, i) => (
            <button
              key={i}
              onClick={() => sendMessage(action)}
              className="bg-gray-100 text-gray-600 rounded-full px-3 py-2 sm:py-1 text-xs hover:bg-gray-200 transition-colors"
            >
              {action}
            </button>
          ))}
        </div>
      )}

      {/* Input bar */}
      <div className="border-t border-gray-200 bg-white px-4 py-3">
        <div className="flex items-end gap-2">
          <textarea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={isStreaming ? 'Agent is working...' : 'Ask anything...'}
            disabled={isStreaming}
            rows={1}
            className="flex-1 resize-none bg-gray-50 rounded-xl border border-gray-200 text-gray-900 placeholder-gray-400 px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/30 focus:border-emerald-500 disabled:opacity-50 max-h-20 overflow-y-auto"
            style={{ minHeight: '40px' }}
            onInput={(e) => {
              const target = e.target as HTMLTextAreaElement;
              target.style.height = 'auto';
              target.style.height = Math.min(target.scrollHeight, 80) + 'px';
            }}
          />
          {isStreaming ? (
            /* Cancel button while streaming */
            <button
              onClick={handleCancel}
              className="p-2.5 bg-gray-200 hover:bg-gray-300 text-gray-600 rounded-xl transition-colors flex-shrink-0"
              title="Cancel"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2.5" d="M6 18L18 6M6 6l12 12"/>
              </svg>
            </button>
          ) : (
            /* Send button */
            <button
              onClick={() => sendMessage(input)}
              disabled={!input.trim()}
              className="p-2.5 bg-emerald-600 hover:bg-emerald-700 text-white rounded-xl transition-colors disabled:opacity-40 disabled:cursor-not-allowed flex-shrink-0"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8"/>
              </svg>
            </button>
          )}
        </div>
      </div>
    </div>
  );
};

export default AgentChat;
