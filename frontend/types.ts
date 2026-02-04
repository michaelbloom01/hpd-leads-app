
export enum Borough {
  MANHATTAN = 'MANHATTAN',
  BRONX = 'BRONX',
  BROOKLYN = 'BROOKLYN',
  QUEENS = 'QUEENS',
  STATEN_ISLAND = 'STATEN ISLAND'
}

export interface ContactInfo {
  name: string;
  role: string;
  phone: string;
  email: string;
  mailingAddress: string;
}

export interface BuildingLead {
  buildingId: string;
  bbl: string;
  address: string;
  borough: Borough;
  zip: string;
  totalViolations: number;
  criticalViolations: number;
  lastInspection: string;
  score: number;
  ownerName: string;
  contacts: ContactInfo[];
}

export interface Violation {
  violationId: string;
  buildingId: string;
  borough: Borough;
  houseNumber: string;
  streetName: string;
  zip: string;
  inspectionDate: string;
  class: 'A' | 'B' | 'C';
  orderNumber: string;
  violationStatus: 'Open' | 'Closed';
  description: string;
}

export interface AIAnalysis {
  summary: string;
  riskLevel: 'Low' | 'Medium' | 'High' | 'Extreme';
  investmentThesis: string;
  suggestedAction: string;
}
