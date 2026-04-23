import type { ProvidersResponse } from '@/types';
import { fetchJson } from './client';

export const providersApi = {
  async getProviders(): Promise<ProvidersResponse> {
    return fetchJson<ProvidersResponse>('/providers');
  },
};
