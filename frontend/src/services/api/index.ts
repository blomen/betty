// Re-export shared types and utilities
export { ApiError, NetworkError, TimeoutError, getMlHealth } from './client';
export type { SpecialItem, SpecialsFilters, LlmHealth, SpecialsResponse, StakePreviewResult, ExtractionProvider, ExtractionPlatform, ExtractionSettingsResponse } from './client';

// Import domain APIs
import { providersApi } from './providers';
import { bankrollApi } from './bankroll';
import { opportunitiesApi } from './opportunities';
import { betsApi } from './bets';
import { riskApi } from './risk';
import { specialsApi } from './specials';
import { profilesApi } from './profiles';
import { tradingApi } from './trading';
import { settingsApi } from './settings';

// Compose the unified api object (preserves existing api.xxx() usage)
export const api = {
  ...providersApi,
  ...bankrollApi,
  ...opportunitiesApi,
  ...betsApi,
  ...riskApi,
  ...specialsApi,
  ...profilesApi,
  ...tradingApi,
  ...settingsApi,
};
