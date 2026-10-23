import React, { useState, useEffect } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { Save, LogOut, Check, Shield, Clock, DollarSign, GitMerge, ChevronRight, Users, User } from "lucide-react";
import { SystemSettingsUpdate, UserPreferencesUpdate } from "../types/api";
import { TelegramNotificationSettings } from "../components/settings/TelegramNotificationSettings";
import {
  clearOpportunityControls,
  MAX_BUDGET_LIRAS,
  MIN_BUDGET_LIRAS,
} from "../features/opportunities/controlState";

interface SettingsPageProps {
  userId: number;
  username: string | null;
  onLogout: () => void;
  onOpenMappingAdmin: () => void;
}

// Small badges distinguishing controls that only affect the signed-in user
// ("Kişisel" / personal) from controls shared by every authenticated user
// ("Paylaşılan" / shared), matching the backend's split between
// `user_preferences` (per-user) and the `system_settings` singleton.
const PersonalBadge: React.FC = () => (
  <span className="inline-flex items-center gap-1 rounded-full bg-brand-primary/15 text-brand-primary text-[9px] font-bold px-2 py-0.5 uppercase tracking-wide">
    <User className="w-2.5 h-2.5" />
    Kişisel
  </span>
);

const SharedBadge: React.FC = () => (
  <span className="inline-flex items-center gap-1 rounded-full bg-amber-500/15 text-amber-300 text-[9px] font-bold px-2 py-0.5 uppercase tracking-wide">
    <Users className="w-2.5 h-2.5" />
    Paylaşılan
  </span>
);

