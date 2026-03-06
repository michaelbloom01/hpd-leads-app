import type { ApiLead } from '../services/api';

const firstNonBlank = (...values: Array<string | null | undefined>): string | null => {
  for (const value of values) {
    const trimmed = String(value ?? '').trim();
    if (trimmed) {
      return trimmed;
    }
  }
  return null;
};

export const getLeadDisplayName = (lead: ApiLead): string =>
  firstNonBlank(
    lead.company_name,
    lead.agent_name,
    lead.owner_name,
    lead.primary_contact,
    lead.address,
    lead.lead_id,
  ) || lead.lead_id;

export const getLeadSecondaryName = (lead: ApiLead): string | null => {
  const displayName = getLeadDisplayName(lead).toLowerCase();
  return firstNonBlank(lead.primary_contact, lead.owner_name, lead.agent_name, lead.company_name)
    ?.trim()
    ?.toLowerCase() === displayName
    ? null
    : firstNonBlank(lead.primary_contact, lead.owner_name, lead.agent_name, lead.company_name);
};
