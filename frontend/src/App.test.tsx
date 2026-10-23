import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { getCsrfToken, setCsrfToken } from "./api/client";
import {
  AuthState,
  DashboardSummary,
  SettingsResponse,
  SourceName,
  TelegramNotificationConfig,
} from "./types/api";

// These tests exercise the *real* api/client.ts request() helper (not a
// mocked api.* method) for the auth/me and logout calls, so that a logout
// request which itself returns 401 goes through the same production 401
// branch (setCsrfToken(null) + unauthorizedHandler()) that a real session
// expiry would. Every other endpoint the shell touches on mount is stubbed
// through the same fetch mock, routed by URL.

const authenticatedState: AuthState = {
  authenticated: true,
  registration_enabled: true,
  user_id: 1,
  username: "alice",
  csrf_token: "csrf-abc",
};

const dashboard: DashboardSummary = {
  total_opportunities_count: 0,
  positive_opportunities_count: 0,
  best_margin_percent: 0,
  sources_health: {},
  overall_status: "live",
  last_refresh_at: null,
};

const settings: SettingsResponse = {
  personal: {
    default_budget_cents: 100000,
    include_source_a_match_sides: false,
    tracked_competitions_only: false,
    updated_at: "2026-08-31T00:00:00Z",
  },
  system: {
    refresh_interval_seconds: 30,
    staleness_limit_seconds: 75,
    updated_at: "2026-08-31T00:00:00Z",
  },
};

const telegramConfig: TelegramNotificationConfig = {
  enabled: false,
  chat_id: null,
  minimum_profit_percent: "0.95",
  decrease_threshold_percentage_points: "0.20",
  increase_threshold_percentage_points: "0.20",
  alert_cooldown_seconds: 300,
  notify_1x2: true,
  notify_ou25: true,
  token_configured: false,
  chat_validated: false,
  validated_at: null,
  last_successful_delivery_at: null,
  last_error: null,
};

function requestUrl(input: RequestInfo | URL): string {
  if (typeof input === "string") return input;
  if (input instanceof URL) return input.toString();
  return input.url;
}

// Routes every fetch call the app shell can make while authenticated and
// navigating to Settings. `logoutResponse` supplies the response for the
// one endpoint each test cares about varying.
//
// `queryClient.clear()` (called inside resetToUnauthenticated) empties the
// cache but leaves the `["auth"]` useQuery observer mounted, so React Query
// immediately fires a second, unrelated `/auth/me` refetch. That refetch's
// own `setCsrfToken(res.csrf_token)` side effect would otherwise race with
// -- and mask whether -- the reset logic under test is what actually
// cleared the token / deduplicated the reset. To keep these tests a clean
// signal for that logic specifically, any `/auth/me` call beyond the
// initial mount is left permanently pending so it cannot resolve during
// the test.
function stubFetch(logoutResponse: () => Response) {
  let authMeCalls = 0;
  const pendingForever = new Promise<Response>(() => {});
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const url = requestUrl(input);
    if (url.includes("/auth/logout")) {
      return logoutResponse();
    }
    if (url.includes("/auth/me")) {
      authMeCalls += 1;
      if (authMeCalls > 1) {
        return pendingForever;
      }
      return new Response(JSON.stringify(authenticatedState), { status: 200 });
    }
    if (url.includes("/dashboard")) {
      return new Response(JSON.stringify(dashboard), { status: 200 });
    }
    if (url.includes("/settings")) {
      return new Response(JSON.stringify(settings), { status: 200 });
    }
    if (url.includes("/preferences/filters")) {
      return new Response(JSON.stringify({ country_ids: [], league_ids: [] }), { status: 200 });
    }
    if (url.includes("/opportunities")) {
      return new Response(JSON.stringify([]), { status: 200 });
    }
    if (url.includes("/events")) {
      return new Response(JSON.stringify([]), { status: 200 });
    }
    if (url.includes("/notifications/telegram")) {
      return new Response(JSON.stringify(telegramConfig), { status: 200 });
    }
    return new Response("{}", { status: 200 });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function renderApp() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const clearSpy = vi.spyOn(client, "clear");
  const utils = render(
    <QueryClientProvider client={client}>
      <App />
    </QueryClientProvider>
  );
  return { ...utils, client, clearSpy };
}

async function goToSettingsAndLogout() {
  fireEvent.click(await screen.findByLabelText(/Ayarlar/i));
  fireEvent.click(await screen.findByRole("button", { name: /Oturumu Kapat/i }));
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  setCsrfToken(null);
});

describe("App logout reset", () => {
  it("clears the real in-memory CSRF token when the logout request fails with a non-401 error", async () => {
    stubFetch(() => new Response(JSON.stringify({ detail: "boom" }), { status: 500 }));
    const { clearSpy } = renderApp();

    // Loaded via the real getAuthState() call above, so this reflects a
    // genuinely-set token, not a value the test poked in directly.
    await waitFor(() => expect(getCsrfToken()).toBe("csrf-abc"));

    await goToSettingsAndLogout();

    await waitFor(() => expect(clearSpy).toHaveBeenCalled());
    expect(getCsrfToken()).toBeNull();
  });

  it("performs exactly one reset when the logout request itself returns 401", async () => {
    stubFetch(() => new Response(JSON.stringify({ detail: "Unauthorized" }), { status: 401 }));
    const { clearSpy } = renderApp();

    await waitFor(() => expect(getCsrfToken()).toBe("csrf-abc"));

    await goToSettingsAndLogout();

    // Wait for the login screen to reappear, proving the reset(s) completed.
    await screen.findByRole("button", { name: /Giriş Yap/i });

    expect(clearSpy).toHaveBeenCalledTimes(1);
    expect(getCsrfToken()).toBeNull();
  });

  it("performs exactly one reset on an ordinary successful logout", async () => {
    stubFetch(() => new Response(JSON.stringify({ status: "ok" }), { status: 200 }));
    const { clearSpy } = renderApp();

    await waitFor(() => expect(getCsrfToken()).toBe("csrf-abc"));

    await goToSettingsAndLogout();

    await screen.findByRole("button", { name: /Giriş Yap/i });

    expect(clearSpy).toHaveBeenCalledTimes(1);
    expect(getCsrfToken()).toBeNull();
  });
});
