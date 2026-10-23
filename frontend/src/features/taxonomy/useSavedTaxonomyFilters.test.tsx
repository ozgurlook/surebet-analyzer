import React from "react";
import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../../api/client";
import { TaxonomyFilters } from "../../types/api";
import { useSavedTaxonomyFilters } from "./useSavedTaxonomyFilters";

// @testing-library/dom's `waitFor` only knows how to auto-advance fake
// timers through a global `jest.advanceTimersByTime` (its "modern fake
// timers" detection also requires that same global to exist) -- it has no
// native Vitest awareness. This project's Vitest fake timers are otherwise
// equivalent (`vi.advanceTimersByTime` has the identical signature), so
// aliasing the one method `waitFor` calls is the standard, minimal way to
// make `waitFor` and `vi.useFakeTimers()` interoperate here. Without this,
// every `await waitFor(...)` below hangs until Vitest's real per-test
// timeout, because `waitFor`'s internal polling `setInterval` is itself one
// of the timers `vi.useFakeTimers()` fakes, and nothing else advances it.
(globalThis as unknown as { jest?: { advanceTimersByTime: typeof vi.advanceTimersByTime } }).jest = {
  advanceTimersByTime: (...args) => vi.advanceTimersByTime(...args),
};

const EMPTY: TaxonomyFilters = { country_ids: [], league_ids: [] };

