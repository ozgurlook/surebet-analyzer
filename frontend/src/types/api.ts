export type SourceName =
  | "source_a"
  | "source_c"
  | "source_b"
  | "source_d"
  | "source_e"
  | "source_f";
export type OutcomeName = "HOME" | "DRAW" | "AWAY";
export type TwoWayOutcomeName = "OVER" | "UNDER";
export type AnyOutcomeName = OutcomeName | TwoWayOutcomeName | string;
export type MarketType = "1X2" | "OU25" | "BASKETBALL_ML" | "BASKETBALL_AH";
export type Sport = "football" | "basketball";

// Taxonomy (country/league) enums - values mirror the backend StrEnum members exactly.
export type TaxonomyStatus = "verified" | "unmapped";
export type RegionClass = "country" | "international" | "unknown";
export type CompetitionKind =
  | "league"
  | "cup"
  | "qualifier"
  | "friendly"
  | "international_club"
  | "international_national"
  | "unknown";
export type Gender = "men" | "women";
export type MappingMethod = "manual" | "verified_source_id" | "exact_alias" | "suggestion";
export type ReviewState = "approved" | "pending" | "conflict";
export type SourceIdentityKind = "provider_id" | "derived_text";

export interface StrategySelection {
  outcome: AnyOutcomeName;
  source: SourceName;
  odds: string | number;
  stake_cents: number;
  cashback_eligible: boolean;
  event_id?: string | null;
}

export interface ScenarioBreakdown {
  outcome: AnyOutcomeName;
  winning_source: SourceName;
  winning_odds: string | number;
  winning_stake_cents: number;
  gross_payout_cents: number;
  losing_eligible_stakes_cents: number;
  cashback_cents: number;
  total_return_cents: number;
  total_stake_cents: number;
  standard_profit_cents: number;
  net_profit_cents: number;
  net_profit_percent: number;
}

export interface CalculatedOpportunity {
  opportunity_id: string;
  matched_id: string;
  competition: string;
  country_id: string | null;
  country_name: string | null;
  country_iso_code: string | null;
  league_id: string | null;
  league_name: string | null;
  home: string;
  away: string;
  kickoff: string;
  budget_cents: number;
  market_type?: MarketType;
  market_label?: string;
  sport?: Sport;
  include_source_a_match_sides: boolean;
  selections: Record<string, StrategySelection>;
  scenarios: Record<string, ScenarioBreakdown>;
  min_adjusted_profit_cents: number;
  min_adjusted_profit_percent: number;
  min_standard_profit_cents: number;
  min_standard_profit_percent: number;
  is_positive: boolean;
  oldest_quote_age_seconds: number;
  calculated_at: string;
  alternatives?: CalculatedOpportunity[];
}

export interface SourceHealth {
  source: SourceName;
  status: "healthy" | "degraded" | "unavailable" | "disabled";
  started_at: string;
  finished_at: string;
  duration_ms: number;
  event_count: number;
  error_message: string | null;
  last_success_at: string | null;
  updated_at?: string;
}

export interface DashboardSummary {
  total_opportunities_count: number;
  positive_opportunities_count: number;
  best_margin_percent: number;
  sources_health: Record<string, SourceHealth>;
  overall_status: "live" | "degraded" | "stale" | "unavailable";
  last_refresh_at: string | null;
}

export interface EventListItem {
  matched_id: string;
  taxonomy_status: TaxonomyStatus;
  competition: string;
  country_id: string | null;
  country_name: string | null;
  country_iso_code: string | null;
  league_id: string | null;
  league_name: string | null;
  home: string;
  away: string;
  kickoff: string;
  sources: string[];
  best_margin_percent: number | null;
  is_positive: boolean;
  sport?: "football" | "basketball";
  quotes: Record<string, string | number>;
}

export type TimeScope = "today" | "tomorrow" | "all";

// Country and league catalog (GET /api/v1/countries, GET /api/v1/leagues).
export interface RegionSummary {
  country_id: string;
  region_class: RegionClass;
  iso_code: string | null;
  display_name: string;
  league_count: number;
  event_count: number;
  opportunity_count: number;
  freshest_at: string | null;
}

export interface LeagueSummary {
  league_id: string;
  country_id: string;
  display_name: string;
  kind: CompetitionKind;
  gender: string | null;
  age_group: string | null;
  is_reserve: boolean | null;
  active: boolean;
  event_count: number;
  opportunity_count: number;
  freshest_at: string | null;
}

// Client-side taxonomy filter selection, persisted per scope (Matches vs Opportunities).
export interface TaxonomyFilters {
  country_ids: string[];
  league_ids: string[];
}

// Mapping administration (GET/POST/PUT/PATCH/DELETE /api/v1/mappings/*, PATCH /api/v1/leagues/{id}).
export interface MappingTarget {
  league_id: string;
  region_id: string;
  display_name: string;
  kind: CompetitionKind;
  gender: string | null;
  age_group: string | null;
  is_reserve: boolean | null;
  active: boolean;
  updated_at: string;
}

export interface UnmatchedItem {
  source: SourceName;
  competition_id: string;
  identity_kind: SourceIdentityKind;
  competition_name: string;
  country_id: string | null;
  country_name: string | null;
  country_alias: string | null;
  season: string | null;
  kind: CompetitionKind;
  gender: string | null;
  age_group: string | null;
  is_reserve: boolean | null;
  first_seen_at: string;
  last_seen_at: string;
  updated_at: string;
  method: MappingMethod | null;
  confidence: number | null;
  review_state: ReviewState | null;
  target: MappingTarget | null;
  conflict_reasons: string[];
}

