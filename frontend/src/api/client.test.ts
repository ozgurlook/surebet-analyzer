import { afterEach, describe, expect, it, vi } from "vitest";

import { api, getCsrfToken, setCsrfToken, setUnauthorizedHandler } from "./client";

afterEach(() => {
  setCsrfToken(null);
  setUnauthorizedHandler(null);
  vi.unstubAllGlobals();
});

describe("auth API client", () => {
  it("sends username and password to login and stores the returned CSRF token", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(
        JSON.stringify({ user_id: 1, username: "alice", csrf_token: "csrf-123" }),
        { status: 200 }
      )
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await api.login("alice", "hunter2");

    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/v1/auth/login");
    const body = JSON.parse((fetchMock.mock.calls[0]?.[1]?.body as string) ?? "{}");
    expect(body).toEqual({ username: "alice", password: "hunter2" });
    expect(result).toEqual({ user_id: 1, username: "alice", csrf_token: "csrf-123" });
    expect(getCsrfToken()).toBe("csrf-123");
  });

  it("sends username, password, and registration code to register and stores the CSRF token", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(
        JSON.stringify({ user_id: 2, username: "bob", csrf_token: "csrf-456" }),
        { status: 200 }
      )
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await api.register("bob", "s3cret", "invite-code");

    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/v1/auth/register");
    const body = JSON.parse((fetchMock.mock.calls[0]?.[1]?.body as string) ?? "{}");
    expect(body).toEqual({ username: "bob", password: "s3cret", registration_code: "invite-code" });
    expect(result).toEqual({ user_id: 2, username: "bob", csrf_token: "csrf-456" });
    expect(getCsrfToken()).toBe("csrf-456");
  });

  it("clears the CSRF token when /auth/me reports an unauthenticated session", async () => {
    setCsrfToken("stale-token");
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(
        JSON.stringify({
          authenticated: false,
          registration_enabled: true,
          user_id: null,
          username: null,
          csrf_token: null,
        }),
        { status: 200 }
      )
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await api.getAuthState();

    expect(result.authenticated).toBe(false);
    expect(getCsrfToken()).toBeNull();
  });

  it("keeps the CSRF token from an authenticated /auth/me response", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(
        JSON.stringify({
          authenticated: true,
          registration_enabled: true,
          user_id: 7,
          username: "alice",
          csrf_token: "fresh-token",
        }),
        { status: 200 }
      )
    );
    vi.stubGlobal("fetch", fetchMock);

    await api.getAuthState();

    expect(getCsrfToken()).toBe("fresh-token");
  });
});

