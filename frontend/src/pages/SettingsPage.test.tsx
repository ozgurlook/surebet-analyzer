import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../api/client";
import {
  SettingsResponse,
  SourceName,
  TelegramNotificationConfig,
} from "../types/api";
import { SettingsPage } from "./SettingsPage";

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

const TEST_USER_ID = 1;
const TEST_USERNAME = "alice";

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return {
    client,
    ...render(
      <QueryClientProvider client={client}>
        <SettingsPage
          userId={TEST_USER_ID}
          username={TEST_USERNAME}
          onLogout={vi.fn()}
          onOpenMappingAdmin={vi.fn()}
        />
      </QueryClientProvider>,
    ),
  };
}

// The settings query resolves asynchronously and only then does the effect
// that syncs local state from `settings` commit. Flushing here avoids a race
// where a click performed too early gets clobbered by the late-arriving sync
// effect.
async function waitForSettingsToSettle() {
  await waitFor(() => expect(api.getSettings).toHaveBeenCalled());
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

afterEach(() => {
  vi.restoreAllMocks();
});

beforeEach(() => {
  localStorage.clear();
  // The Telegram card queries independently of the general settings form; stub it
  // to a benign response so its own network failure never collides with this
  // page's `role="alert"` assertions -- isolation is covered separately in
  // TelegramNotificationSettings.test.tsx.
  vi.spyOn(api, "getTelegramNotificationConfig").mockResolvedValue(telegramConfig);
});

describe("SettingsPage username display", () => {
  it("shows the authenticated username", async () => {
    vi.spyOn(api, "getSettings").mockResolvedValue(settings);

    renderPage();
    await waitForSettingsToSettle();

    expect(await screen.findByText(TEST_USERNAME)).toBeInTheDocument();
  });
});

describe("SettingsPage shared vs personal labeling", () => {
  it("labels budget/Kaynak A controls as personal", async () => {
    vi.spyOn(api, "getSettings").mockResolvedValue(settings);

    renderPage();
    await waitForSettingsToSettle();

    const personalLabels = screen.getAllByText("Kişisel");
    expect(personalLabels.length).toBeGreaterThan(0);
    // The personal-section label must actually be near the budget control,
    // not merely present somewhere on the page.
    const personalSection = screen.getByLabelText("Varsayılan Bütçe (TL)").closest("form");
    expect(personalSection).not.toBeNull();
    expect(personalSection).toHaveTextContent("Kişisel");
  });

  it("labels source URLs and timing/staleness controls as shared", async () => {
    vi.spyOn(api, "getSettings").mockResolvedValue(settings);

    renderPage();
    await waitForSettingsToSettle();

    const sharedLabels = screen.getAllByText("Paylaşılan");
    expect(sharedLabels.length).toBeGreaterThan(0);
    const systemSection = screen
      .getByText("Otomatik Toplama Aralığı")
      .closest("form");
    expect(systemSection).not.toBeNull();
    expect(systemSection).toHaveTextContent("Paylaşılan");
  });
});

describe("SettingsPage personal settings", () => {
  it("offers no tracked-league control, since the scope is fixed", async () => {
    vi.spyOn(api, "getSettings").mockResolvedValue(settings);

    renderPage();
    await waitForSettingsToSettle();

    expect(screen.queryByLabelText(/Yalnızca Takip Edilen Ligler/i)).toBeNull();
  });

  it("submits budget/Kaynak A through updatePersonalSettings only", async () => {
    vi.spyOn(api, "getSettings").mockResolvedValue(settings);
    const updatePersonal = vi.spyOn(api, "updatePersonalSettings").mockResolvedValue(settings.personal);
    const updateSystem = vi.spyOn(api, "updateSystemSettings").mockResolvedValue(settings.system);

    renderPage();
    await waitForSettingsToSettle();

    const source_a = await screen.findByLabelText(/Kaynak A Yan Oranları Varsayılanı/i);
    fireEvent.click(source_a);
    expect(source_a).toBeChecked();

    fireEvent.click(screen.getByRole("button", { name: /Kişisel Ayarları Kaydet/i }));

    await waitFor(() => {
      expect(updatePersonal).toHaveBeenCalledWith(
        expect.objectContaining({ include_source_a_match_sides: true }),
      );
    });
    expect(updateSystem).not.toHaveBeenCalled();
  });

  it("does not submit a default budget below the supported minimum", async () => {
    vi.spyOn(api, "getSettings").mockResolvedValue(settings);
    const updatePersonal = vi.spyOn(api, "updatePersonalSettings").mockResolvedValue(settings.personal);
    renderPage();
    await waitForSettingsToSettle();

    fireEvent.change(screen.getByLabelText("Varsayılan Bütçe (TL)"), {
      target: { value: "9" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Kişisel Ayarları Kaydet/i }));

    expect(updatePersonal).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "10 ile 10.000.000 TL arasında tam sayı girin"
    );
  });

  it("shows an error when the personal save fails, independent of system settings", async () => {
    vi.spyOn(api, "getSettings").mockResolvedValue(settings);
    vi.spyOn(api, "updatePersonalSettings").mockRejectedValue(new Error("save failed"));
    const updateSystem = vi.spyOn(api, "updateSystemSettings").mockResolvedValue(settings.system);
    renderPage();
    await waitForSettingsToSettle();

    fireEvent.click(screen.getByRole("button", { name: /Kişisel Ayarları Kaydet/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Kişisel ayarlar kaydedilemedi");
    // The independent system save control must show no error and never fire.
    expect(updateSystem).not.toHaveBeenCalled();
    expect(screen.queryByText("Paylaşılan ayarlar kaydedilemedi")).not.toBeInTheDocument();
  });

  it("clears page-level opportunity overrides after personal defaults are saved", async () => {
    localStorage.setItem(
      `surebet.opportunities.controls.${TEST_USER_ID}`,
      JSON.stringify({ budgetLiras: 2500, includeSourceA: true })
    );
    vi.spyOn(api, "getSettings").mockResolvedValue(settings);
    vi.spyOn(api, "updatePersonalSettings").mockResolvedValue(settings.personal);
    renderPage();
    await waitForSettingsToSettle();

    fireEvent.click(screen.getByRole("button", { name: /Kişisel Ayarları Kaydet/i }));

    await waitFor(() =>
      expect(localStorage.getItem(`surebet.opportunities.controls.${TEST_USER_ID}`)).toBeNull()
    );
  });
});

describe("SettingsPage system settings", () => {
  it("reflects the stored refresh interval and staleness limit", async () => {
    vi.spyOn(api, "getSettings").mockResolvedValue(settings);

    renderPage();
    await waitForSettingsToSettle();

    expect(screen.getByText("30 sn")).toBeInTheDocument();
    expect(screen.getByText("75 sn")).toBeInTheDocument();
  });

  it("submits refresh interval/staleness limit through updateSystemSettings only", async () => {
    vi.spyOn(api, "getSettings").mockResolvedValue(settings);
    const updatePersonal = vi.spyOn(api, "updatePersonalSettings").mockResolvedValue(settings.personal);
    const updateSystem = vi.spyOn(api, "updateSystemSettings").mockResolvedValue(settings.system);

    renderPage();
    await waitForSettingsToSettle();

    fireEvent.change(screen.getByLabelText(/Otomatik Toplama Aralığı/i), {
      target: { value: "60" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Paylaşılan Ayarları Kaydet/i }));

    await waitFor(() => {
      expect(updateSystem).toHaveBeenCalledWith(
        expect.objectContaining({ refresh_interval_seconds: 60 }),
      );
    });
    expect(updatePersonal).not.toHaveBeenCalled();
  });

  it("shows an error when the system save fails, independent of personal settings", async () => {
    vi.spyOn(api, "getSettings").mockResolvedValue(settings);
    const updatePersonal = vi.spyOn(api, "updatePersonalSettings").mockResolvedValue(settings.personal);
    vi.spyOn(api, "updateSystemSettings").mockRejectedValue(new Error("save failed"));
    renderPage();
    await waitForSettingsToSettle();

    fireEvent.click(screen.getByRole("button", { name: /Paylaşılan Ayarları Kaydet/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Paylaşılan ayarlar kaydedilemedi");
    expect(updatePersonal).not.toHaveBeenCalled();
    expect(screen.queryByText("Kişisel ayarlar kaydedilemedi")).not.toBeInTheDocument();
  });

  it("does not roll back or fake a successful personal save when the system save fails, and vice versa", async () => {
    vi.spyOn(api, "getSettings").mockResolvedValue(settings);
    vi.spyOn(api, "updatePersonalSettings").mockResolvedValue(settings.personal);
    vi.spyOn(api, "updateSystemSettings").mockRejectedValue(new Error("system down"));
    renderPage();
    await waitForSettingsToSettle();

    fireEvent.click(screen.getByRole("button", { name: /Kişisel Ayarları Kaydet/i }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /Kişisel Ayarları Kaydedildi/i })).toBeInTheDocument()
    );

    fireEvent.click(screen.getByRole("button", { name: /Paylaşılan Ayarları Kaydet/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Paylaşılan ayarlar kaydedilemedi");

    // The earlier, independent personal-save success indicator must still stand.
    expect(screen.getByRole("button", { name: /Kişisel Ayarları Kaydedildi/i })).toBeInTheDocument();
  });
});

describe("SettingsPage load and auxiliary controls", () => {
  it("shows a load error instead of silently rendering defaults", async () => {
    vi.spyOn(api, "getSettings").mockRejectedValue(new Error("network down"));

    renderPage();

    expect(await screen.findByRole("alert")).toHaveTextContent("Ayarlar yüklenemedi");
  });

  it("mounts the Telegram card alongside the general settings form", async () => {
    vi.spyOn(api, "getSettings").mockResolvedValue(settings);

    renderPage();
    await waitForSettingsToSettle();

    expect(await screen.findByText("Telegram Bildirimleri")).toBeInTheDocument();
  });

  it("does not replace the page with a global error when the Telegram query fails", async () => {
    vi.spyOn(api, "getSettings").mockResolvedValue(settings);
    vi.spyOn(api, "getTelegramNotificationConfig").mockRejectedValue(new Error("telegram down"));

    renderPage();
    await waitForSettingsToSettle();

    expect(await screen.findByText("Telegram ayarları yüklenemedi.")).toBeInTheDocument();
    expect(await screen.findByLabelText("Varsayılan Bütçe (TL)")).toBeInTheDocument();
  });
});
