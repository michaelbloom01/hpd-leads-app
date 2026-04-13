import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { MemoryRouter, useSearchParams } from 'react-router-dom';

import { useLeadFilters } from './useLeadFilters';
import { useFilterUrl } from './useFilterUrl';

function Harness() {
  const { filters, replaceAll } = useLeadFilters();
  useFilterUrl(filters, replaceAll);
  const [, setSearchParams] = useSearchParams();

  return (
    <div>
      <div data-testid="search-term">{filters.searchTerm}</div>
      <div data-testid="boroughs">{filters.boroughs.join(',')}</div>
      <div data-testid="search-mode">{filters.searchMode}</div>
      <button
        type="button"
        onClick={() => setSearchParams(new URLSearchParams('q=second&boro=MANHATTAN,BRONX&mode=address'))}
      >
        Change URL
      </button>
    </div>
  );
}

describe('useFilterUrl', () => {
  it('rehydrates filter state when the URL changes after mount', async () => {
    render(
      <MemoryRouter initialEntries={['/leads?q=first']}>
        <Harness />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByTestId('search-term')).toHaveTextContent('first');
    });

    fireEvent.click(screen.getByRole('button', { name: 'Change URL' }));

    await waitFor(() => {
      expect(screen.getByTestId('search-term')).toHaveTextContent('second');
      expect(screen.getByTestId('boroughs')).toHaveTextContent('MANHATTAN,BRONX');
      expect(screen.getByTestId('search-mode')).toHaveTextContent('address');
    });
  });
});