export interface MappingItem {
  source: SourceName;
  competition_id: string;
  league_id: string;
  method: MappingMethod;
  confidence: number;
  review_state: ReviewState;
  note: string | null;
  created_at: string;
  updated_at: string;
}

export interface LeagueRecord {
  league_id: string;
  region_id: string;
  display_name: string;
  kind: CompetitionKind;
  gender: string | null;
  age_group: string | null;
  is_reserve: boolean | null;
  active: boolean;
  updated_at: string;
}

export interface ApproveMappingRequest {
  expected_updated_at: string;
  note?: string | null;
}

export interface LinkMappingRequest {
  expected_updated_at: string;
  league_id: string;
  note?: string | null;
}

export interface CreateLeagueRequest {
  expected_updated_at: string;
  region_id: string;
  display_name: string;
  kind: CompetitionKind;
  gender?: Gender | null;
  age_group?: string | null;
  is_reserve?: boolean | null;
  note?: string | null;
}

export interface RenameLeagueRequest {
  expected_updated_at: string;
  display_name: string;
}

// Settings (GET /api/v1/settings, PUT /api/v1/settings/personal, PUT /api/v1/settings/system).
// Two intentionally independent sections: `personal` is scoped to the calling
// user (session-derived, never client-supplied), `system` is a shared
// singleton visible to and writable by every authenticated user.
export interface UserPreferences {
  default_budget_cents: number;
  include_source_a_match_sides: boolean;
  tracked_competitions_only: boolean;
  updated_at: string;
}

export type UserPreferencesUpdate = Partial<
  Pick<
    UserPreferences,
    "default_budget_cents" | "include_source_a_match_sides" | "tracked_competitions_only"
  >
>;

export interface SystemSettings {
  refresh_interval_seconds: number;
  staleness_limit_seconds: number;
  updated_at: string;
}

export type SystemSettingsUpdate = Partial<
  Pick<SystemSettings, "refresh_interval_seconds" | "staleness_limit_seconds">
>;

export interface SettingsResponse {
  personal: UserPreferences;
  system: SystemSettings;
}

// Saved per-user, per-scope taxonomy filters
// (GET/PUT /api/v1/preferences/filters/{scope}). Mirrors the backend's
// `FilterScope` StrEnum members exactly.
export type FilterScope = "events" | "opportunities";

export interface TelegramNotificationConfig {
  enabled: boolean;
  chat_id: string | null;
  minimum_profit_percent: string;
  decrease_threshold_percentage_points: string;
  increase_threshold_percentage_points: string;
  alert_cooldown_seconds: number;
  notify_1x2: boolean;
  notify_ou25: boolean;
  token_configured: boolean;
  chat_validated: boolean;
  validated_at: string | null;
  last_successful_delivery_at: string | null;
  last_error: string | null;
}

export type TelegramNotificationUpdate = Partial<
  Pick<
    TelegramNotificationConfig,
    | "enabled"
    | "chat_id"
    | "minimum_profit_percent"
    | "decrease_threshold_percentage_points"
    | "increase_threshold_percentage_points"
    | "alert_cooldown_seconds"
    | "notify_1x2"
    | "notify_ou25"
  >
>;

export interface AuthState {
  authenticated: boolean;
  registration_enabled: boolean;
  user_id: number | null;
  username: string | null;
  csrf_token: string | null;
}

// Returned by a successful POST /api/v1/auth/login or /api/v1/auth/register.
export interface AuthUserResponse {
  user_id: number;
  username: string;
  csrf_token: string;
}

// Accounting & Bankroll Models
export interface BankrollAccount {
  id: number;
  user_id: number;
  account_name: string;
  initial_balance_cents: number;
  current_balance_cents: number;
  total_deposit_cents: number;
  total_withdrawal_cents: number;
  net_profit_cents: number;
  note: string | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface AccountBreakdown {
  account_id: number;
  account_name: string;
  deposit_cents: number;
  withdrawal_cents: number;
  net_profit_cents: number;
}

export interface AccountingTransaction {
  id: number;
  chat_id: number;
  tx_type: "DEPOSIT" | "WITHDRAWAL";
  amount_cents: number;
  tx_date: string;
  note: string | null;
  status: "ACTIVE" | "CANCELLED";
  created_at: string;
  cancelled_at: string | null;
  account_id: number | null;
  user_id: number | null;
}

export interface DayBreakdown {
  date_str: string;
  day_name: string;
  deposit_cents: number;
  withdrawal_cents: number;
  net_profit_cents: number;
  has_transactions: boolean;
}

export interface DailySummary {
  date_str: string;
  total_deposit_cents: number;
  total_withdrawal_cents: number;
  net_profit_cents: number;
  transaction_count: number;
  transactions: AccountingTransaction[];
  account_breakdowns: AccountBreakdown[];
}

export interface WeeklySummary {
  week_start_str: string;
  week_end_str: string;
  total_deposit_cents: number;
  total_withdrawal_cents: number;
  net_profit_cents: number;
  daily_breakdown: DayBreakdown[];
  account_breakdowns: AccountBreakdown[];
}

export interface AccountingSummaryResponse {
  daily: DailySummary;
  weekly: WeeklySummary;
}

export interface CreateAccountRequest {
  account_name: string;
  initial_balance_cents?: number;
  note?: string | null;
}

export interface UpdateAccountRequest {
  account_name?: string;
  initial_balance_cents?: number;
  note?: string | null;
  is_active?: boolean;
}

export interface RecordTransactionRequest {
  tx_type: "DEPOSIT" | "WITHDRAWAL";
  amount_cents: number;
  tx_date?: string | null;
  note?: string | null;
  account_id?: number | null;
}
