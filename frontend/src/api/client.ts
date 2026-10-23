import {
  AccountingSummaryResponse,
  AccountingTransaction,
  ApproveMappingRequest,
  AuthState,
  AuthUserResponse,
  BankrollAccount,
  CalculatedOpportunity,
  CreateAccountRequest,
  CreateLeagueRequest,
  DashboardSummary,
  EventListItem,
  FilterScope,
  LeagueRecord,
  LeagueSummary,
  LinkMappingRequest,
  MappingItem,
  RegionSummary,
  RenameLeagueRequest,
  RecordTransactionRequest,
  SettingsResponse,
  SourceHealth,
  SourceName,
  Sport,
  SystemSettings,
  SystemSettingsUpdate,
  TaxonomyFilters,
  TelegramNotificationConfig,
  TelegramNotificationUpdate,
  TimeScope,
  UnmatchedItem,
  UpdateAccountRequest,
  UserPreferences,
  UserPreferencesUpdate,
} from "../types/api";
import { appendMany, appendTaxonomyFilters } from "../features/taxonomy/filterState";

let currentCsrfToken: string | null = null;

export function setCsrfToken(token: string | null): void {
  currentCsrfToken = token;
}

export function getCsrfToken(): string | null {
  return currentCsrfToken;
}

// Registered by the App shell so any real HTTP 401 (an expired/invalidated
// session on an otherwise-authenticated request) can drive one centralized
// reset -- clearing the query cache, the CSRF token, and any personal
// transient UI state -- instead of every call site handling it separately.
// A normal `/auth/me` 200-with-authenticated:false response never reaches
// this: it is not a 401, so it cannot recursively re-trigger the reset.
type UnauthorizedHandler = () => void;
let unauthorizedHandler: UnauthorizedHandler | null = null;

