import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import ComplianceBalanceCapture from './ComplianceBalanceCapture';

const auth = { role: 'admin' };
const submit = vi.fn();
vi.mock('../contexts/AuthContext', () => ({ useAuth: () => ({ user: auth }) }));
vi.mock('../services/compliance-balance-api', async original => ({ ...await original<typeof import('../services/compliance-balance-api')>(), submitBalanceEvidence: (...args: unknown[]) => submit(...args) }));
function show() { return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { mutations: { retry: false } } })}><ComplianceBalanceCapture bin="3348179" /></QueryClientProvider>); }
function fill() {
  fireEvent.click(screen.getByRole('button', { name: 'Record portal balance' }));
  fireEvent.change(screen.getByLabelText('Displayed LL152 total in dollars'), { target: { value: '5000' } });
  fireEvent.change(screen.getByLabelText(/Source date label/), { target: { value: 'No date shown' } });
  fireEvent.change(screen.getByLabelText(/When you checked/), { target: { value: '2026-08-28T12:00' } });
  fireEvent.change(screen.getByLabelText(/Evidence note/), { target: { value: 'Read total for this exact BIN on official portal.' } });
}
describe('attributed balance capture', () => {
  beforeEach(() => { vi.clearAllMocks(); auth.role = 'admin'; submit.mockImplementation(async input => ({ evidence: { ...input, id: 'abc', reviewer: 'u1' }, dry_run: true, writes: 0 })); });
  afterEach(cleanup);
  it('hides admin capture from ordinary users', () => { auth.role = 'viewer'; show(); expect(screen.queryByRole('button')).not.toBeInTheDocument(); });
  it('requires preview and a separate explicit capture click', async () => {
    show(); fill();
    expect(screen.queryByRole('button', { name: 'Confirm and save observation' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Preview evidence' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Confirm and save observation' }));
    expect(await screen.findByText(/Balance observation saved/)).toBeInTheDocument();
    expect(submit).toHaveBeenCalledTimes(2);
    expect(submit.mock.calls[0]).toHaveLength(1);
    expect(submit.mock.calls[1][1]).toBe(true);
    expect(submit.mock.calls[1][0]).toEqual(submit.mock.calls[0][0]);
  });
  it('invalidates a reviewed preview when evidence changes', async () => {
    show(); fill(); fireEvent.click(screen.getByRole('button', { name: 'Preview evidence' }));
    await screen.findByRole('button', { name: 'Confirm and save observation' });
    fireEvent.change(screen.getByLabelText('Displayed LL152 total in dollars'), { target: { value: '0' } });
    expect(screen.queryByRole('button', { name: 'Confirm and save observation' })).not.toBeInTheDocument();
    expect(submit).toHaveBeenCalledOnce();
  });
});