function makeWrapper(client: QueryClient) {
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("useSavedTaxonomyFilters initial load", () => {
  it("loads saved filters for the given user and scope after mount", async () => {
    vi.spyOn(api, "getSavedFilters").mockResolvedValue({
      country_ids: ["country:DE"],
      league_ids: ["league:1"],
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    const { result } = renderHook(() => useSavedTaxonomyFilters(1, "events"), {
      wrapper: makeWrapper(client),
    });

    await waitFor(() =>
      expect(result.current.filters).toEqual({ country_ids: ["country:DE"], league_ids: ["league:1"] })
    );
    expect(api.getSavedFilters).toHaveBeenCalledWith("events");
  });

  it("does not fetch before a valid userId is available", () => {
    const spy = vi.spyOn(api, "getSavedFilters").mockResolvedValue(EMPTY);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    renderHook(() => useSavedTaxonomyFilters(null, "events"), {
      wrapper: makeWrapper(client),
    });

    expect(spy).not.toHaveBeenCalled();
  });
});

describe("useSavedTaxonomyFilters immediate local updates", () => {
  it("applies onChange to local filters immediately, without waiting on the network", async () => {
    vi.spyOn(api, "getSavedFilters").mockResolvedValue(EMPTY);
    vi.spyOn(api, "saveFilters").mockResolvedValue(EMPTY);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    const { result } = renderHook(() => useSavedTaxonomyFilters(1, "events"), {
      wrapper: makeWrapper(client),
    });
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    act(() => {
      result.current.onChange({ country_ids: ["country:DE"], league_ids: [] });
    });

    expect(result.current.filters).toEqual({ country_ids: ["country:DE"], league_ids: [] });
    // The write has not been sent yet -- it is debounced.
    expect(api.saveFilters).not.toHaveBeenCalled();
  });
});

describe("useSavedTaxonomyFilters debounced persistence", () => {
  it("sends exactly one PUT after 300ms of inactivity, not one per keystroke", async () => {
    vi.spyOn(api, "getSavedFilters").mockResolvedValue(EMPTY);
    const saveSpy = vi.spyOn(api, "saveFilters").mockResolvedValue({
      country_ids: ["country:DE", "country:TR"],
      league_ids: [],
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    const { result } = renderHook(() => useSavedTaxonomyFilters(1, "events"), {
      wrapper: makeWrapper(client),
    });
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    act(() => {
      result.current.onChange({ country_ids: ["country:DE"], league_ids: [] });
    });
    await act(async () => {
      vi.advanceTimersByTime(150);
    });
    act(() => {
      result.current.onChange({ country_ids: ["country:DE", "country:TR"], league_ids: [] });
    });

    // Still well within the debounce window measured from the *last* edit.
    await act(async () => {
      vi.advanceTimersByTime(150);
    });
    expect(saveSpy).not.toHaveBeenCalled();

    await act(async () => {
      vi.advanceTimersByTime(150);
    });

    expect(saveSpy).toHaveBeenCalledTimes(1);
    expect(saveSpy).toHaveBeenCalledWith("events", {
      country_ids: ["country:DE", "country:TR"],
      league_ids: [],
    });
  });

  it("cancels a pending write on unmount so it never fires afterward", async () => {
    vi.spyOn(api, "getSavedFilters").mockResolvedValue(EMPTY);
    const saveSpy = vi.spyOn(api, "saveFilters").mockResolvedValue(EMPTY);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    const { result, unmount } = renderHook(() => useSavedTaxonomyFilters(1, "events"), {
      wrapper: makeWrapper(client),
    });
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    act(() => {
      result.current.onChange({ country_ids: ["country:DE"], league_ids: [] });
    });
    unmount();

    await act(async () => {
      vi.advanceTimersByTime(400);
    });

    expect(saveSpy).not.toHaveBeenCalled();
  });
});

describe("useSavedTaxonomyFilters scope independence", () => {
  it("never lets an edit in one scope write to, or read from, the other scope", async () => {
    const eventsFilters = { country_ids: ["country:DE"], league_ids: [] };
    const opportunitiesFilters = { country_ids: ["country:TR"], league_ids: [] };
    vi.spyOn(api, "getSavedFilters").mockImplementation(async (scope) =>
      scope === "events" ? eventsFilters : opportunitiesFilters
    );
    const saveSpy = vi.spyOn(api, "saveFilters").mockImplementation(async (_scope, filters) => filters);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const wrapper = makeWrapper(client);

    const events = renderHook(() => useSavedTaxonomyFilters(1, "events"), { wrapper });
    const opportunities = renderHook(() => useSavedTaxonomyFilters(1, "opportunities"), { wrapper });

    await waitFor(() => expect(events.result.current.filters).toEqual(eventsFilters));
    await waitFor(() => expect(opportunities.result.current.filters).toEqual(opportunitiesFilters));

    act(() => {
      events.result.current.onChange({ country_ids: ["country:DE", "country:ES"], league_ids: [] });
    });
    await act(async () => {
      vi.advanceTimersByTime(300);
    });

    expect(saveSpy).toHaveBeenCalledTimes(1);
    expect(saveSpy).toHaveBeenCalledWith("events", {
      country_ids: ["country:DE", "country:ES"],
      league_ids: [],
    });
    // The opportunities-scoped hook instance must be untouched.
    expect(opportunities.result.current.filters).toEqual(opportunitiesFilters);
  });
});

describe("useSavedTaxonomyFilters user isolation", () => {
  it("never shows the previous user's filters while switching users, and cancels their pending write", async () => {
    const userOneFilters = { country_ids: ["country:DE"], league_ids: [] };
    const userTwoFilters = { country_ids: ["country:TR"], league_ids: [] };
    vi.spyOn(api, "getSavedFilters").mockImplementation(async () => userOneFilters);
    const saveSpy = vi.spyOn(api, "saveFilters").mockResolvedValue(userOneFilters);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    const { result, rerender } = renderHook(
      ({ userId }: { userId: number }) => useSavedTaxonomyFilters(userId, "events"),
      { wrapper: makeWrapper(client), initialProps: { userId: 1 } }
    );
    await waitFor(() => expect(result.current.filters).toEqual(userOneFilters));

    // Start an edit for user 1 but do not let the debounce fire yet.
    act(() => {
      result.current.onChange({ country_ids: ["country:DE", "country:FR"], league_ids: [] });
    });

    // Switch users before the debounce completes.
    vi.spyOn(api, "getSavedFilters").mockImplementation(async () => userTwoFilters);
    rerender({ userId: 2 });

    // Never shows user 1's (even edited) selection once the key has changed.
    expect(result.current.filters).not.toEqual({
      country_ids: ["country:DE", "country:FR"],
      league_ids: [],
    });

    await act(async () => {
      vi.advanceTimersByTime(400);
    });

    // The pending write for user 1 must never have been sent once user 2 is active.
    expect(saveSpy).not.toHaveBeenCalled();

    await waitFor(() => expect(result.current.filters).toEqual(userTwoFilters));
  });
});

describe("useSavedTaxonomyFilters failed save handling", () => {
  it("keeps the optimistic selection and exposes a retryable error instead of reverting silently", async () => {
    vi.spyOn(api, "getSavedFilters").mockResolvedValue(EMPTY);
    const saveSpy = vi
      .spyOn(api, "saveFilters")
      .mockRejectedValueOnce(new Error("network down"))
      .mockResolvedValueOnce({ country_ids: ["country:DE"], league_ids: [] });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    const { result } = renderHook(() => useSavedTaxonomyFilters(1, "events"), {
      wrapper: makeWrapper(client),
    });
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    act(() => {
      result.current.onChange({ country_ids: ["country:DE"], league_ids: [] });
    });
    await act(async () => {
      vi.advanceTimersByTime(300);
    });
    await waitFor(() => expect(result.current.isSaveError).toBe(true));

    // Not silently reverted to the stale (empty) server value.
    expect(result.current.filters).toEqual({ country_ids: ["country:DE"], league_ids: [] });

    await act(async () => {
      result.current.retry();
    });

    await waitFor(() => expect(result.current.isSaveError).toBe(false));
    expect(saveSpy).toHaveBeenCalledTimes(2);
  });
});

describe("useSavedTaxonomyFilters query invalidation", () => {
  it("invalidates the matching events/opportunities query after a successful save", async () => {
    vi.spyOn(api, "getSavedFilters").mockResolvedValue(EMPTY);
    vi.spyOn(api, "saveFilters").mockResolvedValue({ country_ids: ["country:DE"], league_ids: [] });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const invalidateSpy = vi.spyOn(client, "invalidateQueries");

    const { result } = renderHook(() => useSavedTaxonomyFilters(1, "opportunities"), {
      wrapper: makeWrapper(client),
    });
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    act(() => {
      result.current.onChange({ country_ids: ["country:DE"], league_ids: [] });
    });
    await act(async () => {
      vi.advanceTimersByTime(300);
    });

    await waitFor(() =>
      expect(invalidateSpy).toHaveBeenCalledWith(
        expect.objectContaining({ queryKey: ["opportunities"] })
      )
    );
  });
});

describe("useSavedTaxonomyFilters query override semantics", () => {
  it("omits explicit override params while local filters match the confirmed saved state", async () => {
    vi.spyOn(api, "getSavedFilters").mockResolvedValue({
      country_ids: ["country:DE"],
      league_ids: [],
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    const { result } = renderHook(() => useSavedTaxonomyFilters(1, "events"), {
      wrapper: makeWrapper(client),
    });
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    expect(result.current.queryOverride).toEqual({});
  });

  it("sends an explicit override while an edit is in flight and not yet persisted, then omits it again once saved", async () => {
    vi.spyOn(api, "getSavedFilters").mockResolvedValue(EMPTY);
    vi.spyOn(api, "saveFilters").mockResolvedValue({ country_ids: ["country:DE"], league_ids: [] });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    const { result } = renderHook(() => useSavedTaxonomyFilters(1, "events"), {
      wrapper: makeWrapper(client),
    });
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    act(() => {
      result.current.onChange({ country_ids: ["country:DE"], league_ids: [] });
    });
    expect(result.current.queryOverride).toEqual({
      country_ids: ["country:DE"],
      league_ids: [],
      use_saved_filters: false,
    });

    await act(async () => {
      vi.advanceTimersByTime(300);
    });

    await waitFor(() => expect(result.current.queryOverride).toEqual({}));
  });

  it("still sends use_saved_filters:false when the override clears every filter to empty", async () => {
    // Clearing all filters produces country_ids/league_ids that are both []
    // -- a plain query string cannot distinguish that explicit override from
    // an omitted param, so use_saved_filters:false must be present to tell
    // the backend not to fall back to the still-saved filter (Finding 3).
    vi.spyOn(api, "getSavedFilters").mockResolvedValue({
      country_ids: ["country:TR"],
      league_ids: [],
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    const { result } = renderHook(() => useSavedTaxonomyFilters(1, "events"), {
      wrapper: makeWrapper(client),
    });
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    act(() => {
      result.current.onChange({ country_ids: [], league_ids: [] });
    });

    expect(result.current.queryOverride).toEqual({
      country_ids: [],
      league_ids: [],
      use_saved_filters: false,
    });
  });

  it("does not trigger any extra network write merely from local re-renders while editing", async () => {
    vi.spyOn(api, "getSavedFilters").mockResolvedValue(EMPTY);
    const saveSpy = vi.spyOn(api, "saveFilters").mockResolvedValue(EMPTY);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    const { result, rerender } = renderHook(() => useSavedTaxonomyFilters(1, "events"), {
      wrapper: makeWrapper(client),
    });
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    act(() => {
      result.current.onChange({ country_ids: ["country:DE"], league_ids: [] });
    });
    // Reading queryOverride and re-rendering the hook consumer repeatedly
    // (as a page would on every keystroke elsewhere) must not itself save.
    rerender();
    rerender();
    expect(saveSpy).not.toHaveBeenCalled();
  });
});