export function setUnauthorizedHandler(handler: UnauthorizedHandler | null): void {
  unauthorizedHandler = handler;
}

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly activeUrl?: string
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  headers.set("Accept", "application/json");

  if (options.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  if (currentCsrfToken && !headers.has("X-CSRF-Token")) {
    headers.set("X-CSRF-Token", currentCsrfToken);
  }

  const response = await fetch(path, {
    ...options,
    headers,
    credentials: "same-origin",
  });

  if (!response.ok) {
    let errorDetail = response.statusText;
    let activeUrl: string | undefined;
    try {
      const json = await response.json();
      if (typeof json.detail === "string") {
        errorDetail = json.detail;
      } else if (json.detail && typeof json.detail === "object") {
        if (typeof json.detail.message === "string") {
          errorDetail = json.detail.message;
        }
        if (typeof json.detail.active_url === "string") {
          activeUrl = json.detail.active_url;
        }
      }
    } catch {
      // ignore
    }
    if (response.status === 401) {
      setCsrfToken(null);
      unauthorizedHandler?.();
    }
    throw new ApiError(errorDetail, response.status, activeUrl);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return response.json();
}

export const api = {
  // Auth
  getAuthState: async (): Promise<AuthState> => {
    const res = await request<AuthState>("/api/v1/auth/me");
    // Unconditional: when unauthenticated, res.csrf_token is already null, so
    // this also satisfies "clear the CSRF token whenever /auth/me reports
    // unauthenticated" without a separate branch.
    setCsrfToken(res.csrf_token);
    return res;
  },

  login: async (username: string, password: string): Promise<AuthUserResponse> => {
    const res = await request<AuthUserResponse>("/api/v1/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    });
    setCsrfToken(res.csrf_token);
    return res;
  },

  register: async (
    username: string,
    password: string,
    registrationCode: string
  ): Promise<AuthUserResponse> => {
    const res = await request<AuthUserResponse>("/api/v1/auth/register", {
      method: "POST",
      body: JSON.stringify({ username, password, registration_code: registrationCode }),
    });
    setCsrfToken(res.csrf_token);
    return res;
  },

  logout: async (): Promise<{ status: string }> => {
    const res = await request<{ status: string }>("/api/v1/auth/logout", {
      method: "POST",
    });
    setCsrfToken(null);
    return res;
  },

  // Dashboard
  getDashboard: (): Promise<DashboardSummary> => request<DashboardSummary>("/api/v1/dashboard"),

  // Opportunities
  getOpportunities: (params?: {
    budget_cents?: number;
    include_source_a_match_sides?: boolean;
    positive_only?: boolean;
    country_ids?: readonly string[];
    league_ids?: readonly string[];
    use_saved_filters?: boolean;
    sport?: Sport;
  }): Promise<CalculatedOpportunity[]> => {
    const search = new URLSearchParams();
    if (params?.budget_cents !== undefined) search.set("budget_cents", params.budget_cents.toString());
    if (params?.include_source_a_match_sides !== undefined)
      search.set("include_source_a_match_sides", params.include_source_a_match_sides.toString());
    if (params?.positive_only !== undefined) search.set("positive_only", params.positive_only.toString());
    if (params?.sport !== undefined) search.set("sport", params.sport);
    appendTaxonomyFilters(search, {
      country_ids: params?.country_ids,
      league_ids: params?.league_ids,
      use_saved_filters: params?.use_saved_filters,
    });
    const query = search.toString();
    return request<CalculatedOpportunity[]>(`/api/v1/opportunities${query ? `?${query}` : ""}`);
  },

  getOpportunityDetail: (
    id: string,
    params?: {
      budget_cents?: number;
      include_source_a_match_sides?: boolean;
    }
  ): Promise<CalculatedOpportunity> => {
    const search = new URLSearchParams();
    if (params?.budget_cents !== undefined) search.set("budget_cents", params.budget_cents.toString());
    if (params?.include_source_a_match_sides !== undefined)
      search.set("include_source_a_match_sides", params.include_source_a_match_sides.toString());
    const query = search.toString();
    return request<CalculatedOpportunity>(`/api/v1/opportunities/${id}${query ? `?${query}` : ""}`);
  },

  getEvents: (params?: {
    search?: string;
    time_scope?: TimeScope;
    country_ids?: readonly string[];
    league_ids?: readonly string[];
    use_saved_filters?: boolean;
    sport?: "football" | "basketball" | "all";
  }): Promise<EventListItem[]> => {
    const resolved = params ?? {};
    const search = new URLSearchParams();
    if (resolved.search) search.set("search", resolved.search);
    if (resolved.time_scope) search.set("time_scope", resolved.time_scope);
    if (resolved.sport && resolved.sport !== "all") search.set("sport", resolved.sport);
    appendTaxonomyFilters(search, {
      country_ids: resolved.country_ids,
      league_ids: resolved.league_ids,
      use_saved_filters: resolved.use_saved_filters,
    });
    const query = search.toString();
    return request<EventListItem[]>(`/api/v1/events${query ? `?${query}` : ""}`);
  },

  // Taxonomy: countries and leagues
  getCountries: (params?: { full_catalog?: boolean }): Promise<RegionSummary[]> => {
    const search = new URLSearchParams();
    if (params?.full_catalog !== undefined)
      search.set("full_catalog", params.full_catalog.toString());
    const query = search.toString();
    return request<RegionSummary[]>(`/api/v1/countries${query ? `?${query}` : ""}`);
  },

  getLeagues: (params?: {
    country_ids?: readonly string[];
    limit?: number;
    offset?: number;
    full_catalog?: boolean;
  }): Promise<LeagueSummary[]> => {
    const search = new URLSearchParams();
    appendMany(search, "country_id", params?.country_ids);
    if (params?.limit !== undefined) search.set("limit", params.limit.toString());
    if (params?.offset !== undefined) search.set("offset", params.offset.toString());
    if (params?.full_catalog !== undefined)
      search.set("full_catalog", params.full_catalog.toString());
    const query = search.toString();
    return request<LeagueSummary[]>(`/api/v1/leagues${query ? `?${query}` : ""}`);
  },

  // Mapping administration
  getUnmatchedMappings: (params?: { limit?: number; offset?: number }): Promise<UnmatchedItem[]> => {
    const search = new URLSearchParams();
    if (params?.limit !== undefined) search.set("limit", params.limit.toString());
    if (params?.offset !== undefined) search.set("offset", params.offset.toString());
    const query = search.toString();
    return request<UnmatchedItem[]>(`/api/v1/mappings/unmatched${query ? `?${query}` : ""}`);
  },

  getMappedMappings: (params?: { limit?: number; offset?: number }): Promise<UnmatchedItem[]> => {
    const search = new URLSearchParams();
    if (params?.limit !== undefined) search.set("limit", params.limit.toString());
    if (params?.offset !== undefined) search.set("offset", params.offset.toString());
    const query = search.toString();
    return request<UnmatchedItem[]>(`/api/v1/mappings/mapped${query ? `?${query}` : ""}`);
  },

  approveMappingSuggestion: (
    source: SourceName,
    competitionId: string,
    payload: ApproveMappingRequest
  ): Promise<MappingItem> =>
    request<MappingItem>(`/api/v1/mappings/${source}/${encodeURIComponent(competitionId)}/approve`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  linkMapping: (
    source: SourceName,
    competitionId: string,
    payload: LinkMappingRequest
  ): Promise<MappingItem> =>
    request<MappingItem>(`/api/v1/mappings/${source}/${encodeURIComponent(competitionId)}/link`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),

  createAndLinkLeague: (
    source: SourceName,
    competitionId: string,
    payload: CreateLeagueRequest
  ): Promise<MappingItem> =>
    request<MappingItem>(`/api/v1/mappings/${source}/${encodeURIComponent(competitionId)}/league`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  renameLeague: (leagueId: string, payload: RenameLeagueRequest): Promise<LeagueRecord> =>
    request<LeagueRecord>(`/api/v1/leagues/${encodeURIComponent(leagueId)}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),

  undoMapping: (
    source: SourceName,
    competitionId: string,
    expectedUpdatedAt: string
  ): Promise<void> => {
    const search = new URLSearchParams({ expected_updated_at: expectedUpdatedAt });
    return request<void>(
      `/api/v1/mappings/${source}/${encodeURIComponent(competitionId)}?${search.toString()}`,
      { method: "DELETE" }
    );
  },

  // Sources
  getSourcesHealth: (): Promise<Record<string, SourceHealth>> =>
    request<Record<string, SourceHealth>>("/api/v1/sources/health"),

  refreshSources: (): Promise<{ status: string }> =>
    request<{ status: string }>("/api/v1/sources/refresh", { method: "POST" }),

  // Settings
  // `personal` and `system` are two intentionally independent repositories on
  // the backend (see routes/settings.py): there is no combined write, so the
  // client mirrors that with two separate update methods instead of one.
  getSettings: (): Promise<SettingsResponse> => request<SettingsResponse>("/api/v1/settings"),

  updatePersonalSettings: (updates: UserPreferencesUpdate): Promise<UserPreferences> =>
    request<UserPreferences>("/api/v1/settings/personal", {
      method: "PUT",
      body: JSON.stringify(updates),
    }),

  updateSystemSettings: (updates: SystemSettingsUpdate): Promise<SystemSettings> =>
    request<SystemSettings>("/api/v1/settings/system", {
      method: "PUT",
      body: JSON.stringify(updates),
    }),

  // Saved taxonomy filters (per authenticated user, per scope). Ownership is
  // always session-derived server-side -- no userId is sent by the client.
  getSavedFilters: (scope: FilterScope): Promise<TaxonomyFilters> =>
    request<TaxonomyFilters>(`/api/v1/preferences/filters/${scope}`),

  saveFilters: (scope: FilterScope, filters: TaxonomyFilters): Promise<TaxonomyFilters> =>
    request<TaxonomyFilters>(`/api/v1/preferences/filters/${scope}`, {
      method: "PUT",
      body: JSON.stringify(filters),
    }),

  // Telegram notifications
  getTelegramNotificationConfig: (): Promise<TelegramNotificationConfig> =>
    request<TelegramNotificationConfig>("/api/v1/notifications/telegram"),

  updateTelegramNotificationConfig: (
    updates: TelegramNotificationUpdate
  ): Promise<TelegramNotificationConfig> =>
    request<TelegramNotificationConfig>("/api/v1/notifications/telegram", {
      method: "PUT",
      body: JSON.stringify(updates),
    }),

  testTelegramConnection: (): Promise<TelegramNotificationConfig> =>
    request<TelegramNotificationConfig>("/api/v1/notifications/telegram/test", {
      method: "POST",
    }),

  // Accounting & Bankroll Accounts
  getAccountingAccounts: (includeInactive = false): Promise<BankrollAccount[]> =>
    request<BankrollAccount[]>(
      `/api/v1/accounting/accounts${includeInactive ? "?include_inactive=true" : ""}`
    ),

  createAccountingAccount: (data: CreateAccountRequest): Promise<BankrollAccount> =>
    request<BankrollAccount>("/api/v1/accounting/accounts", {
      method: "POST",
      body: JSON.stringify(data),
    }),

  updateAccountingAccount: (
    accountId: number,
    data: UpdateAccountRequest
  ): Promise<BankrollAccount> =>
    request<BankrollAccount>(`/api/v1/accounting/accounts/${accountId}`, {
      method: "PUT",
      body: JSON.stringify(data),
    }),

  deleteAccountingAccount: (accountId: number): Promise<{ ok: boolean }> =>
    request<{ ok: boolean }>(`/api/v1/accounting/accounts/${accountId}`, {
      method: "DELETE",
    }),

  getAccountingSummary: (
    date?: string,
    accountId?: number
  ): Promise<AccountingSummaryResponse> => {
    const params = new URLSearchParams();
    if (date) params.set("date", date);
    if (accountId !== undefined) params.set("account_id", String(accountId));
    const qs = params.toString();
    return request<AccountingSummaryResponse>(`/api/v1/accounting/summary${qs ? `?${qs}` : ""}`);
  },

  getAccountingTransactions: (
    limit = 50,
    accountId?: number
  ): Promise<AccountingTransaction[]> => {
    const params = new URLSearchParams();
    params.set("limit", String(limit));
    if (accountId !== undefined) params.set("account_id", String(accountId));
    return request<AccountingTransaction[]>(
      `/api/v1/accounting/transactions?${params.toString()}`
    );
  },

  recordAccountingTransaction: (
    data: RecordTransactionRequest
  ): Promise<AccountingTransaction> =>
    request<AccountingTransaction>("/api/v1/accounting/transactions", {
      method: "POST",
      body: JSON.stringify(data),
    }),

  cancelAccountingTransaction: (txId: number): Promise<AccountingTransaction> =>
    request<AccountingTransaction>(`/api/v1/accounting/transactions/${txId}/cancel`, {
      method: "POST",
    }),
};