export const SettingsPage: React.FC<SettingsPageProps> = ({
  userId,
  username,
  onLogout,
  onOpenMappingAdmin,
}) => {
  const queryClient = useQueryClient();

  const { data: settings, isLoading, isError, error, refetch } = useQuery({
    queryKey: ["settings"],
    queryFn: api.getSettings,
  });

  // Personal fields (user_preferences)
  const [budgetLiras, setBudgetLiras] = useState("1000");
  const [budgetError, setBudgetError] = useState<string | null>(null);
  const [includeSourceA, setIncludeSourceA] = useState(false);
  const [personalSavedSuccess, setPersonalSavedSuccess] = useState(false);

  // Shared/system fields (system_settings singleton)
  const [refreshInterval, setRefreshInterval] = useState(30);
  const [stalenessLimit, setStalenessLimit] = useState(75);
  const [systemSavedSuccess, setSystemSavedSuccess] = useState(false);

  useEffect(() => {
    if (settings) {
      setBudgetLiras((settings.personal.default_budget_cents / 100).toString());
      setIncludeSourceA(settings.personal.include_source_a_match_sides);
      setRefreshInterval(settings.system.refresh_interval_seconds);
      setStalenessLimit(settings.system.staleness_limit_seconds);
    }
  }, [settings]);

  // Two intentionally independent mutations, matching the two independent
  // backend endpoints (PUT /settings/personal, PUT /settings/system): a
  // failure in one never rolls back, blocks, or fakes success for the other.
  const personalMutation = useMutation({
    mutationFn: (updates: UserPreferencesUpdate) => api.updatePersonalSettings(updates),
    onSuccess: () => {
      clearOpportunityControls(userId);
      queryClient.invalidateQueries({ queryKey: ["settings"] });
      queryClient.invalidateQueries({ queryKey: ["opportunities"] });
      queryClient.invalidateQueries({ queryKey: ["events"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      setPersonalSavedSuccess(true);
      setTimeout(() => setPersonalSavedSuccess(false), 3000);
    },
  });

  const systemMutation = useMutation({
    mutationFn: (updates: SystemSettingsUpdate) => api.updateSystemSettings(updates),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["settings"] });
      queryClient.invalidateQueries({ queryKey: ["opportunities"] });
      queryClient.invalidateQueries({ queryKey: ["events"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      setSystemSavedSuccess(true);
      setTimeout(() => setSystemSavedSuccess(false), 3000);
    },
  });

  const handlePersonalSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!/^\d+$/.test(budgetLiras)) {
      setBudgetError("10 ile 10.000.000 TL arasında tam sayı girin");
      return;
    }
    const parsedBudget = Number(budgetLiras);
    if (
      !Number.isSafeInteger(parsedBudget) ||
      parsedBudget < MIN_BUDGET_LIRAS ||
      parsedBudget > MAX_BUDGET_LIRAS
    ) {
      setBudgetError("10 ile 10.000.000 TL arasında tam sayı girin");
      return;
    }
    setBudgetError(null);
    personalMutation.mutate({
      default_budget_cents: parsedBudget * 100,
      include_source_a_match_sides: includeSourceA,
    });
  };

  const handleSystemSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    systemMutation.mutate({
      refresh_interval_seconds: refreshInterval,
      staleness_limit_seconds: stalenessLimit,
    });
  };

  if (isLoading) {
    return <div className="h-32 rounded-2xl border border-navy-800 bg-navy-900/60 animate-pulse" />;
  }

  if (isError) {
    return (
      <div role="alert" className="rounded-2xl border border-rose-500/30 bg-rose-500/10 p-5 text-center text-xs text-rose-300">
        <p className="font-semibold">Ayarlar yüklenemedi.</p>
        {error instanceof Error && <p className="mt-1">{error.message}</p>}
        <button type="button" onClick={() => refetch()} className="touch-target mt-3 rounded-xl border border-rose-500/30 px-3 font-semibold">
          Tekrar Dene
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-4 pb-8">
      <div className="flex items-center justify-between gap-2">
        <div>
          <h2 className="text-sm font-bold text-white">Uygulama Ayarları</h2>
          <p className="text-[11px] text-slate-400">
            Varsayılan hesaplama tercihleri ve parametreler
          </p>
        </div>
        {username && (
          <span className="text-[11px] font-semibold text-slate-300 whitespace-nowrap">{username}</span>
        )}
      </div>

      {/* Personal preferences */}
      <form onSubmit={handlePersonalSubmit} className="space-y-4">
        <div className="flex items-center justify-between px-1">
          <h3 className="text-[11px] font-bold text-slate-300 uppercase tracking-wide">Kişisel Tercihler</h3>
          <PersonalBadge />
        </div>

        {/* Default Budget */}
        <section className="bg-navy-900 rounded-2xl border border-navy-800 p-4 space-y-2 shadow-sm">
          <label htmlFor="default-budget" className="text-xs font-bold text-white flex items-center gap-1.5">
            <DollarSign className="w-4 h-4 text-brand-primary" />
            <span>Varsayılan Bütçe (TL)</span>
          </label>
          <p className="text-[11px] text-slate-400">
            Açılışta kullanılacak varsayılan toplam bahis bütçesi.
          </p>
          <input
            id="default-budget"
            type="text"
            inputMode="numeric"
            value={budgetLiras}
            onChange={(e) => {
              setBudgetLiras(e.target.value);
              setBudgetError(null);
            }}
            className="w-full bg-navy-950 border border-navy-800 rounded-xl p-2.5 text-sm font-mono font-bold text-white outline-none focus:border-brand-primary"
          />
          {budgetError && <p role="alert" className="text-[11px] text-rose-300">{budgetError}</p>}
        </section>

        {/* Source A Switch Default */}
        <section className="bg-navy-900 rounded-2xl border border-navy-800 p-4 space-y-2 shadow-sm">
          <div className="flex items-center justify-between">
            <label
              htmlFor="include-source_a"
              className="text-xs font-bold text-white flex items-center gap-1.5"
            >
              <Shield className="w-4 h-4 text-amber-400" />
              <span>Kaynak A Yan Oranları Varsayılanı</span>
            </label>
            <input
              id="include-source_a"
              type="checkbox"
              checked={includeSourceA}
              onChange={(e) => setIncludeSourceA(e.target.checked)}
              className="w-4 h-4 accent-brand-primary rounded"
            />
          </div>
          <p className="text-[11px] text-slate-400">
            Açık olduğunda, Kaynak A MS 1 ve MS 2 oranları da optimizasyona dahil edilir (ancak Kaynak A kuponuna cashback uygulanmaz).
          </p>
        </section>

        {/* Personal Save Button */}
        <button
          type="submit"
          disabled={personalMutation.isPending}
          className="touch-target w-full py-3 rounded-xl bg-brand-primary hover:bg-brand-primary/90 active:scale-[0.99] text-navy-950 font-bold text-xs flex items-center justify-center gap-1.5 shadow-lg transition"
        >
          {personalSavedSuccess ? (
            <>
              <Check className="w-4 h-4" />
              <span>Kişisel Ayarları Kaydedildi!</span>
            </>
          ) : (
            <>
              <Save className="w-4 h-4" />
              <span>Kişisel Ayarları Kaydet</span>
            </>
          )}
        </button>
        {personalMutation.isError && (
          <div role="alert" className="rounded-xl border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-300">
            <p className="font-semibold">Kişisel ayarlar kaydedilemedi.</p>
            {personalMutation.error instanceof Error && <p>{personalMutation.error.message}</p>}
          </div>
        )}
      </form>

      {/* Shared/system settings */}
      <form onSubmit={handleSystemSubmit} className="space-y-4">
        <div className="flex items-center justify-between px-1">
          <h3 className="text-[11px] font-bold text-slate-300 uppercase tracking-wide">Paylaşılan Ayarlar</h3>
          <SharedBadge />
        </div>

        {/* Refresh Interval & Staleness Limit */}
        <section className="bg-navy-900 rounded-2xl border border-navy-800 p-4 space-y-3 shadow-sm">
          <label className="text-xs font-bold text-white flex items-center gap-1.5">
            <Clock className="w-4 h-4 text-brand-primary" />
            <span>Zamanlama ve Bayatlama Sınırları</span>
          </label>
          <p className="text-[11px] text-slate-400">
            Bu ayarlar tüm kullanıcılar için ortaktır; değişiklikler herkesi etkiler.
          </p>

          <div className="space-y-1">
            <div className="flex justify-between text-xs text-slate-300 font-medium">
              <label htmlFor="refresh-interval">Otomatik Toplama Aralığı</label>
              <span className="font-mono">{refreshInterval} sn</span>
            </div>
            <input
              id="refresh-interval"
              type="range"
              min="10"
              max="120"
              step="5"
              value={refreshInterval}
              onChange={(e) => setRefreshInterval(parseInt(e.target.value, 10))}
              className="w-full accent-brand-primary"
            />
          </div>

          <div className="space-y-1 pt-2 border-t border-navy-850">
            <div className="flex justify-between text-xs text-slate-300 font-medium">
              <label htmlFor="staleness-limit">Maksimum Oran Bayatlama Limiti</label>
              <span className="font-mono">{stalenessLimit} sn</span>
            </div>
            <input
              id="staleness-limit"
              type="range"
              min="30"
              max="300"
              step="15"
              value={stalenessLimit}
              onChange={(e) => setStalenessLimit(parseInt(e.target.value, 10))}
              className="w-full accent-brand-primary"
            />
          </div>
        </section>

        {/* System Save Button */}
        <button
          type="submit"
          disabled={systemMutation.isPending}
          className="touch-target w-full py-3 rounded-xl bg-navy-800 hover:bg-navy-750 active:scale-[0.99] border border-amber-500/30 text-amber-300 font-bold text-xs flex items-center justify-center gap-1.5 shadow-lg transition"
        >
          {systemSavedSuccess ? (
            <>
              <Check className="w-4 h-4" />
              <span>Paylaşılan Ayarları Kaydedildi!</span>
            </>
          ) : (
            <>
              <Save className="w-4 h-4" />
              <span>Paylaşılan Ayarları Kaydet</span>
            </>
          )}
        </button>
        {systemMutation.isError && (
          <div role="alert" className="rounded-xl border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-300">
            <p className="font-semibold">Paylaşılan ayarlar kaydedilemedi.</p>
            {systemMutation.error instanceof Error && <p>{systemMutation.error.message}</p>}
          </div>
        )}
      </form>

      <div>
        <div className="flex items-center gap-1.5 px-1 mb-2">
        </div>
        <TelegramNotificationSettings />
      </div>

      {/* Data Mapping Administration Entry */}
      <button
        type="button"
        onClick={onOpenMappingAdmin}
        className="touch-target w-full flex items-center justify-between gap-2 px-4 py-3 rounded-2xl bg-navy-900 border border-navy-800 text-xs font-semibold text-slate-200 hover:text-white transition active:scale-95"
      >
        <span className="flex items-center gap-1.5">
          <GitMerge className="w-4 h-4 text-brand-primary" />
          <span>Veri Eşleştirme</span>
        </span>
        <ChevronRight className="w-4 h-4 text-slate-500" />
      </button>

      {/* Logout Row */}
      <div className="pt-4">
        <button
          onClick={onLogout}
          className="touch-target w-full py-2.5 px-4 rounded-xl bg-rose-500/10 hover:bg-rose-500/20 border border-rose-500/30 text-rose-300 text-xs font-semibold flex items-center justify-center gap-1.5 transition active:scale-95"
        >
          <LogOut className="w-4 h-4" />
          <span>Oturumu Kapat</span>
        </button>
      </div>
    </div>
  );
};
