
import { Borough, BuildingLead, Violation } from './types';

export const COLORS = {
  primary: '#020617',
  secondary: '#0f172a',
  accent: '#3b82f6',
  danger: '#f43f5e',
  warning: '#f59e0b',
  success: '#10b981',
};

export const MOCK_LEADS: BuildingLead[] = [
  {
    buildingId: "102345",
    bbl: "1002010045",
    address: "123 Mott St",
    borough: Borough.MANHATTAN,
    zip: "10013",
    totalViolations: 45,
    criticalViolations: 12,
    lastInspection: "2024-11-15",
    score: 88,
    ownerName: "Mott Street Realty LLC",
    contacts: [
      { name: "Abraham Goldberg", role: "Principal", phone: "212-555-0192", email: "abraham@mottrealty.com", mailingAddress: "450 7th Ave, Ste 1200, NY 10123" }
    ]
  },
  {
    buildingId: "204456",
    bbl: "3012450012",
    address: "567 Ocean Ave",
    borough: Borough.BROOKLYN,
    zip: "11226",
    totalViolations: 12,
    criticalViolations: 2,
    lastInspection: "2024-12-01",
    score: 42,
    ownerName: "Ocean Parkway Holdings",
    contacts: [
      { name: "Sarah Jenkins", role: "Managing Member", phone: "718-555-9988", email: "jenkins@oceanpk.net", mailingAddress: "1200 Brighton Beach Ave, Brooklyn NY" }
    ]
  },
  {
    buildingId: "305567",
    bbl: "4055670089",
    address: "89-12 164th St",
    borough: Borough.QUEENS,
    zip: "11432",
    totalViolations: 31,
    criticalViolations: 8,
    lastInspection: "2024-10-20",
    score: 75,
    ownerName: "Queens Boulevard Trust",
    contacts: [
      { name: "Robert Chen", role: "Trustee", phone: "917-555-4321", email: "rchen@trustq.org", mailingAddress: "164-02 89th Ave, Queens NY" }
    ]
  },
  {
    buildingId: "401122",
    bbl: "2030450001",
    address: "2200 Grand Concourse",
    borough: Borough.BRONX,
    zip: "10457",
    totalViolations: 89,
    criticalViolations: 24,
    lastInspection: "2024-12-10",
    score: 95,
    ownerName: "Concourse Dev Corp",
    contacts: [
      { name: "Mike Moretti", role: "Manager", phone: "347-555-7721", email: "mike@concoursedev.io", mailingAddress: "PO Box 450, Bronx NY 10451" }
    ]
  }
];

export const MOCK_VIOLATIONS: Violation[] = [
  {
    violationId: "V1001",
    buildingId: "102345",
    borough: Borough.MANHATTAN,
    houseNumber: "123",
    streetName: "MOTT STREET",
    zip: "10013",
    inspectionDate: "2024-11-15",
    class: 'C',
    orderNumber: "654",
    violationStatus: 'Open',
    description: "ADEQUATE SUPPLY OF HEAT NOT PROVIDED TO RESIDENTIAL UNIT 4B."
  },
  {
    violationId: "V1002",
    buildingId: "102345",
    borough: Borough.MANHATTAN,
    houseNumber: "123",
    streetName: "MOTT STREET",
    zip: "10013",
    inspectionDate: "2024-11-16",
    class: 'C',
    orderNumber: "702",
    violationStatus: 'Open',
    description: "LEAD BASED PAINT VIOLATION DETECTED IN PUBLIC HALLWAY."
  }
];
