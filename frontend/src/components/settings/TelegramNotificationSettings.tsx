import React, { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, LoaderCircle, Send, Shield } from "lucide-react";
import { ApiError, api } from "../../api/client";
import { TelegramNotificationUpdate } from "../../types/api";

const PERCENT_PATTERN = /^(?:100(?:\.0{1,2})?|(?:\d{1,2})(?:\.\d{1,2})?)$/;
const isPercentInRange = (value: string): boolean =>
  PERCENT_PATTERN.test(value) && Number(value) >= 0.01 && Number(value) <= 100;

const MAX_CHAT_ID = 9_223_372_036_854_775_807n;
const isPositiveChatId = (value: string): boolean => {
  if (!/^\d+$/.test(value)) return false;
  const parsed = BigInt(value);
  return parsed > 0n && parsed <= MAX_CHAT_ID;
};

export const TelegramNotificationSettings: React.FC = () => {
  const queryClient = useQueryClient();
  const { data: config, isLoading, isError, error, refetch } = useQuery({
    queryKey: ["telegram-notifications"],
    queryFn: api.getTelegramNotificationConfig,
  });

  const [enabled, setEnabled] = useState(false);
  const [chatId, setChatId] = useState("");
  const [minPercent, setMinPercent] = useState("0.95");
  const [decreasePercent, setDecreasePercent] = useState("0.20");
  const [increasePercent, setIncreasePercent] = useState("0.20");
  const [cooldownMinutes, setCooldownMinutes] = useState("5");
  const [notify1x2, setNotify1x2] = useState(true);
  const [notifyOu25, setNotifyOu25] = useState(true);
  const [validationError, setValidationError] = useState<string | null>(null);
  const [savedSuccess, setSavedSuccess] = useState(false);
  const [testMessage, setTestMessage] = useState<{ kind: "success" | "error"; text: string } | null>(
    null
  );
  const lastSavedChatId = useRef<string>("");

  useEffect(() => {
    if (config) {
      setEnabled(config.enabled);
      setChatId(config.chat_id ?? "");
      setMinPercent(config.minimum_profit_percent);
      setDecreasePercent(config.decrease_threshold_percentage_points);
      setIncreasePercent(config.increase_threshold_percentage_points);
      setCooldownMinutes(String(Math.round(config.alert_cooldown_seconds / 60)));
      setNotify1x2(config.notify_1x2);
      setNotifyOu25(config.notify_ou25);
      lastSavedChatId.current = config.chat_id ?? "";
    }
  }, [config]);

  const saveMutation = useMutation({
    mutationFn: (updates: TelegramNotificationUpdate) => api.updateTelegramNotificationConfig(updates),
    onSuccess: (updated) => {
      queryClient.setQueryData(["telegram-notifications"], updated);
      lastSavedChatId.current = updated.chat_id ?? "";
      setSavedSuccess(true);
      setTestMessage(null);
      setTimeout(() => setSavedSuccess(false), 3000);
    },
  });

  const testMutation = useMutation({
    mutationFn: api.testTelegramConnection,
    onSuccess: (updated) => {
      queryClient.setQueryData(["telegram-notifications"], updated);
      setTestMessage({ kind: "success", text: "Bağlantı testi başarılı." });
    },
    onError: (mutationError: Error) => {
      setTestMessage({ kind: "error", text: mutationError.message });
    },
  });

  if (isLoading) {
    return <div className="h-32 rounded-2xl border border-navy-800 bg-navy-900/60 animate-pulse" />;
  }

  if (isError || !config) {
    return (
      <div
        role="alert"
        className="rounded-2xl border border-rose-500/30 bg-rose-500/10 p-5 text-center text-xs text-rose-300"
      >
        <p className="font-semibold">Telegram ayarları yüklenemedi.</p>
        {error instanceof Error && <p className="mt-1">{error.message}</p>}
        <button
          type="button"
          onClick={() => refetch()}
          className="touch-target mt-3 rounded-xl border border-rose-500/30 px-3 font-semibold"
        >
          Tekrar Dene
        </button>
      </div>
    );
  }

  const chatChangedSincePersist = chatId !== lastSavedChatId.current;
  const testDisabled =
    !config.token_configured || !chatId || chatChangedSincePersist || testMutation.isPending;
  const enableToggleDisabled = !config.token_configured || !config.chat_validated;

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    setValidationError(null);
    setTestMessage(null);

    if (!isPercentInRange(minPercent)) {
      setValidationError("Minimum fark yüzdesi en fazla iki ondalık basamak içerebilir (0,01-100).");
      return;
    }
    if (!isPercentInRange(decreasePercent)) {
      setValidationError("Düşüş eşiği en fazla iki ondalık basamak içerebilir (0,01-100).");
      return;
    }
    if (!isPercentInRange(increasePercent)) {
      setValidationError("Yükseliş eşiği en fazla iki ondalık basamak içerebilir (0,01-100).");
      return;
    }
    if (chatId && !isPositiveChatId(chatId)) {
      setValidationError("Chat ID pozitif bir tam sayı olmalı.");
      return;
    }
    const cooldownSeconds = Number(cooldownMinutes) * 60;
    if (!Number.isInteger(cooldownSeconds) || cooldownSeconds < 60 || cooldownSeconds > 86_400) {
      setValidationError("Bekleme süresi 1 ile 1440 dakika arasında olmalı.");
      return;
    }

    saveMutation.mutate({
      enabled,
      chat_id: chatId ? chatId : null,
      minimum_profit_percent: minPercent,
      decrease_threshold_percentage_points: decreasePercent,
      increase_threshold_percentage_points: increasePercent,
      alert_cooldown_seconds: cooldownSeconds,
      notify_1x2: notify1x2,
      notify_ou25: notifyOu25,
    });
  };

  const saveErrorMessage =
    saveMutation.isError && saveMutation.error instanceof Error ? saveMutation.error.message : null;
  const saveErrorDetail =
    saveMutation.error instanceof ApiError ? saveMutation.error.message : saveErrorMessage;

  return (
    <section
      aria-labelledby="telegram-notification-settings-title"
      className="bg-navy-900 rounded-2xl border border-navy-800 p-4 space-y-3 shadow-sm"
    >
      <div>
        <h2
          id="telegram-notification-settings-title"
          className="text-sm font-bold text-white flex items-center gap-1.5"
        >
          <Send className="w-4 h-4 text-brand-primary" />
          Telegram Bildirimleri
        </h2>
        <p className="text-[11px] text-slate-400">
          Cashback dahil %0,95 ve üzeri fırsatlar için Telegram üzerinden bildirim al.
        </p>
      </div>

      <div className="flex items-center justify-between text-[11px]">
        <span className="text-slate-300">
          {config.token_configured ? "Bot tokeni yapılandırıldı" : "Bot tokeni eksik"}
        </span>
        <span className={config.chat_validated ? "text-emerald-400" : "text-amber-400"}>
          {config.chat_validated ? "Chat doğrulandı" : "Doğrulama gerekli"}
        </span>
      </div>

      <form onSubmit={handleSubmit} className="space-y-3">
        <div className="flex items-center justify-between">
          <label
            htmlFor="telegram-enabled"
            className="text-xs font-bold text-white flex items-center gap-1.5"
          >
            <Shield className="w-4 h-4 text-brand-primary" />
            Telegram Bildirimlerini Etkinleştir
          </label>
          <input
            id="telegram-enabled"
            type="checkbox"
            checked={enabled}
            disabled={enableToggleDisabled}
            onChange={(event) => setEnabled(event.target.checked)}
            className="w-4 h-4 accent-brand-primary rounded disabled:opacity-40"
          />
        </div>

        <div className="space-y-2 pt-2 border-t border-navy-800">
          <span className="text-xs font-bold text-white block">
            Bildirim Gönderilecek Bahis Türleri
          </span>
          <div className="grid grid-cols-2 gap-3">
            <label
              htmlFor="telegram-notify-1x2"
              className="flex items-center gap-2 p-2.5 rounded-xl bg-navy-950 border border-navy-800 cursor-pointer text-xs text-slate-200 hover:border-navy-700"
            >
              <input
                id="telegram-notify-1x2"
                type="checkbox"
                checked={notify1x2}
                onChange={(event) => setNotify1x2(event.target.checked)}
                className="w-4 h-4 accent-brand-primary rounded"
              />
              <span>Maç Sonucu (MS 1X2)</span>
            </label>
            <label
              htmlFor="telegram-notify-ou25"
              className="flex items-center gap-2 p-2.5 rounded-xl bg-navy-950 border border-navy-800 cursor-pointer text-xs text-slate-200 hover:border-navy-700"
            >
              <input
                id="telegram-notify-ou25"
                type="checkbox"
                checked={notifyOu25}
                onChange={(event) => setNotifyOu25(event.target.checked)}
                className="w-4 h-4 accent-brand-primary rounded"
              />
              <span>2.5 Alt / Üst</span>
            </label>
          </div>
        </div>

        <div className="space-y-1">
          <label htmlFor="telegram-chat-id" className="text-xs font-bold text-white">
            Telegram Chat ID
          </label>
          <input
            id="telegram-chat-id"
            type="text"
            inputMode="numeric"
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
            value={chatId}
            onChange={(event) => setChatId(event.target.value)}
            className="w-full bg-navy-950 border border-navy-800 rounded-xl p-2.5 text-sm font-mono text-white outline-none focus:border-brand-primary"
          />
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1">
            <label htmlFor="telegram-min-percent" className="text-xs font-bold text-white">
              Minimum Fark (%)
            </label>
            <input
              id="telegram-min-percent"
              type="text"
              inputMode="decimal"
              value={minPercent}
              onChange={(event) => setMinPercent(event.target.value)}
              className="w-full bg-navy-950 border border-navy-800 rounded-xl p-2.5 text-sm font-mono text-white outline-none focus:border-brand-primary"
            />
          </div>
          <div className="space-y-1">
            <label htmlFor="telegram-cooldown" className="text-xs font-bold text-white">
              Uyarı Bekleme Süresi (dk)
            </label>
            <input
              id="telegram-cooldown"
              type="text"
              inputMode="numeric"
              value={cooldownMinutes}
              onChange={(event) => setCooldownMinutes(event.target.value)}
              className="w-full bg-navy-950 border border-navy-800 rounded-xl p-2.5 text-sm font-mono text-white outline-none focus:border-brand-primary"
            />
            <p className="text-[11px] text-navy-400">
              İki uyarı arasındaki en kısa süre. Düşüş ve yükseliş uyarıları aynı sayacı paylaşır.
            </p>
          </div>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1">
            <label htmlFor="telegram-decrease-percent" className="text-xs font-bold text-white">
              Düşüş Uyarı Eşiği (puan)
            </label>
            <input
              id="telegram-decrease-percent"
              type="text"
              inputMode="decimal"
              value={decreasePercent}
              onChange={(event) => setDecreasePercent(event.target.value)}
              className="w-full bg-navy-950 border border-navy-800 rounded-xl p-2.5 text-sm font-mono text-white outline-none focus:border-brand-primary"
            />
          </div>
          <div className="space-y-1">
            <label htmlFor="telegram-increase-percent" className="text-xs font-bold text-white">
              Yükseliş Uyarı Eşiği (puan)
            </label>
            <input
              id="telegram-increase-percent"
              type="text"
              inputMode="decimal"
              value={increasePercent}
              onChange={(event) => setIncreasePercent(event.target.value)}
              className="w-full bg-navy-950 border border-navy-800 rounded-xl p-2.5 text-sm font-mono text-white outline-none focus:border-brand-primary"
            />
          </div>
        </div>

        {validationError && (
          <p role="alert" className="text-[11px] text-rose-300">
            {validationError}
          </p>
        )}

        <button
          type="submit"
          disabled={saveMutation.isPending}
          className="touch-target w-full py-2.5 rounded-xl bg-brand-primary text-navy-950 font-bold text-xs flex items-center justify-center gap-1.5 disabled:opacity-60"
        >
          {saveMutation.isPending ? (
            <LoaderCircle className="w-4 h-4 animate-spin" />
          ) : savedSuccess ? (
            <Check className="w-4 h-4" />
          ) : null}
          Telegram Ayarlarını Kaydet
        </button>
        {saveErrorDetail && (
          <p role="alert" className="text-[11px] text-rose-300">
            {saveErrorDetail}
          </p>
        )}
      </form>

      <button
        type="button"
        onClick={() => testMutation.mutate()}
        disabled={testDisabled}
        className="touch-target w-full py-2.5 rounded-xl border border-navy-800 bg-navy-950 text-white font-bold text-xs flex items-center justify-center gap-1.5 disabled:opacity-40"
      >
        {testMutation.isPending ? <LoaderCircle className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
        Test Mesajı Gönder
      </button>
      {chatChangedSincePersist && (
        <p className="text-[10px] text-slate-500">
          Chat ID değişti; test etmeden önce ayarları kaydedin.
        </p>
      )}
      {testMessage && (
        <p
          role={testMessage.kind === "error" ? "alert" : "status"}
          className={`text-[11px] ${testMessage.kind === "error" ? "text-rose-300" : "text-emerald-400"}`}
        >
          {testMessage.text}
        </p>
      )}

      <div className="text-[10px] text-slate-500 space-y-0.5">
        <p>
          Son başarılı gönderim:{" "}
          {config.last_successful_delivery_at
            ? new Date(config.last_successful_delivery_at).toLocaleString("tr-TR")
            : "Henüz yok"}
        </p>
        {config.last_error && <p className="text-rose-400">Son hata: {config.last_error}</p>}
      </div>
    </section>
  );
};
