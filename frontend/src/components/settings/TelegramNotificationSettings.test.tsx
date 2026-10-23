import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import { TelegramNotificationConfig } from "../../types/api";
import { TelegramNotificationSettings } from "./TelegramNotificationSettings";

const baseConfig: TelegramNotificationConfig = {
  enabled: false,
  chat_id: "123",
  minimum_profit_percent: "0.95",
  decrease_threshold_percentage_points: "0.20",
  increase_threshold_percentage_points: "0.20",
  alert_cooldown_seconds: 300,
  notify_1x2: true,
  notify_ou25: true,
  token_configured: true,
  chat_validated: true,
  validated_at: "2026-09-01T10:00:00Z",
  last_successful_delivery_at: null,
  last_error: null,
};

function renderCard() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return {
    queryClient,
    ...render(
      <QueryClientProvider client={queryClient}>
        <TelegramNotificationSettings />
      </QueryClientProvider>
    ),
  };
}

async function renderCardWithLoadedConfig(config: TelegramNotificationConfig = baseConfig) {
  vi.spyOn(api, "getTelegramNotificationConfig").mockResolvedValue(config);
  const result = renderCard();
  await screen.findByLabelText("Telegram Chat ID");
  return result;
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("TelegramNotificationSettings", () => {
  it("shows token and validation state without rendering a token value", async () => {
    vi.spyOn(api, "getTelegramNotificationConfig").mockResolvedValue(baseConfig);
    renderCard();

    expect(await screen.findByText("Bot tokeni yapılandırıldı")).toBeInTheDocument();
    expect(screen.queryByDisplayValue(/token/i)).not.toBeInTheDocument();
  });

  it("shows a missing-token state when the server has no token configured", async () => {
    vi.spyOn(api, "getTelegramNotificationConfig").mockResolvedValue({
      ...baseConfig,
      token_configured: false,
      chat_validated: false,
    });
    renderCard();

    expect(await screen.findByText("Bot tokeni eksik")).toBeInTheDocument();
  });

  it("rejects an invalid chat id and a three-decimal threshold", async () => {
    await renderCardWithLoadedConfig();
    vi.spyOn(api, "updateTelegramNotificationConfig");

    fireEvent.change(screen.getByLabelText("Telegram Chat ID"), { target: { value: "-1" } });
    fireEvent.change(screen.getByLabelText("Minimum Fark (%)"), { target: { value: "0.951" } });
    fireEvent.click(screen.getByRole("button", { name: "Telegram Ayarlarını Kaydet" }));

    expect(api.updateTelegramNotificationConfig).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent("en fazla iki ondalık");
  });

  it("saves before allowing a connection test for a changed chat", async () => {
    await renderCardWithLoadedConfig();

    fireEvent.change(screen.getByLabelText("Telegram Chat ID"), { target: { value: "456" } });

    expect(screen.getByRole("button", { name: "Test Mesajı Gönder" })).toBeDisabled();
  });

  it("saves valid settings through the authenticated client", async () => {
    await renderCardWithLoadedConfig();
    vi.spyOn(api, "updateTelegramNotificationConfig").mockResolvedValue({
      ...baseConfig,
      minimum_profit_percent: "1.50",
    });

    fireEvent.change(screen.getByLabelText("Minimum Fark (%)"), { target: { value: "1.50" } });
    fireEvent.click(screen.getByRole("button", { name: "Telegram Ayarlarını Kaydet" }));

    await waitFor(() =>
      expect(api.updateTelegramNotificationConfig).toHaveBeenCalledWith(
        expect.objectContaining({ minimum_profit_percent: "1.50", chat_id: "123" })
      )
    );
  });

  it("disables the enabled toggle until the chat is validated", async () => {
    await renderCardWithLoadedConfig({ ...baseConfig, chat_validated: false });

    expect(screen.getByLabelText("Telegram Bildirimlerini Etkinleştir")).toBeDisabled();
  });

  it("runs a connection test and reports success without exposing a token", async () => {
    vi.spyOn(api, "testTelegramConnection").mockResolvedValue({
      ...baseConfig,
      chat_validated: true,
      last_successful_delivery_at: "2026-09-01T12:00:00Z",
    });
    await renderCardWithLoadedConfig();

    fireEvent.click(screen.getByRole("button", { name: "Test Mesajı Gönder" }));

    await waitFor(() => expect(api.testTelegramConnection).toHaveBeenCalled());
    expect(await screen.findByText("Bağlantı testi başarılı.")).toBeInTheDocument();
  });

  it("allows selecting and updating notification preferences for 1X2 and OU25 markets", async () => {
    await renderCardWithLoadedConfig();
    vi.spyOn(api, "updateTelegramNotificationConfig").mockResolvedValue({
      ...baseConfig,
      notify_1x2: false,
      notify_ou25: true,
    });

    const checkbox1x2 = screen.getByLabelText("Maç Sonucu (MS 1X2)");
    const checkboxOu25 = screen.getByLabelText("2.5 Alt / Üst");

    expect(checkbox1x2).toBeChecked();
    expect(checkboxOu25).toBeChecked();

    fireEvent.click(checkbox1x2);
    expect(checkbox1x2).not.toBeChecked();

    fireEvent.click(screen.getByRole("button", { name: "Telegram Ayarlarını Kaydet" }));

    await waitFor(() =>
      expect(api.updateTelegramNotificationConfig).toHaveBeenCalledWith(
        expect.objectContaining({
          notify_1x2: false,
          notify_ou25: true,
        })
      )
    );
  });
});
