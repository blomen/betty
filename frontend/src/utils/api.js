/**
 * API Service - connects to OddOpp backend
 */

const API_BASE = 'http://localhost:8000/api';

/**
 * Generic fetch wrapper with error handling
 */
async function fetchAPI(endpoint, options = {}) {
    try {
        const response = await fetch(`${API_BASE}${endpoint}`, {
            headers: {
                'Content-Type': 'application/json',
                ...options.headers,
            },
            ...options,
        });

        if (!response.ok) {
            throw new Error(`API error: ${response.status}`);
        }

        return await response.json();
    } catch (error) {
        console.error(`API call failed: ${endpoint}`, error);
        throw error;
    }
}

/**
 * Bankroll endpoints
 */
export const bankroll = {
    getSummary: () => fetchAPI('/bankroll'),
    getStats: () => fetchAPI('/bankroll/stats'),
};

/**
 * Providers endpoints
 */
export const providers = {
    list: () => fetchAPI('/providers'),
    create: (data) => fetchAPI('/providers', { method: 'POST', body: JSON.stringify(data) }),
    update: (id, data) => fetchAPI(`/providers/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
};

/**
 * Opportunities endpoints
 */
export const opportunities = {
    list: (type = null) => fetchAPI(`/opportunities${type ? `?type=${type}` : ''}`),
    arbitrage: () => fetchAPI('/opportunities?type=arb'),
    value: () => fetchAPI('/opportunities?type=value'),
    bonus: () => fetchAPI('/opportunities?type=bonus'),
};

/**
 * Events endpoints
 */
export const events = {
    list: (sport = null, limit = 50) => fetchAPI(`/events?limit=${limit}${sport ? `&sport=${sport}` : ''}`),
    get: (id) => fetchAPI(`/events/${id}`),
};

/**
 * Bets endpoints
 */
export const bets = {
    list: (status = null, limit = 50) => fetchAPI(`/bets?limit=${limit}${status ? `&status=${status}` : ''}`),
    create: (data) => fetchAPI('/bets', { method: 'POST', body: JSON.stringify(data) }),
    settle: (id, data) => fetchAPI(`/bets/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
};

/**
 * Profile endpoints
 */
export const profile = {
    get: () => fetchAPI('/profile'),
    update: (data) => fetchAPI('/profile', { method: 'PUT', body: JSON.stringify(data) }),
};

/**
 * Stake calculator
 */
export const calculator = {
    stake: (odds, fairOdds) => fetchAPI(`/calculate/stake?odds=${odds}&fair_odds=${fairOdds}`, { method: 'POST' }),
};

/**
 * Extraction endpoints
 */
export const extraction = {
    status: () => fetchAPI('/extraction/status'),
    run: (providers = 'unibet,leovegas,casumo', sport = 'football', maxGroups = 5) =>
        fetchAPI(`/extraction/run?providers=${providers}&sport=${sport}&max_groups=${maxGroups}`, { method: 'POST' }),
};

export default {
    bankroll,
    providers,
    opportunities,
    events,
    bets,
    profile,
    calculator,
    extraction,
};

