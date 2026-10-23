import React, { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { X } from "lucide-react";
import { ApiError, api } from "../../api/client";
import {
  CompetitionKind,
  Gender,
  LeagueSummary,
  MappingItem,
  ReviewState,
  SourceIdentityKind,
  UnmatchedItem,
} from "../../types/api";
import { sourceLabel } from "../../features/sources/sourceLabels";

export const IDENTITY_KIND_LABEL: Record<SourceIdentityKind, string> = {
  provider_id: "Sağlayıcı Kimliği",
  derived_text: "Metinden Türetildi",
};

export const REVIEW_STATE_LABEL: Record<ReviewState, string> = {
  approved: "Onaylandı",
  pending: "Beklemede",
  conflict: "Çakışma",
};

export const CONFLICT_REASON_LABEL: Record<string, string> = {
  derived_text_identity: "Kimlik metinden türetildi",
  unresolved_country: "Ülke çözümlenemedi",
  ambiguous_country: "Birden çok ülkeyle eşleşti",
};

export const KIND_LABEL: Record<CompetitionKind, string> = {
  league: "Lig",
  cup: "Kupa",
  qualifier: "Eleme",
  friendly: "Hazırlık",
  international_club: "Uluslararası Kulüp",
  international_national: "Uluslararası Milli Takım",
  unknown: "Bilinmiyor",
};

const CREATABLE_KINDS: CompetitionKind[] = [
  "league",
  "cup",
  "qualifier",
  "friendly",
  "international_club",
  "international_national",
];

const GENDER_LABEL: Record<Gender, string> = {
  men: "Erkekler",
  women: "Kadınlar",
};

// The backend caps a single /api/v1/leagues response at this many rows
// (see _MAX_LEAGUES_LIMIT in src/surebet/web/routes/taxonomy.py). The link
// picker needs the *entire* league universe available to its client-side
// search box, so it pages through with offset until a short page comes back.
const LEAGUE_PAGE_SIZE = 200;

async function fetchAllLeagues(): Promise<LeagueSummary[]> {
  const allLeagues: LeagueSummary[] = [];
  let offset = 0;
  for (;;) {
    const page = await api.getLeagues({
      full_catalog: true,
      limit: LEAGUE_PAGE_SIZE,
      offset,
    });
    allLeagues.push(...page);
    if (page.length < LEAGUE_PAGE_SIZE) {
      break;
    }
    offset += LEAGUE_PAGE_SIZE;
  }
  return allLeagues;
}

type Stage =
  | "browse"
  | "approve-confirm"
  | "link-form"
  | "create-form"
  | "rename-form"
  | "approve-success"
  | "rename-success"
  | "manual-success"
  | "undo-confirm";

interface MappingReviewSheetProps {
  item: UnmatchedItem;
  onClose: () => void;
  onMutated: () => void;
  onConflict: () => void;
}

function describeRawCountry(item: UnmatchedItem): string {
  return item.country_name ?? item.country_alias ?? item.country_id ?? "Ülke bilinmiyor";
}

export const MappingReviewSheet: React.FC<MappingReviewSheetProps> = ({
  item,
  onClose,
  onMutated,
  onConflict,
}) => {
  const [stage, setStage] = useState<Stage>("browse");
  const [pending, setPending] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [mapping, setMapping] = useState<MappingItem | null>(null);

  const [selectedLeagueId, setSelectedLeagueId] = useState<string | null>(null);
  const [leagueQuery, setLeagueQuery] = useState("");

  const [regionId, setRegionId] = useState("");
  const [displayName, setDisplayName] = useState(item.competition_name);
  const [kind, setKind] = useState<CompetitionKind>(
    item.kind === "unknown" ? "league" : item.kind
  );
  const [gender, setGender] = useState<Gender | "">((item.gender as Gender) ?? "");

  const [renameName, setRenameName] = useState(item.target?.display_name ?? "");

  const { data: leagues } = useQuery({
    queryKey: ["leagues", "mapping-link"],
    queryFn: fetchAllLeagues,
    enabled: stage === "link-form",
  });

  const { data: countries } = useQuery({
    queryKey: ["countries", "mapping-create", "full"],
    queryFn: () => api.getCountries({ full_catalog: true }),
    enabled: stage === "create-form",
  });

  const canApprove = item.review_state === "pending" && item.target !== null;
  const canCreate = item.target === null;
  const canRename = item.target !== null;
  const canUndo =
    item.review_state === "approved" &&
    (item.method === "manual" || item.method === "suggestion");

  async function run<T>(action: () => Promise<T>, onOk: (result: T) => void): Promise<void> {
    setErrorMessage(null);
    setPending(true);
    try {
      const result = await action();
      setPending(false);
      onOk(result);
    } catch (error) {
      setPending(false);
      if (error instanceof ApiError && error.status === 409) {
        onConflict();
        return;
      }
      setErrorMessage(error instanceof Error ? error.message : "Beklenmeyen bir hata oluştu.");
    }
  }

  const handleApproveConfirm = () => {
    void run(
      () =>
        api.approveMappingSuggestion(item.source, item.competition_id, {
          expected_updated_at: item.updated_at,
        }),
      (result) => {
        onMutated();
        setMapping(result);
        setStage("approve-success");
      }
    );
  };

  const handleLinkSubmit = () => {
    if (!selectedLeagueId) {
      setErrorMessage("Lütfen bir lig seçin.");
      return;
    }
    void run(
      () =>
        api.linkMapping(item.source, item.competition_id, {
          expected_updated_at: item.updated_at,
          league_id: selectedLeagueId,
        }),
      (result) => {
        onMutated();
        setMapping(result);
        setStage("manual-success");
      }
    );
  };

  const handleCreateSubmit = () => {
    if (!regionId || !displayName.trim()) {
      setErrorMessage("Lütfen bölge ve lig adı girin.");
      return;
    }
    void run(
      () =>
        api.createAndLinkLeague(item.source, item.competition_id, {
          expected_updated_at: item.updated_at,
          region_id: regionId,
          display_name: displayName.trim(),
          kind,
          gender: gender === "" ? null : gender,
        }),
      (result) => {
        onMutated();
        setMapping(result);
        setStage("manual-success");
      }
    );
  };

  const handleRenameSubmit = () => {
    if (!renameName.trim()) {
      setErrorMessage("Lig adı boş olamaz.");
      return;
    }
    if (!item.target) {
      return;
    }
    void run(
      () =>
        api.renameLeague(item.target!.league_id, {
          expected_updated_at: item.target!.updated_at,
          display_name: renameName.trim(),
        }),
      () => {
        onMutated();
        setStage("rename-success");
      }
    );
  };

  const handleUndoConfirm = () => {
    void run(
      () =>
        api.undoMapping(
          item.source,
          item.competition_id,
          mapping?.updated_at ?? item.updated_at
        ),
      () => {
        onMutated();
        onClose();
      }
    );
  };

  const filteredLeagues = (leagues ?? []).filter((league) =>
    league.display_name.toLowerCase().includes(leagueQuery.toLowerCase())
  );

  const resetToBrowse = () => {
    setErrorMessage(null);
    setStage("browse");
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/60"
      role="dialog"
      aria-modal="true"
      aria-label={`${item.competition_name} eşleştirmesini incele`}
    >
      <div className="w-full max-w-md max-h-[85vh] overflow-y-auto bg-navy-950 border-t border-navy-800 rounded-t-2xl p-4 space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-bold text-white">Eşleştirmeyi İncele</h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Kapat"
            className="touch-target flex items-center justify-center text-slate-400"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="space-y-1 text-[11px] text-slate-300 bg-navy-900 rounded-xl border border-navy-850 p-3">
          <p>
            <span className="text-slate-500">Kaynak: </span>
            {sourceLabel(item.source)}
          </p>
          <p>
            <span className="text-slate-500">Ham Ülke/Kategori: </span>
            {describeRawCountry(item)} · {KIND_LABEL[item.kind]}
          </p>
          <p>
            <span className="text-slate-500">Ham Yarışma: </span>
            {item.competition_name}
          </p>
          <p>
            <span className="text-slate-500">Kimlik Türü: </span>
            {IDENTITY_KIND_LABEL[item.identity_kind]}
          </p>
          <p>
            <span className="text-slate-500">Güven: </span>
            {item.confidence !== null ? `%${Math.round(item.confidence * 100)}` : "-"}
          </p>
          <p>
            <span className="text-slate-500">İnceleme Durumu: </span>
            {item.review_state !== null ? REVIEW_STATE_LABEL[item.review_state] : "Eşleştirme yok"}
          </p>
          <p>
            <span className="text-slate-500">Önerilen Hedef: </span>
            {item.target ? item.target.display_name : "Yok"}
          </p>
          {item.conflict_reasons.length > 0 && (
            <p>
              <span className="text-slate-500">Çakışma Nedenleri: </span>
              {item.conflict_reasons.map((reason) => CONFLICT_REASON_LABEL[reason] ?? reason).join(", ")}
            </p>
          )}
        </div>

        {errorMessage && (
          <p role="alert" className="text-xs text-rose-300">
            {errorMessage}
          </p>
        )}

        {stage === "browse" && (
          <div className="space-y-2">
            {canApprove && (
              <button
                type="button"
                onClick={() => setStage("approve-confirm")}
                className="touch-target w-full py-2.5 rounded-xl bg-navy-900 border border-navy-800 text-xs font-semibold text-slate-200"
              >
                Onayla
              </button>
            )}
            <button
              type="button"
              onClick={() => setStage("link-form")}
              className="touch-target w-full py-2.5 rounded-xl bg-navy-900 border border-navy-800 text-xs font-semibold text-slate-200"
            >
              Başka Lige Bağla
            </button>
            {canCreate && (
              <button
                type="button"
                onClick={() => setStage("create-form")}
                className="touch-target w-full py-2.5 rounded-xl bg-navy-900 border border-navy-800 text-xs font-semibold text-slate-200"
              >
                Yeni Lig Oluştur
              </button>
            )}
            {canRename && (
              <button
                type="button"
                onClick={() => setStage("rename-form")}
                className="touch-target w-full py-2.5 rounded-xl bg-navy-900 border border-navy-800 text-xs font-semibold text-slate-200"
              >
                Yeniden Adlandır
              </button>
            )}
            {canUndo && (
              <button
                type="button"
                onClick={() => setStage("undo-confirm")}
                className="touch-target w-full py-2.5 rounded-xl bg-rose-500/10 border border-rose-500/30 text-rose-300 text-xs font-semibold"
              >
                Geri Al
              </button>
            )}
          </div>
        )}

        {stage === "approve-confirm" && (
          <div className="space-y-3">
            <p className="text-xs text-slate-300">
              Bu eşleştirmeyi onaylamak istediğinize emin misiniz?
            </p>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={resetToBrowse}
                className="touch-target flex-1 py-2.5 rounded-xl bg-navy-900 border border-navy-800 text-xs font-semibold text-slate-300"
              >
                Vazgeç
              </button>
              <button
                type="button"
                disabled={pending}
                onClick={handleApproveConfirm}
                className="touch-target flex-1 py-2.5 rounded-xl bg-brand-primary text-navy-950 font-bold text-xs disabled:opacity-60"
              >
                Onayı Kaydet
              </button>
            </div>
          </div>
        )}

        {stage === "link-form" && (
          <div className="space-y-3">
            <input
              type="text"
              value={leagueQuery}
              onChange={(event) => setLeagueQuery(event.target.value)}
              placeholder="Lig ara..."
              className="w-full bg-navy-950 border border-navy-800 rounded-xl p-2.5 text-xs text-white outline-none focus:border-brand-primary"
            />
            <div className="space-y-1 max-h-48 overflow-y-auto">
              {filteredLeagues.map((league) => (
                <button
                  key={league.league_id}
                  type="button"
                  onClick={() => setSelectedLeagueId(league.league_id)}
                  aria-pressed={selectedLeagueId === league.league_id}
                  className={`touch-target w-full text-left px-3 py-2 rounded-lg border text-xs ${
                    selectedLeagueId === league.league_id
                      ? "bg-brand-primary/20 border-brand-primary text-white"
                      : "bg-navy-900 border-navy-850 text-slate-200"
                  }`}
                >
                  {league.display_name}
                </button>
              ))}
            </div>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={resetToBrowse}
                className="touch-target flex-1 py-2.5 rounded-xl bg-navy-900 border border-navy-800 text-xs font-semibold text-slate-300"
              >
                Vazgeç
              </button>
              <button
                type="button"
                disabled={pending}
                onClick={handleLinkSubmit}
                className="touch-target flex-1 py-2.5 rounded-xl bg-brand-primary text-navy-950 font-bold text-xs disabled:opacity-60"
              >
                Bağla
              </button>
            </div>
          </div>
        )}

        {stage === "create-form" && (
          <div className="space-y-3">
            <label className="block space-y-1 text-xs text-slate-300">
              <span>Bölge</span>
              <select
                value={regionId}
                onChange={(event) => setRegionId(event.target.value)}
                className="w-full bg-navy-950 border border-navy-800 rounded-xl p-2.5 text-xs text-white outline-none focus:border-brand-primary"
              >
                <option value="">Seçiniz</option>
                {(countries ?? []).map((country) => (
                  <option key={country.country_id} value={country.country_id}>
                    {country.display_name}
                  </option>
                ))}
              </select>
            </label>
            <label className="block space-y-1 text-xs text-slate-300">
              <span>Lig Adı</span>
              <input
                type="text"
                value={displayName}
                onChange={(event) => setDisplayName(event.target.value)}
                className="w-full bg-navy-950 border border-navy-800 rounded-xl p-2.5 text-xs text-white outline-none focus:border-brand-primary"
              />
            </label>
            <label className="block space-y-1 text-xs text-slate-300">
              <span>Tür</span>
              <select
                value={kind}
                onChange={(event) => setKind(event.target.value as CompetitionKind)}
                className="w-full bg-navy-950 border border-navy-800 rounded-xl p-2.5 text-xs text-white outline-none focus:border-brand-primary"
              >
                {CREATABLE_KINDS.map((value) => (
                  <option key={value} value={value}>
                    {KIND_LABEL[value]}
                  </option>
                ))}
              </select>
            </label>
            <label className="block space-y-1 text-xs text-slate-300">
              <span>Cinsiyet (opsiyonel)</span>
              <select
                value={gender}
                onChange={(event) => setGender(event.target.value as Gender | "")}
                className="w-full bg-navy-950 border border-navy-800 rounded-xl p-2.5 text-xs text-white outline-none focus:border-brand-primary"
              >
                <option value="">Belirtilmemiş</option>
                <option value="men">{GENDER_LABEL.men}</option>
                <option value="women">{GENDER_LABEL.women}</option>
              </select>
            </label>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={resetToBrowse}
                className="touch-target flex-1 py-2.5 rounded-xl bg-navy-900 border border-navy-800 text-xs font-semibold text-slate-300"
              >
                Vazgeç
              </button>
              <button
                type="button"
                disabled={pending}
                onClick={handleCreateSubmit}
                className="touch-target flex-1 py-2.5 rounded-xl bg-brand-primary text-navy-950 font-bold text-xs disabled:opacity-60"
              >
                Oluştur ve Bağla
              </button>
            </div>
          </div>
        )}

        {stage === "rename-form" && (
          <div className="space-y-3">
            <label className="block space-y-1 text-xs text-slate-300">
              <span>Yeni Lig Adı</span>
              <input
                type="text"
                value={renameName}
                onChange={(event) => setRenameName(event.target.value)}
                className="w-full bg-navy-950 border border-navy-800 rounded-xl p-2.5 text-xs text-white outline-none focus:border-brand-primary"
              />
            </label>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={resetToBrowse}
                className="touch-target flex-1 py-2.5 rounded-xl bg-navy-900 border border-navy-800 text-xs font-semibold text-slate-300"
              >
                Vazgeç
              </button>
              <button
                type="button"
                disabled={pending}
                onClick={handleRenameSubmit}
                className="touch-target flex-1 py-2.5 rounded-xl bg-brand-primary text-navy-950 font-bold text-xs disabled:opacity-60"
              >
                Adı Kaydet
              </button>
            </div>
          </div>
        )}

        {stage === "approve-success" && (
          <div className="space-y-3">
            <p className="text-xs text-emerald-300">Eşleştirme onaylandı.</p>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => setStage("undo-confirm")}
                className="touch-target flex-1 py-2.5 rounded-xl bg-rose-500/10 border border-rose-500/30 text-rose-300 text-xs font-semibold"
              >
                Geri Al
              </button>
              <button
                type="button"
                onClick={onClose}
                className="touch-target flex-1 py-2.5 rounded-xl bg-navy-900 border border-navy-800 text-xs font-semibold text-slate-200"
              >
                Kapat
              </button>
            </div>
          </div>
        )}

        {stage === "rename-success" && (
          <div className="space-y-3">
            <p className="text-xs text-emerald-300">Lig adı güncellendi.</p>
            <button
              type="button"
              onClick={onClose}
              className="touch-target w-full py-2.5 rounded-xl bg-navy-900 border border-navy-800 text-xs font-semibold text-slate-200"
            >
              Kapat
            </button>
          </div>
        )}

        {stage === "manual-success" && (
          <div className="space-y-3">
            <p className="text-xs text-emerald-300">Eşleştirme kaydedildi.</p>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => setStage("undo-confirm")}
                className="touch-target flex-1 py-2.5 rounded-xl bg-rose-500/10 border border-rose-500/30 text-rose-300 text-xs font-semibold"
              >
                Geri Al
              </button>
              <button
                type="button"
                onClick={onClose}
                className="touch-target flex-1 py-2.5 rounded-xl bg-navy-900 border border-navy-800 text-xs font-semibold text-slate-200"
              >
                Kapat
              </button>
            </div>
          </div>
        )}

        {stage === "undo-confirm" && (
          <div className="space-y-3">
            <p className="text-xs text-slate-300">
              Bu eşleştirmeyi geri almak istediğinize emin misiniz?
            </p>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() =>
                  setStage(
                    mapping?.method === "suggestion"
                      ? "approve-success"
                      : mapping
                        ? "manual-success"
                        : "browse"
                  )
                }
                className="touch-target flex-1 py-2.5 rounded-xl bg-navy-900 border border-navy-800 text-xs font-semibold text-slate-300"
              >
                Vazgeç
              </button>
              <button
                type="button"
                disabled={pending}
                onClick={handleUndoConfirm}
                className="touch-target flex-1 py-2.5 rounded-xl bg-rose-500 text-navy-950 font-bold text-xs disabled:opacity-60"
              >
                Evet, Geri Al
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