describe("centralized 401 handling", () => {
  it("invokes the registered unauthorized handler and clears the CSRF token on a real 401", async () => {
    setCsrfToken("still-valid-looking");
    const handler = vi.fn();
    setUnauthorizedHandler(handler);
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValue(new Response(JSON.stringify({ detail: "Unauthorized" }), { status: 401 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(api.getDashboard()).rejects.toThrow();

    expect(handler).toHaveBeenCalledTimes(1);
    expect(getCsrfToken()).toBeNull();
  });

  it("does not invoke the unauthorized handler for a normal 200 unauthenticated /auth/me response", async () => {
    const handler = vi.fn();
    setUnauthorizedHandler(handler);
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(
        JSON.stringify({
          authenticated: false,
          registration_enabled: true,
          user_id: null,
          username: null,
          csrf_token: null,
        }),
        { status: 200 }
      )
    );
    vi.stubGlobal("fetch", fetchMock);

    await api.getAuthState();

    expect(handler).not.toHaveBeenCalled();
  });

  it("does not invoke the unauthorized handler for a non-401 error", async () => {
    const handler = vi.fn();
    setUnauthorizedHandler(handler);
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValue(new Response(JSON.stringify({ detail: "boom" }), { status: 500 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(api.getDashboard()).rejects.toThrow();

    expect(handler).not.toHaveBeenCalled();
  });
});

describe("settings API client", () => {
  const settingsResponse = {
    personal: {
      default_budget_cents: 100_000,
      include_source_a_match_sides: false,
      tracked_competitions_only: true,
      updated_at: "2026-08-31T00:00:00Z",
    },
    system: {
      refresh_interval_seconds: 30,
      staleness_limit_seconds: 75,
      updated_at: "2026-08-31T00:00:00Z",
    },
  };

  it("fetches the combined personal and system settings from a single GET", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValue(new Response(JSON.stringify(settingsResponse), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await api.getSettings();

    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/v1/settings");
    expect(result).toEqual(settingsResponse);
  });

  it("updates personal settings through a dedicated PUT, independent of system settings", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValue(new Response(JSON.stringify(settingsResponse.personal), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await api.updatePersonalSettings({ default_budget_cents: 250_000 });

    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/v1/settings/personal");
    expect(fetchMock.mock.calls[0]?.[1]?.method).toBe("PUT");
    expect(JSON.parse((fetchMock.mock.calls[0]?.[1]?.body as string) ?? "{}")).toEqual({
      default_budget_cents: 250_000,
    });
    expect(result).toEqual(settingsResponse.personal);
  });

  it("updates system settings through a separate PUT that never touches the personal path", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValue(new Response(JSON.stringify(settingsResponse.system), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await api.updateSystemSettings({ refresh_interval_seconds: 60 });

    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/v1/settings/system");
    expect(fetchMock.mock.calls[0]?.[1]?.method).toBe("PUT");
    expect(JSON.parse((fetchMock.mock.calls[0]?.[1]?.body as string) ?? "{}")).toEqual({
      refresh_interval_seconds: 60,
    });
    expect(result).toEqual(settingsResponse.system);
  });
});

describe("saved taxonomy filters API client", () => {
  it("fetches saved filters for a given scope", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({ country_ids: ["country:DE"], league_ids: [] }), { status: 200 })
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await api.getSavedFilters("events");

    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/v1/preferences/filters/events");
    expect(result).toEqual({ country_ids: ["country:DE"], league_ids: [] });
  });

  it("saves filters for a given scope through a PUT carrying both id lists", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(
        JSON.stringify({ country_ids: ["country:DE"], league_ids: ["league:1"] }),
        { status: 200 }
      )
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await api.saveFilters("opportunities", {
      country_ids: ["country:DE"],
      league_ids: ["league:1"],
    });

    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/v1/preferences/filters/opportunities");
    expect(fetchMock.mock.calls[0]?.[1]?.method).toBe("PUT");
    expect(JSON.parse((fetchMock.mock.calls[0]?.[1]?.body as string) ?? "{}")).toEqual({
      country_ids: ["country:DE"],
      league_ids: ["league:1"],
    });
    expect(result).toEqual({ country_ids: ["country:DE"], league_ids: ["league:1"] });
  });
});

describe("taxonomy API client", () => {
  it("requests the full country catalog only when explicitly requested", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(new Response("[]", { status: 200 }))
      .mockResolvedValueOnce(new Response("[]", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await api.getCountries();
    await api.getCountries({ full_catalog: true });

    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/v1/countries");
    expect(fetchMock.mock.calls[1]?.[0]).toBe("/api/v1/countries?full_catalog=true");
  });

  it("requests the explicit full league catalog without changing pagination", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValue(new Response("[]", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await api.getLeagues({ full_catalog: true, limit: 200, offset: 400 });

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "/api/v1/leagues?limit=200&offset=400&full_catalog=true"
    );
  });
});

describe("Telegram notifications API client", () => {
  const telegramConfig = {
    enabled: false,
    chat_id: null,
    minimum_profit_percent: "0.95",
    decrease_threshold_percentage_points: "0.20",
    increase_threshold_percentage_points: "0.20",
    alert_cooldown_seconds: 300,
    notify_1x2: true,
    notify_ou25: true,
    token_configured: true,
    chat_validated: false,
    validated_at: null,
    last_successful_delivery_at: null,
    last_error: null,
  };

  it("sends Telegram updates and test requests through the authenticated client", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(new Response(JSON.stringify(telegramConfig), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(telegramConfig), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await api.updateTelegramNotificationConfig({
      enabled: true,
      chat_id: "123",
      minimum_profit_percent: "0.95",
      decrease_threshold_percentage_points: "0.20",
      increase_threshold_percentage_points: "0.20",
      alert_cooldown_seconds: 300,
    });
    await api.testTelegramConnection();

    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/v1/notifications/telegram");
    expect(fetchMock.mock.calls[0]?.[1]?.method).toBe("PUT");
    expect(fetchMock.mock.calls[1]?.[0]).toBe("/api/v1/notifications/telegram/test");
    expect(fetchMock.mock.calls[1]?.[1]?.method).toBe("POST");
  });

  it("fetches the Telegram config with a plain GET", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValue(new Response(JSON.stringify(telegramConfig), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await api.getTelegramNotificationConfig();

    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/v1/notifications/telegram");
    expect(result.token_configured).toBe(true);
  });
});
