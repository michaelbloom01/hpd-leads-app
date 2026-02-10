import React, { useState, useCallback } from 'react';
import { AgentScriptRow, logOutreachFromCallMode } from '../services/api';

interface AgentCallModeProps {
  scripts: AgentScriptRow[];
  onClose: () => void;
}

const OUTCOMES = [
  { id: 'interested', label: 'Interested', color: 'bg-emerald-600 hover:bg-emerald-700 text-white' },
  { id: 'not_interested', label: 'Not Interested', color: 'bg-gray-200 hover:bg-gray-300 text-gray-700' },
  { id: 'no_answer', label: 'No Answer', color: 'bg-amber-100 hover:bg-amber-200 text-amber-800' },
  { id: 'callback', label: 'Callback', color: 'bg-blue-100 hover:bg-blue-200 text-blue-800' },
  { id: 'left_voicemail', label: 'Left Voicemail', color: 'bg-purple-100 hover:bg-purple-200 text-purple-800' },
];

const AgentCallMode: React.FC<AgentCallModeProps> = ({ scripts, onClose }) => {
  const [currentIndex, setCurrentIndex] = useState(0);
  const [logging, setLogging] = useState(false);

  const current = scripts[currentIndex];
  const progress = `Lead ${currentIndex + 1} of ${scripts.length}`;

  const handleOutcome = useCallback(async (outcome: string) => {
    if (!current || logging) return;
    setLogging(true);
    try {
      await logOutreachFromCallMode(current.lead_id, outcome);
      // Auto-advance to next lead
      if (currentIndex < scripts.length - 1) {
        setCurrentIndex(i => i + 1);
      }
    } catch (err) {
      console.error('Failed to log outreach:', err);
    } finally {
      setLogging(false);
    }
  }, [current, currentIndex, scripts.length, logging]);

  const handleSkip = useCallback(() => {
    if (currentIndex < scripts.length - 1) {
      setCurrentIndex(i => i + 1);
    }
  }, [currentIndex, scripts.length]);

  if (!current) {
    return (
      <div className="fixed inset-0 z-[60] bg-white flex items-center justify-center">
        <div className="text-center space-y-4">
          <div className="w-16 h-16 mx-auto bg-emerald-50 rounded-full flex items-center justify-center">
            <svg className="w-8 h-8 text-emerald-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M5 13l4 4L19 7"/>
            </svg>
          </div>
          <h2 className="text-xl font-bold text-gray-900">All done!</h2>
          <p className="text-gray-500">You've gone through all {scripts.length} leads.</p>
          <button
            onClick={onClose}
            className="px-6 py-2.5 bg-emerald-600 text-white rounded-lg hover:bg-emerald-700 font-medium transition-colors"
          >
            Close
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="fixed inset-0 z-[60] bg-white flex flex-col">
      {/* Top bar */}
      <div className="flex items-center justify-between px-6 py-3 border-b border-gray-200 bg-gray-50">
        <div className="flex items-center gap-4">
          <span className="text-sm font-medium text-gray-500">{progress}</span>
          <div className="w-32 h-1.5 bg-gray-200 rounded-full overflow-hidden">
            <div
              className="h-full bg-emerald-500 rounded-full transition-all"
              style={{ width: `${((currentIndex + 1) / scripts.length) * 100}%` }}
            />
          </div>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-base font-semibold text-gray-900">{current.company_name}</span>
        </div>
        <button
          onClick={onClose}
          className="p-2 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded-lg transition-colors"
        >
          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M6 18L18 6M6 6l12 12"/>
          </svg>
        </button>
      </div>

      {/* Main content */}
      <div className="flex-1 flex overflow-hidden">
        {/* Left: Contact info */}
        <div className="w-2/5 border-r border-gray-200 p-6 overflow-y-auto bg-white">
          <h3 className="text-lg font-bold text-gray-900 mb-4">{current.company_name}</h3>

          {current.owner_name && (
            <div className="mb-4">
              <label className="text-xs text-gray-400 font-medium uppercase tracking-wider">Contact</label>
              <p className="text-sm text-gray-900 mt-0.5">{current.owner_name}</p>
            </div>
          )}

          {current.phone ? (
            <div className="mb-4">
              <label className="text-xs text-gray-400 font-medium uppercase tracking-wider">Phone</label>
              <a
                href={`tel:${current.phone}`}
                className="flex items-center gap-2 mt-1 text-lg font-semibold text-emerald-600 hover:text-emerald-700"
              >
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M3 5a2 2 0 012-2h3.28a1 1 0 01.948.684l1.498 4.493a1 1 0 01-.502 1.21l-2.257 1.13a11.042 11.042 0 005.516 5.516l1.13-2.257a1 1 0 011.21-.502l4.493 1.498a1 1 0 01.684.949V19a2 2 0 01-2 2h-1C9.716 21 3 14.284 3 6V5z"/>
                </svg>
                {current.phone}
              </a>
            </div>
          ) : (
            <div className="mb-4 p-3 bg-amber-50 rounded-lg text-sm text-amber-700">
              No phone number available. Consider enriching this lead.
            </div>
          )}

          {/* Navigation */}
          <div className="flex gap-2 mt-8">
            <button
              onClick={() => setCurrentIndex(i => Math.max(0, i - 1))}
              disabled={currentIndex === 0}
              className="flex-1 px-3 py-2 text-sm text-gray-600 bg-gray-100 rounded-lg hover:bg-gray-200 disabled:opacity-40 transition-colors"
            >
              Previous
            </button>
            <button
              onClick={() => setCurrentIndex(i => Math.min(scripts.length - 1, i + 1))}
              disabled={currentIndex === scripts.length - 1}
              className="flex-1 px-3 py-2 text-sm text-gray-600 bg-gray-100 rounded-lg hover:bg-gray-200 disabled:opacity-40 transition-colors"
            >
              Next
            </button>
          </div>
        </div>

        {/* Right: Script */}
        <div className="flex-1 p-6 overflow-y-auto bg-gray-50">
          <h4 className="text-sm font-semibold text-gray-500 uppercase tracking-wider mb-3">Cold Call Script</h4>
          <div className="bg-white rounded-xl border border-gray-200 p-5 text-sm text-gray-800 whitespace-pre-wrap leading-relaxed shadow-sm">
            {current.script}
          </div>
        </div>
      </div>

      {/* Bottom bar: Outcomes */}
      <div className="border-t border-gray-200 bg-white px-6 py-3">
        <div className="flex items-center gap-2 justify-center">
          {OUTCOMES.map((outcome) => (
            <button
              key={outcome.id}
              onClick={() => handleOutcome(outcome.id)}
              disabled={logging}
              className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors disabled:opacity-50 ${outcome.color}`}
            >
              {outcome.label}
            </button>
          ))}
          <div className="w-px h-8 bg-gray-200 mx-1" />
          <button
            onClick={handleSkip}
            disabled={currentIndex === scripts.length - 1}
            className="px-4 py-2 rounded-lg text-sm font-medium text-gray-400 hover:text-gray-600 hover:bg-gray-100 transition-colors disabled:opacity-50"
          >
            Skip
          </button>
        </div>
      </div>
    </div>
  );
};

export default AgentCallMode;
