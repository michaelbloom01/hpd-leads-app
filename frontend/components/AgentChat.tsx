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

const AgentChat: React.FC<AgentChatProps> = ({
  conversationId,
  onConversationIdChange,
  onSelectLead,
}) => {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [isStreaming, setIsStreaming] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // Auto-scroll to bottom on new messages
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, status]);

  // Focus input when panel opens
  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  // Reset messages when conversation changes
  useEffect(() => {
    if (!conversationId) {
      setMessages([]);
    }
  }, [conversationId]);

  const sendMessage = useCallback((text: string, confirmation?: { action_id: string; confirmed: boolean }) => {
    if (!text.trim() && !confirmation) return;
    if (isStreaming) return;

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
    setStatus('Thinking...');

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
          updateAssistant();
          break;
        case 'tool_call':
          setStatus(`Running ${event.data.name}...`);
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

    abortRef.current = agentChat(
      {
        message: text.trim(),
        conversation_id: conversationId,
        confirmation,
      },
      handleEvent,
      (err) => {
        setIsStreaming(false);
        setStatus(null);
        accumulatedError = err.message;
        updateAssistant();
      },
    );
  }, [isStreaming, conversationId, onConversationIdChange]);

  const handleConfirm = useCallback((actionId: string, confirmed: boolean) => {
    sendMessage('', { action_id: actionId, confirmed });
  }, [sendMessage]);

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
    if (lastAssistant.scripts) return ['Start Calling', 'Email me these', 'Narrow down'];
    if (lastAssistant.leads) return ['Enrich these', 'Generate scripts', 'Show details'];
    if (lastAssistant.actions) return ["What's next?", 'Show updated pipeline'];
    return [];
  };

  const quickActions = getQuickActions();
  const showEmptyState = messages.length === 0;

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
                  className="w-full text-left px-3 py-2.5 text-sm text-gray-700 bg-gray-50 hover:bg-gray-100 rounded-lg transition-colors border border-gray-100"
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

            {/* Status indicator */}
            {status && (
              <div className="flex items-center gap-2 px-2 py-1">
                <div className="w-2 h-2 bg-emerald-500 rounded-full animate-pulse" />
                <span className="text-xs text-gray-500">{status}</span>
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
              className="bg-gray-100 text-gray-600 rounded-full px-3 py-1 text-xs hover:bg-gray-200 transition-colors"
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
            placeholder={isStreaming ? 'Agent is thinking...' : 'Ask anything...'}
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
          <button
            onClick={() => sendMessage(input)}
            disabled={isStreaming || !input.trim()}
            className="p-2.5 bg-emerald-600 hover:bg-emerald-700 text-white rounded-xl transition-colors disabled:opacity-40 disabled:cursor-not-allowed flex-shrink-0"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8"/>
            </svg>
          </button>
        </div>
      </div>
    </div>
  );
};

export default AgentChat;
