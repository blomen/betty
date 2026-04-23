/**
 * API client — barrel re-export.
 *
 * Domain-specific methods are in api/ subdirectory.
 * This file preserves the existing import path: import { api } from '@/services/api'
 */
export { api, ApiError, NetworkError, TimeoutError, getMlHealth } from './api/index';
export type { SpecialItem, SpecialsFilters, LlmHealth, SpecialsResponse, StakePreviewResult, ExtractionProvider, ExtractionPlatform, ExtractionSettingsResponse } from './api/index';
