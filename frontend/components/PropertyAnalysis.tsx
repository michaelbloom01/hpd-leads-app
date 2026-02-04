
import React, { useState, useEffect } from 'react';
import { analyzeBuildingData } from '../services/geminiService';
import { BuildingLead, AIAnalysis } from '../types';
import { MOCK_VIOLATIONS } from '../constants';

interface Props {
  lead: BuildingLead;
  onClose: () => void;
}

const PropertyAnalysis: React.FC<Props> = ({ lead, onClose }) => {
  const [loading, setLoading] = useState(false);
  const [analysis, setAnalysis] = useState<AIAnalysis | null>(null);

  const getAnalysis = async () => {
    setLoading(true);
    try {
      const result = await analyzeBuildingData(MOCK_VIOLATIONS);
      setAnalysis(result);
    } catch (error) {
      console.error("Critical: Intelligence Retrieval Failure", error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    getAnalysis();
  }, [lead.buildingId]);

  // High-Resolution Satellite View with Dark Filter Logic
  const mapUrl = `https://www.google.com/maps?q=${encodeURIComponent(lead.address + ', ' + lead.borough + ', NY ' + lead.zip)}&output=embed&t=k`;

  return (
    <div className="fixed inset-0 bg-slate-950/98 backdrop-blur-[40px] z-50 flex items-center justify-center p-6 lg:p-16 overflow-y-auto">
      <div className="bg-slate-900 border border-white/10 rounded-[5rem] shadow-[0_0_200px_rgba(0,0,0,1)] w-full max-w-7xl my-auto overflow-hidden flex flex-col animate-in zoom-in-95 fade-in duration-600">
        
        {/* Dossier Command Header */}
        <div className="p-14 border-b border-white/5 flex justify-between items-center bg-slate-950/50 sticky top-0 z-40 backdrop-blur-3xl">
          <div className="flex items-center gap-12">
            <div className={`w-32 h-32 rounded-[3rem] bg-slate-950 border-[6px] flex flex-col items-center justify-center font-mono shadow-inner ${lead.score > 80 ? 'text-rose-500 border-rose-500/10' : 'text-blue-500 border-blue-500/10'}`}>
              <span className="text-5xl font-black tracking-tighter">{lead.score}</span>
              <span className="text-[11px] font-black uppercase tracking-[0.4em] text-slate-700 mt-2">Risk Index</span>
            </div>
            <div>
              <div className="flex items-center gap-6 mb-4">
                <h2 className="text-6xl font-black text-white tracking-tighter leading-none">{lead.address}</h2>
                <div className="px-5 py-1.5 bg-blue-600/10 text-blue-500 text-[11px] font-black uppercase tracking-[0.3em] rounded-full border border-blue-500/20 shadow-[0_0_15px_rgba(37,99,235,0.1)]">Priority Lead</div>
              </div>
              <p className="text-base text-slate-500 font-mono tracking-[0.25em] uppercase flex items-center gap-8">
                <span className="flex items-center gap-2"><span className="text-slate-800">BBL</span> {lead.bbl}</span>
                <span className="w-2 h-2 bg-slate-800 rounded-full"></span>
                <span>{lead.borough}</span>
                <span className="w-2 h-2 bg-slate-800 rounded-full"></span>
                <span>NY {lead.zip}</span>
              </p>
            </div>
          </div>
          <button 
            onClick={onClose} 
            className="p-6 bg-slate-950 hover:bg-slate-800 border border-white/10 rounded-[2.5rem] transition-all group active:scale-90 shadow-2xl"
          >
            <svg className="w-12 h-12 text-slate-700 group-hover:text-white transition-colors" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2.5" d="M6 18L18 6M6 6l12 12"/>
            </svg>
          </button>
        </div>

        <div className="flex-1 p-16 overflow-y-auto custom-scrollbar bg-slate-900/10">
          {loading ? (
            <div className="flex flex-col items-center justify-center py-64 space-y-16">
              <div className="relative">
                <div className="w-40 h-40 border-[10px] border-blue-500/5 border-t-blue-500 rounded-full animate-spin"></div>
                <div className="absolute inset-0 flex items-center justify-center">
                   <div className="w-24 h-24 bg-blue-500/10 rounded-full animate-pulse border-[3px] border-blue-500/20 shadow-[0_0_60px_rgba(37,99,235,0.2)]"></div>
                </div>
              </div>
              <div className="text-center space-y-6">
                <p className="text-blue-500 font-black text-xl uppercase tracking-[0.8em] animate-pulse">Running Neural Appraisal</p>
                <p className="text-slate-800 text-[12px] font-mono uppercase tracking-[0.3em] font-black">Decrypting HPD Historical Violation Sequence...</p>
              </div>
            </div>
          ) : analysis ? (
            <div className="grid grid-cols-1 xl:grid-cols-12 gap-24">
              
              {/* Strategic Dossier Column */}
              <div className="xl:col-span-7 space-y-24">
                <section>
                  <div className="flex items-center gap-6 mb-12">
                    <div className="w-3 h-12 bg-blue-600 rounded-full shadow-[0_0_30px_#2563eb]"></div>
                    <h3 className="text-[16px] font-black text-slate-500 uppercase tracking-[0.6em]">Investment Summary</h3>
                  </div>
                  <div className="bg-slate-950/70 border border-white/5 p-14 rounded-[4rem] relative overflow-hidden group shadow-2xl">
                    <div className="absolute -top-32 -left-32 w-80 h-80 bg-blue-600/[0.03] blur-[150px] rounded-full group-hover:bg-blue-600/[0.07] transition-all duration-1000"></div>
                    <p className="text-slate-100 leading-relaxed text-4xl font-black italic tracking-tight relative z-10">"{analysis.summary}"</p>
                    
                    <div className="mt-20 pt-20 border-t border-white/10 grid grid-cols-1 md:grid-cols-2 gap-20 relative z-10">
                      <div>
                        <h4 className="text-[12px] font-black text-slate-600 uppercase tracking-[0.5em] mb-8">Growth Thesis</h4>
                        <p className="text-slate-400 text-lg leading-relaxed font-semibold">{analysis.investmentThesis}</p>
                      </div>
                      <div className="bg-blue-600/10 p-12 rounded-[3rem] border border-blue-500/20 shadow-inner group-hover:border-blue-500/40 transition-colors">
                        <h4 className="text-[12px] font-black text-blue-400 uppercase tracking-[0.5em] mb-8">Strategic Order</h4>
                        <p className="text-blue-100 text-xl font-black tracking-tight leading-snug uppercase italic">{analysis.suggestedAction}</p>
                      </div>
                    </div>
                  </div>
                </section>

                <section>
                  <div className="flex items-center gap-6 mb-12">
                    <div className="w-3 h-12 bg-slate-800 rounded-full"></div>
                    <h3 className="text-[16px] font-black text-slate-500 uppercase tracking-[0.6em]">Ownership Intel</h3>
                  </div>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-12">
                    {lead.contacts.map((contact, i) => (
                      <div key={i} className="bg-slate-950/60 p-12 rounded-[3.5rem] border border-white/5 hover:border-blue-500/40 transition-all shadow-2xl group/card">
                        <div className="flex justify-between items-start mb-12">
                          <div>
                            <p className="text-3xl font-black text-white group-hover/card:text-blue-400 transition-colors">{contact.name}</p>
                            <p className="text-[12px] text-blue-500 uppercase font-black tracking-[0.4em] mt-4">{contact.role}</p>
                          </div>
                          <div className="p-6 bg-slate-900/80 rounded-[2rem] border border-white/5 text-slate-600 shadow-xl group-hover/card:bg-blue-600 group-hover/card:text-white transition-all">
                             <svg className="w-10 h-10" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z"/></svg>
                          </div>
                        </div>
                        <div className="space-y-6">
                          <button className="w-full flex items-center justify-between p-6 bg-slate-950 rounded-2xl text-base text-slate-500 border border-white/5 hover:border-blue-500/50 hover:bg-slate-900 transition-all group/btn">
                            <div className="flex items-center gap-6">
                              <svg className="w-6 h-6 text-blue-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M3 5a2 2 0 012-2h3.28a1 1 0 01.948.684l1.498 4.493a1 1 0 01-.502 1.21l-2.257 1.13a11.042 11.042 0 005.516 5.516l1.13-2.257a1 1 0 011.21-.502l4.493 1.498a1 1 0 01.684.949V19a2 2 0 01-2 2h-1C9.716 21 3 14.284 3 6V5z"/></svg>
                              <span className="font-mono tracking-widest font-black uppercase text-xs">{contact.phone}</span>
                            </div>
                            <span className="text-[10px] font-black text-blue-500 uppercase opacity-0 group-hover/btn:opacity-100 transition-all tracking-[0.2em]">Initialize Dial</span>
                          </button>
                          <button className="w-full flex items-center justify-between p-6 bg-slate-950 rounded-2xl text-base text-slate-500 border border-white/5 hover:border-emerald-500/50 hover:bg-slate-900 transition-all group/btn">
                            <div className="flex items-center gap-6">
                              <svg className="w-6 h-6 text-emerald-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"/></svg>
                              <span className="font-mono tracking-widest font-black uppercase text-xs">{contact.email}</span>
                            </div>
                            <span className="text-[10px] font-black text-emerald-500 uppercase opacity-0 group-hover/btn:opacity-100 transition-all tracking-[0.2em]">Transmit Comms</span>
                          </button>
                        </div>
                      </div>
                    ))}
                    <div className="bg-slate-950/20 border-4 border-dashed border-white/5 rounded-[4rem] p-12 flex flex-col items-center justify-center text-slate-800 hover:text-slate-400 hover:border-blue-500/30 transition-all cursor-pointer group active:scale-95 shadow-inner">
                       <svg className="w-20 h-20 mb-6 group-hover:scale-110 group-hover:text-blue-500 transition-all duration-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" d="M12 6v6m0 0v6m0-6h6m-6 0H6"/></svg>
                       <p className="text-[14px] font-black uppercase tracking-[0.5em]">Append Intelligence</p>
                    </div>
                  </div>
                </section>
              </div>

              {/* Tactical Assets Column */}
              <div className="xl:col-span-5 space-y-20">
                {/* Visual Environment Map */}
                <section>
                  <div className="flex items-center gap-6 mb-12">
                    <h3 className="text-[16px] font-black text-slate-500 uppercase tracking-[0.6em]">Environment Overview</h3>
                  </div>
                  <div className="h-[600px] rounded-[5rem] overflow-hidden border border-white/10 relative shadow-[0_50px_120px_rgba(0,0,0,0.9)] bg-slate-950 group">
                    <iframe 
                      title="Tactical Asset Location"
                      width="100%" 
                      height="100%" 
                      frameBorder="0" 
                      className="map-dark"
                      src={mapUrl} 
                      allowFullScreen
                    ></iframe>
                    <div className="absolute top-10 left-10 bg-slate-950/90 backdrop-blur-3xl px-10 py-4.5 rounded-[2rem] border border-white/10 text-[12px] font-black text-white uppercase tracking-[0.4em] shadow-2xl flex items-center gap-4">
                      <div className="w-3 h-3 bg-rose-500 rounded-full animate-pulse shadow-[0_0_20px_#f43f5e]"></div>
                      Satellite Matrix Feed
                    </div>
                    <div className="absolute top-10 right-10 p-6 bg-blue-600 rounded-[2.5rem] shadow-[0_20px_50px_rgba(37,99,235,0.6)] opacity-0 group-hover:opacity-100 transition-all cursor-pointer hover:scale-110 active:scale-90">
                       <svg className="w-8 h-8 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="3" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"/></svg>
                    </div>
                  </div>
                </section>

                <div className="bg-slate-950 p-16 rounded-[5rem] border border-white/5 shadow-2xl">
                  <p className="text-[14px] font-black text-slate-700 uppercase tracking-[0.6em] mb-14 text-center">Quant Risk Profile</p>
                  <div className="grid grid-cols-2 gap-20 mb-14">
                    <div className="text-center">
                      <p className="text-[12px] font-bold text-slate-600 uppercase tracking-widest mb-6">Severity Tier</p>
                      <p className={`text-5xl font-black italic tracking-tighter ${
                        analysis.riskLevel === 'Extreme' ? 'text-rose-500' : 
                        analysis.riskLevel === 'High' ? 'text-orange-500' : 'text-emerald-500'
                      }`}>
                        {analysis.riskLevel.toUpperCase()}
                      </p>
                    </div>
                    <div className="text-center border-l border-white/5">
                      <p className="text-[12px] font-bold text-slate-600 uppercase tracking-widest mb-6">Critical Class</p>
                      <p className="text-5xl font-mono font-black text-rose-600">{lead.criticalViolations}</p>
                    </div>
                  </div>
                  <div className="space-y-8 pt-14 border-t border-white/5">
                     <div className="flex justify-between items-center px-4">
                        <span className="text-[11px] text-slate-600 font-black uppercase tracking-[0.2em]">BBL Verification Status</span>
                        <span className="text-[12px] font-mono text-emerald-500 font-black tracking-widest bg-emerald-500/10 px-4 py-1.5 rounded-full border border-emerald-500/20">VALIDATED</span>
                     </div>
                     <div className="flex justify-between items-center px-4">
                        <span className="text-[11px] text-slate-600 font-black uppercase tracking-[0.2em]">Asset Volatility</span>
                        <span className="text-[12px] font-mono text-rose-500 font-black tracking-widest">+18.4% YoY</span>
                     </div>
                  </div>
                </div>

                <section>
                  <h4 className="text-[16px] font-black text-slate-500 uppercase tracking-[0.6em] mb-12">Violation Telemetry</h4>
                  <div className="space-y-8 max-h-[650px] overflow-y-auto pr-10 custom-scrollbar">
                    {MOCK_VIOLATIONS.map(v => (
                      <div key={v.violationId} className="p-10 bg-slate-950/60 rounded-[3rem] border border-white/5 hover:border-rose-500/40 transition-all group shadow-2xl">
                        <div className="flex justify-between items-start mb-6">
                          <span className="text-[10px] font-black text-rose-500 bg-rose-500/10 px-4 py-2 rounded-[1.25rem] uppercase tracking-[0.3em] border border-rose-500/20 shadow-inner">CLASS {v.class}</span>
                          <span className="text-[12px] font-mono text-slate-700 uppercase font-black tracking-tighter">{v.inspectionDate}</span>
                        </div>
                        <p className="text-[15px] text-slate-400 leading-relaxed font-bold group-hover:text-slate-100 transition-colors uppercase tracking-tight">{v.description}</p>
                      </div>
                    ))}
                  </div>
                </section>
              </div>
            </div>
          ) : null}
        </div>

        {/* Tactical Control Footer */}
        <div className="p-14 border-t border-white/5 bg-slate-950/80 flex justify-end gap-10 backdrop-blur-3xl">
          <button className="px-14 py-7 bg-slate-900 border border-white/10 text-slate-500 font-black rounded-[2.5rem] text-[12px] uppercase tracking-[0.5em] hover:text-white hover:bg-slate-800 hover:border-white/30 transition-all active:scale-95 shadow-xl">
            Export Prospectus
          </button>
          <button className="px-20 py-7 bg-blue-600 text-white font-black rounded-[2.5rem] text-[12px] uppercase tracking-[0.5em] hover:bg-blue-500 transition-all shadow-[0_20px_80px_rgba(37,99,235,0.6)] transform hover:translate-y-[-4px] active:scale-90">
            Initialize Acquisition Protocol
          </button>
        </div>
      </div>
    </div>
  );
};

export default PropertyAnalysis;
