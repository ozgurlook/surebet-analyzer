import React, { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Wallet,
  Plus,
  ArrowDownLeft,
  ArrowUpRight,
  TrendingUp,
  TrendingDown,
  Search,
  Settings,
  Trash2,
  X,
  RefreshCw,
  Building2,
  Calendar,
  FileText,
  AlertCircle,
  CheckCircle2,
} from "lucide-react";
import clsx from "clsx";
import { api } from "../api/client";
import { BankrollAccount, AccountingTransaction } from "../types/api";
import { MoneyDisplay } from "../components/common/MoneyDisplay";

export const AccountingPage: React.FC = () => {
  const queryClient = useQueryClient();

  // Search and filter state
  const [accountSearch, setAccountSearch] = useState("");
  const [selectedAccountFilter, setSelectedAccountFilter] = useState<number | "all">("all");

  // Modals state
  const [isAccountModalOpen, setIsAccountModalOpen] = useState(false);
  const [editingAccount, setEditingAccount] = useState<BankrollAccount | null>(null);

  const [isTxModalOpen, setIsTxModalOpen] = useState(false);
  const [txAccountId, setTxAccountId] = useState<number | null>(null);
  const [txType, setTxType] = useState<"DEPOSIT" | "WITHDRAWAL">("DEPOSIT");
  const [txAmount, setTxAmount] = useState("");
  const [txDate, setTxDate] = useState("");
  const [txNote, setTxNote] = useState("");

  const [cancelTargetTx, setCancelTargetTx] = useState<AccountingTransaction | null>(null);

  // Queries
  const { data: accounts = [], isLoading: isLoadingAccounts } = useQuery({
    queryKey: ["accounting", "accounts"],
    queryFn: () => api.getAccountingAccounts(false),
  });

  const { data: summary, isLoading: isLoadingSummary } = useQuery({
    queryKey: ["accounting", "summary", selectedAccountFilter],
    queryFn: () =>
      api.getAccountingSummary(
        undefined,
        selectedAccountFilter === "all" ? undefined : selectedAccountFilter
      ),
  });

  const { data: transactions = [], isLoading: isLoadingTxs } = useQuery({
    queryKey: ["accounting", "transactions", selectedAccountFilter],
    queryFn: () =>
      api.getAccountingTransactions(
        100,
        selectedAccountFilter === "all" ? undefined : selectedAccountFilter
      ),
  });

  // Mutations
  const createAccountMutation = useMutation({
    mutationFn: api.createAccountingAccount,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["accounting"] });
      setIsAccountModalOpen(false);
    },
  });

  const updateAccountMutation = useMutation({
    mutationFn: ({ id, data }: { id: number; data: Parameters<typeof api.updateAccountingAccount>[1] }) =>
      api.updateAccountingAccount(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["accounting"] });
      setIsAccountModalOpen(false);
      setEditingAccount(null);
    },
  });

  const deleteAccountMutation = useMutation({
    mutationFn: api.deleteAccountingAccount,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["accounting"] });
      setIsAccountModalOpen(false);
      setEditingAccount(null);
    },
  });

  const recordTxMutation = useMutation({
    mutationFn: api.recordAccountingTransaction,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["accounting"] });
      setIsTxModalOpen(false);
      setTxAmount("");
      setTxNote("");
    },
  });

  const cancelTxMutation = useMutation({
    mutationFn: api.cancelAccountingTransaction,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["accounting"] });
      setCancelTargetTx(null);
    },
  });

  // Derived portfolio figures
  const totalBalanceCents = accounts.reduce((acc, a) => acc + a.current_balance_cents, 0);
  const filteredAccounts = accounts.filter((a) =>
    a.account_name.toLowerCase().includes(accountSearch.toLowerCase())
  );

  const openNewTxModal = (accountId?: number, defaultType: "DEPOSIT" | "WITHDRAWAL" = "DEPOSIT") => {
    setTxAccountId(accountId ?? (accounts.length > 0 ? accounts[0].id : null));
    setTxType(defaultType);
    setTxAmount("");
    setTxNote("");
    setTxDate(new Date().toISOString().split("T")[0]);
    setIsTxModalOpen(true);
  };

  const handleSaveAccount = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const form = e.currentTarget;
    const name = (form.elements.namedItem("name") as HTMLInputElement).value.trim();
    const initialLira = parseFloat((form.elements.namedItem("initial_balance") as HTMLInputElement).value || "0");
    const note = (form.elements.namedItem("note") as HTMLInputElement).value.trim() || null;

    const initialCents = Math.round(initialLira * 100);

    if (editingAccount) {
      updateAccountMutation.mutate({
        id: editingAccount.id,
        data: {
          account_name: name,
          initial_balance_cents: initialCents,
          note,
        },
      });
    } else {
      createAccountMutation.mutate({
        account_name: name,
        initial_balance_cents: initialCents,
        note,
      });
    }
  };

  const handleRecordTx = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const parsedAmount = parseFloat(txAmount.replace(",", "."));
    if (isNaN(parsedAmount) || parsedAmount <= 0) return;

    recordTxMutation.mutate({
      account_id: txAccountId,
      tx_type: txType,
      amount_cents: Math.round(parsedAmount * 100),
      tx_date: txDate || null,
      note: txNote || null,
    });
  };

  return (
    <div className="space-y-6 max-w-7xl mx-auto px-2 sm:px-4 py-4 safe-area-pb">
      {/* 1. Top Header & Global Portfolio Stats */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        {/* Total Bankroll Balance Card */}
        <div className="bg-gradient-to-br from-navy-900 to-navy-950 border border-brand-primary/20 rounded-2xl p-4 sm:p-5 shadow-lg relative overflow-hidden">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2 text-brand-primary font-medium text-xs sm:text-sm">
              <Wallet className="w-4 h-4" />
              <span>Toplam Kasa Portföyü</span>
            </div>
            <span className="text-xs px-2 py-0.5 rounded-full bg-brand-primary/10 text-brand-primary font-semibold">
              {accounts.length} Kaynak B Hesabı
            </span>
          </div>
          <div className="mt-2 flex items-baseline gap-2">
            <MoneyDisplay
              cents={totalBalanceCents}
              size="xl"
              className="text-2xl sm:text-3xl text-slate-100"
            />
          </div>
          <div className="mt-3 flex items-center gap-2">
            <button
              onClick={() => {
                setEditingAccount(null);
                setIsAccountModalOpen(true);
              }}
              className="touch-target inline-flex items-center gap-1.5 px-3 py-1.5 bg-brand-primary/15 hover:bg-brand-primary/25 text-brand-primary text-xs font-semibold rounded-lg transition"
            >
              <Plus className="w-3.5 h-3.5" />
              <span>Hesap Ekle</span>
            </button>
            <button
              onClick={() => openNewTxModal()}
              className="touch-target inline-flex items-center gap-1.5 px-3 py-1.5 bg-emerald-500/15 hover:bg-emerald-500/25 text-emerald-400 text-xs font-semibold rounded-lg transition"
            >
              <Plus className="w-3.5 h-3.5" />
              <span>İşlem Gir</span>
            </button>
          </div>
        </div>

        {/* Today's Cashflow / Net Profit Card */}
        <div className="bg-navy-900/80 border border-navy-800 rounded-2xl p-4 sm:p-5 shadow-lg flex flex-col justify-between">
          <div className="flex items-center justify-between text-xs text-slate-400 font-medium">
            <span>Bugünkü Net Kâr/Zarar</span>
            {summary && (
              <span
                className={clsx(
                  "px-2 py-0.5 rounded-full font-bold inline-flex items-center gap-1",
                  summary.daily.net_profit_cents >= 0
                    ? "bg-emerald-500/10 text-emerald-400"
                    : "bg-rose-500/10 text-rose-400"
                )}
              >
                {summary.daily.net_profit_cents >= 0 ? (
                  <TrendingUp className="w-3 h-3" />
                ) : (
                  <TrendingDown className="w-3 h-3" />
                )}
                {summary.daily.net_profit_cents >= 0 ? "KÂRDA" : "ZARARDA"}
              </span>
            )}
          </div>
          <div className="mt-2">
            <MoneyDisplay
              cents={summary?.daily.net_profit_cents ?? 0}
              showSign
              size="xl"
              className={clsx(
                "text-2xl",
                (summary?.daily.net_profit_cents ?? 0) >= 0 ? "text-emerald-400" : "text-rose-400"
              )}
            />
          </div>
          <div className="mt-3 text-xs text-slate-400 flex items-center justify-between border-t border-navy-800 pt-2">
            <span>
              Yatırım:{" "}
              <MoneyDisplay
                cents={summary?.daily.total_deposit_cents ?? 0}
                size="sm"
                className="text-slate-300 font-semibold"
              />
            </span>
            <span>
              Çekim:{" "}
              <MoneyDisplay
                cents={summary?.daily.total_withdrawal_cents ?? 0}
                size="sm"
                className="text-slate-300 font-semibold"
              />
            </span>
          </div>
        </div>

        {/* Weekly Cashflow / Net Profit Card */}
        <div className="bg-navy-900/80 border border-navy-800 rounded-2xl p-4 sm:p-5 shadow-lg flex flex-col justify-between">
          <div className="flex items-center justify-between text-xs text-slate-400 font-medium">
            <span>Bu Haftaki Net Kâr/Zarar</span>
            {summary && (
              <span className="text-[11px] text-slate-400">
                {summary.weekly.week_start_str.slice(5)} - {summary.weekly.week_end_str.slice(5)}
              </span>
            )}
          </div>
          <div className="mt-2">
            <MoneyDisplay
              cents={summary?.weekly.net_profit_cents ?? 0}
              showSign
              size="xl"
              className={clsx(
                "text-2xl",
                (summary?.weekly.net_profit_cents ?? 0) >= 0 ? "text-emerald-400" : "text-rose-400"
              )}
            />
          </div>
          <div className="mt-3 text-xs text-slate-400 flex items-center justify-between border-t border-navy-800 pt-2">
            <span>
              Haftalık Yatırım:{" "}
              <MoneyDisplay
                cents={summary?.weekly.total_deposit_cents ?? 0}
                size="sm"
                className="text-slate-300 font-semibold"
              />
            </span>
            <span>
              Haftalık Çekim:{" "}
              <MoneyDisplay
                cents={summary?.weekly.total_withdrawal_cents ?? 0}
                size="sm"
                className="text-slate-300 font-semibold"
              />
            </span>
          </div>
        </div>
      </div>

      {/* 2. Source B Accounts Grid Section */}
      <div className="space-y-3">
        <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <Building2 className="w-5 h-5 text-brand-primary" />
            <h2 className="text-base sm:text-lg font-bold text-slate-100">
              Kaynak B Hesapları ({accounts.length})
            </h2>
          </div>

          {/* Quick Search across the 20-30 accounts */}
          <div className="relative w-full sm:w-64">
            <Search className="w-4 h-4 text-slate-400 absolute left-3 top-2.5" />
            <input
              type="text"
              placeholder="Hesap ara (örn: Hesap 1, VIP)..."
              value={accountSearch}
              onChange={(e) => setAccountSearch(e.target.value)}
              className="w-full bg-navy-900 border border-navy-750 text-slate-200 text-xs rounded-xl pl-9 pr-3 py-2 placeholder-slate-500 focus:outline-none focus:border-brand-primary"
            />
          </div>
        </div>

        {isLoadingAccounts ? (
          <div className="p-8 text-center text-slate-400 text-sm">
            <RefreshCw className="w-6 h-6 animate-spin mx-auto mb-2 text-brand-primary" />
            Hesaplar yükleniyor...
          </div>
        ) : filteredAccounts.length === 0 ? (
          <div className="bg-navy-900/60 border border-dashed border-navy-800 rounded-2xl p-8 text-center">
            <Wallet className="w-10 h-10 mx-auto text-slate-600 mb-3" />
            <p className="text-slate-300 text-sm font-semibold">Henüz Kaynak B Hesabı Bulunmuyor</p>
            <p className="text-slate-500 text-xs mt-1">
              Yaklaşık 20-30 hesabınızı sisteme tanımlayarak her birinin bakiyesini ve kâr/zararını ayrı ayrı takip edebilirsiniz.
            </p>
            <button
              onClick={() => {
                setEditingAccount(null);
                setIsAccountModalOpen(true);
              }}
              className="mt-4 px-4 py-2 bg-brand-primary text-slate-950 font-bold text-xs rounded-xl hover:opacity-90 transition"
            >
              + İlk Hesabı Ekle
            </button>
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3">
            {filteredAccounts.map((acc) => {
              const isProfit = acc.net_profit_cents >= 0;
              return (
                <div
                  key={acc.id}
                  className="bg-navy-900 border border-navy-800 hover:border-navy-700 rounded-2xl p-4 transition shadow-md flex flex-col justify-between"
                >
                  <div>
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <span className="w-2.5 h-2.5 rounded-full bg-emerald-400" />
                        <h3 className="font-bold text-slate-200 text-sm truncate">{acc.account_name}</h3>
                      </div>
                      <button
                        onClick={() => {
                          setEditingAccount(acc);
                          setIsAccountModalOpen(true);
                        }}
                        className="text-slate-400 hover:text-slate-200 p-1 rounded-lg hover:bg-navy-800 transition"
                        title="Hesabı Düzenle"
                      >
                        <Settings className="w-3.5 h-3.5" />
                      </button>
                    </div>
                    {acc.note && (
                      <p className="text-[11px] text-slate-400 mt-1 line-clamp-1 italic">
                        {acc.note}
                      </p>
                    )}

                    {/* Current Balance */}
                    <div className="mt-3">
                      <span className="text-[11px] text-slate-400 uppercase tracking-wider block">
                        Güncel Kasa Bakiyesi
                      </span>
                      <MoneyDisplay
                        cents={acc.current_balance_cents}
                        size="lg"
                        className="text-lg font-bold text-slate-100 mt-0.5"
                      />
                    </div>

                    {/* Deposit, Withdrawal & Net Profit */}
                    <div className="mt-3 bg-navy-950/60 rounded-xl p-2.5 space-y-1 text-xs">
                      <div className="flex justify-between text-slate-400">
                        <span>Toplam Yatırım:</span>
                        <MoneyDisplay cents={acc.total_deposit_cents} size="sm" className="text-slate-300" />
                      </div>
                      <div className="flex justify-between text-slate-400">
                        <span>Toplam Çekim:</span>
                        <MoneyDisplay cents={acc.total_withdrawal_cents} size="sm" className="text-slate-300" />
                      </div>
                      <div className="flex justify-between font-semibold border-t border-navy-800 pt-1">
                        <span className="text-slate-300">Net K/Z:</span>
                        <MoneyDisplay
                          cents={acc.net_profit_cents}
                          showSign
                          size="sm"
                          className={isProfit ? "text-emerald-400" : "text-rose-400"}
                        />
                      </div>
                    </div>
                  </div>

                  {/* Fast Action Buttons */}
                  <div className="mt-3 grid grid-cols-2 gap-2 pt-2 border-t border-navy-850">
                    <button
                      onClick={() => openNewTxModal(acc.id, "DEPOSIT")}
                      className="touch-target flex items-center justify-center gap-1 py-1.5 px-2 bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-400 rounded-lg text-xs font-semibold transition"
                    >
                      <ArrowDownLeft className="w-3.5 h-3.5" />
                      <span>+ Yatır</span>
                    </button>
                    <button
                      onClick={() => openNewTxModal(acc.id, "WITHDRAWAL")}
                      className="touch-target flex items-center justify-center gap-1 py-1.5 px-2 bg-rose-500/10 hover:bg-rose-500/20 text-rose-400 rounded-lg text-xs font-semibold transition"
                    >
                      <ArrowUpRight className="w-3.5 h-3.5" />
                      <span>- Çek</span>
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* 3. Transaction History Section */}
      <div className="bg-navy-900 border border-navy-800 rounded-2xl p-4 sm:p-5 shadow-lg space-y-4">
        <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 border-b border-navy-800 pb-3">
          <div className="flex items-center gap-2">
            <FileText className="w-5 h-5 text-brand-primary" />
            <h3 className="font-bold text-slate-100 text-sm sm:text-base">Kasa İşlem Geçmişi</h3>
          </div>

          {/* Filter by specific account */}
          <div className="flex items-center gap-2 w-full sm:w-auto">
            <span className="text-xs text-slate-400 whitespace-nowrap">Filtre:</span>
            <select
              value={selectedAccountFilter}
              onChange={(e) =>
                setSelectedAccountFilter(e.target.value === "all" ? "all" : Number(e.target.value))
              }
              className="bg-navy-950 border border-navy-750 text-slate-200 text-xs rounded-xl px-3 py-1.5 focus:outline-none focus:border-brand-primary"
            >
              <option value="all">Tüm Kaynak B Hesapları</option>
              {accounts.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.account_name}
                </option>
              ))}
            </select>
          </div>
        </div>

        {isLoadingTxs ? (
          <div className="p-8 text-center text-slate-400 text-sm">
            <RefreshCw className="w-6 h-6 animate-spin mx-auto mb-2 text-brand-primary" />
            İşlemler yükleniyor...
          </div>
        ) : transactions.length === 0 ? (
          <div className="p-8 text-center text-slate-500 text-xs">
            Kayıtlı işlem bulunamadı. Yukarıdaki butonlarla veya Telegram üzerinden yatırım/çekim kaydedebilirsiniz.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs text-slate-300">
              <thead className="text-[11px] uppercase bg-navy-950/60 text-slate-400 border-b border-navy-800">
                <tr>
                  <th className="py-2.5 px-3">İşlem</th>
                  <th className="py-2.5 px-3">Hesap</th>
                  <th className="py-2.5 px-3">Tarih</th>
                  <th className="py-2.5 px-3">Tutar</th>
                  <th className="py-2.5 px-3">Açıklama</th>
                  <th className="py-2.5 px-3">Durum</th>
                  <th className="py-2.5 px-3 text-right">Aksiyon</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-navy-850">
                {transactions.map((tx) => {
                  const isDeposit = tx.tx_type === "DEPOSIT";
                  const isCancelled = tx.status === "CANCELLED";
                  const account = accounts.find((a) => a.id === tx.account_id);

                  return (
                    <tr
                      key={tx.id}
                      className={clsx(
                        "hover:bg-navy-850/50 transition",
                        isCancelled && "opacity-50 line-through"
                      )}
                    >
                      <td className="py-2.5 px-3 font-semibold">
                        <span
                          className={clsx(
                            "inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-bold",
                            isDeposit
                              ? "bg-emerald-500/10 text-emerald-400"
                              : "bg-rose-500/10 text-rose-400"
                          )}
                        >
                          {isDeposit ? "+" : "-"} {isDeposit ? "Yatırım" : "Çekim"}
                        </span>
                      </td>
                      <td className="py-2.5 px-3 font-medium text-slate-200">
                        {account?.account_name ?? (tx.account_id ? `#${tx.account_id}` : "Genel Kasa")}
                      </td>
                      <td className="py-2.5 px-3 text-slate-400 font-mono">{tx.tx_date}</td>
                      <td className="py-2.5 px-3 font-bold font-mono">
                        <MoneyDisplay cents={tx.amount_cents} />
                      </td>
                      <td className="py-2.5 px-3 text-slate-400 max-w-[200px] truncate">
                        {tx.note || "-"}
                      </td>
                      <td className="py-2.5 px-3">
                        {isCancelled ? (
                          <span className="text-rose-400 text-[11px] font-medium inline-flex items-center gap-1">
                            <AlertCircle className="w-3 h-3" /> İptal Edildi
                          </span>
                        ) : (
                          <span className="text-emerald-400 text-[11px] font-medium inline-flex items-center gap-1">
                            <CheckCircle2 className="w-3 h-3" /> Aktif
                          </span>
                        )}
                      </td>
                      <td className="py-2.5 px-3 text-right">
                        {!isCancelled && (
                          <button
                            onClick={() => setCancelTargetTx(tx)}
                            className="text-xs text-rose-400 hover:text-rose-300 font-semibold px-2 py-1 rounded bg-rose-500/10 hover:bg-rose-500/20 transition"
                          >
                            İptal Et
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* MODAL 1: Account Create / Edit Modal */}
      {isAccountModalOpen && (
        <div className="fixed inset-0 z-50 bg-black/75 backdrop-blur-sm flex items-center justify-center p-4">
          <div className="bg-navy-900 border border-navy-750 rounded-2xl w-full max-w-md p-5 shadow-2xl space-y-4">
            <div className="flex items-center justify-between border-b border-navy-800 pb-3">
              <h3 className="text-base font-bold text-slate-100 flex items-center gap-2">
                <Building2 className="w-4 h-4 text-brand-primary" />
                {editingAccount ? "Hesabı Düzenle" : "Yeni Kaynak B Hesabı Ekle"}
              </h3>
              <button
                onClick={() => setIsAccountModalOpen(false)}
                className="text-slate-400 hover:text-slate-200 p-1 rounded-lg"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <form onSubmit={handleSaveAccount} className="space-y-3">
              <div>
                <label className="block text-xs font-medium text-slate-300 mb-1">
                  Hesap Adı / Etiketi *
                </label>
                <input
                  name="name"
                  type="text"
                  required
                  defaultValue={editingAccount?.account_name || ""}
                  placeholder="Örn: Hesap 1, Hesap 25, VIP..."
                  className="w-full bg-navy-950 border border-navy-750 text-slate-200 text-sm rounded-xl px-3 py-2 focus:outline-none focus:border-brand-primary"
                />
              </div>

              <div>
                <label className="block text-xs font-medium text-slate-300 mb-1">
                  Başlangıç Kasası / Bakiyesi (₺)
                </label>
                <input
                  name="initial_balance"
                  type="number"
                  step="0.01"
                  min="0"
                  defaultValue={
                    editingAccount ? (editingAccount.initial_balance_cents / 100).toString() : "0"
                  }
                  placeholder="0.00"
                  className="w-full bg-navy-950 border border-navy-750 text-slate-200 text-sm rounded-xl px-3 py-2 focus:outline-none focus:border-brand-primary"
                />
              </div>

              <div>
                <label className="block text-xs font-medium text-slate-300 mb-1">
                  Not / Açıklama (İsteğe Bağlı)
                </label>
                <input
                  name="note"
                  type="text"
                  defaultValue={editingAccount?.note || ""}
                  placeholder="Örn: Ana hesap, yedek limit..."
                  className="w-full bg-navy-950 border border-navy-750 text-slate-200 text-sm rounded-xl px-3 py-2 focus:outline-none focus:border-brand-primary"
                />
              </div>

              <div className="pt-3 flex items-center justify-between border-t border-navy-800">
                {editingAccount ? (
                  <button
                    type="button"
                    onClick={() => {
                      if (window.confirm(`${editingAccount.account_name} hesabını silmek istediğinize emin misiniz?`)) {
                        deleteAccountMutation.mutate(editingAccount.id);
                      }
                    }}
                    className="inline-flex items-center gap-1.5 text-xs text-rose-400 hover:text-rose-300 font-semibold px-3 py-2 rounded-xl bg-rose-500/10 hover:bg-rose-500/20 transition"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                    <span>Sil</span>
                  </button>
                ) : (
                  <div />
                )}

                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => setIsAccountModalOpen(false)}
                    className="px-4 py-2 text-xs font-medium text-slate-400 hover:text-slate-200"
                  >
                    İptal
                  </button>
                  <button
                    type="submit"
                    disabled={createAccountMutation.isPending || updateAccountMutation.isPending}
                    className="px-4 py-2 bg-brand-primary text-slate-950 font-bold text-xs rounded-xl hover:opacity-90 transition disabled:opacity-50"
                  >
                    Kaydet
                  </button>
                </div>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* MODAL 2: Record Transaction Modal */}
      {isTxModalOpen && (
        <div className="fixed inset-0 z-50 bg-black/75 backdrop-blur-sm flex items-center justify-center p-4">
          <div className="bg-navy-900 border border-navy-750 rounded-2xl w-full max-w-md p-5 shadow-2xl space-y-4">
            <div className="flex items-center justify-between border-b border-navy-800 pb-3">
              <h3 className="text-base font-bold text-slate-100 flex items-center gap-2">
                <Plus className="w-4 h-4 text-brand-primary" />
                <span>İşlem Gir</span>
              </h3>
              <button
                onClick={() => setIsTxModalOpen(false)}
                className="text-slate-400 hover:text-slate-200 p-1 rounded-lg"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <form onSubmit={handleRecordTx} className="space-y-3">
              {/* Account Selection */}
              <div>
                <label className="block text-xs font-medium text-slate-300 mb-1">
                  Kaynak B Hesabı *
                </label>
                <select
                  value={txAccountId ?? ""}
                  onChange={(e) => setTxAccountId(e.target.value ? Number(e.target.value) : null)}
                  className="w-full bg-navy-950 border border-navy-750 text-slate-200 text-sm rounded-xl px-3 py-2 focus:outline-none focus:border-brand-primary"
                >
                  {accounts.map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.account_name} (Bakiye: {(a.current_balance_cents / 100).toLocaleString("tr-TR")} ₺)
                    </option>
                  ))}
                </select>
              </div>

              {/* Transaction Type Tabs */}
              <div>
                <label className="block text-xs font-medium text-slate-300 mb-1">İşlem Türü</label>
                <div className="grid grid-cols-2 gap-2">
                  <button
                    type="button"
                    onClick={() => setTxType("DEPOSIT")}
                    className={clsx(
                      "py-2 px-3 rounded-xl font-bold text-xs flex items-center justify-center gap-1.5 transition",
                      txType === "DEPOSIT"
                        ? "bg-emerald-500 text-slate-950"
                        : "bg-navy-950 text-slate-400 hover:text-slate-200"
                    )}
                  >
                    <ArrowDownLeft className="w-4 h-4" />
                    + Yatırım (Deposit)
                  </button>
                  <button
                    type="button"
                    onClick={() => setTxType("WITHDRAWAL")}
                    className={clsx(
                      "py-2 px-3 rounded-xl font-bold text-xs flex items-center justify-center gap-1.5 transition",
                      txType === "WITHDRAWAL"
                        ? "bg-rose-500 text-slate-950"
                        : "bg-navy-950 text-slate-400 hover:text-slate-200"
                    )}
                  >
                    <ArrowUpRight className="w-4 h-4" />
                    - Çekim (Withdrawal)
                  </button>
                </div>
              </div>

              {/* Amount */}
              <div>
                <label className="block text-xs font-medium text-slate-300 mb-1">Tutar (₺) *</label>
                <input
                  type="text"
                  required
                  value={txAmount}
                  onChange={(e) => setTxAmount(e.target.value)}
                  placeholder="Örn: 5000 veya 1250.50"
                  className="w-full bg-navy-950 border border-navy-750 text-slate-200 text-base font-mono font-bold rounded-xl px-3 py-2 focus:outline-none focus:border-brand-primary"
                />
              </div>

              {/* Date */}
              <div>
                <label className="block text-xs font-medium text-slate-300 mb-1">
                  Tarih (Varsayılan Bugün)
                </label>
                <div className="relative">
                  <Calendar className="w-4 h-4 text-slate-400 absolute left-3 top-2.5" />
                  <input
                    type="date"
                    value={txDate}
                    onChange={(e) => setTxDate(e.target.value)}
                    className="w-full bg-navy-950 border border-navy-750 text-slate-200 text-sm rounded-xl pl-9 pr-3 py-2 focus:outline-none focus:border-brand-primary"
                  />
                </div>
              </div>

              {/* Note */}
              <div>
                <label className="block text-xs font-medium text-slate-300 mb-1">
                  Açıklama / Not (İsteğe Bağlı)
                </label>
                <input
                  type="text"
                  value={txNote}
                  onChange={(e) => setTxNote(e.target.value)}
                  placeholder="Örn: Papara, QR kod, bonus..."
                  className="w-full bg-navy-950 border border-navy-750 text-slate-200 text-sm rounded-xl px-3 py-2 focus:outline-none focus:border-brand-primary"
                />
              </div>

              <div className="pt-3 flex items-center justify-end gap-2 border-t border-navy-800">
                <button
                  type="button"
                  onClick={() => setIsTxModalOpen(false)}
                  className="px-4 py-2 text-xs font-medium text-slate-400 hover:text-slate-200"
                >
                  Vazgeç
                </button>
                <button
                  type="submit"
                  disabled={recordTxMutation.isPending}
                  className={clsx(
                    "px-4 py-2 font-bold text-xs rounded-xl hover:opacity-90 transition disabled:opacity-50",
                    txType === "DEPOSIT" ? "bg-emerald-500 text-slate-950" : "bg-rose-500 text-slate-950"
                  )}
                >
                  {txType === "DEPOSIT" ? "Yatırımı Kaydet" : "Çekimi Kaydet"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* MODAL 3: Cancel Transaction Confirmation */}
      {cancelTargetTx && (
        <div className="fixed inset-0 z-50 bg-black/75 backdrop-blur-sm flex items-center justify-center p-4">
          <div className="bg-navy-900 border border-navy-750 rounded-2xl w-full max-w-sm p-5 shadow-2xl space-y-4">
            <h3 className="text-base font-bold text-slate-100 flex items-center gap-2">
              <AlertCircle className="w-5 h-5 text-rose-400" />
              İşlemi İptal Et
            </h3>
            <p className="text-xs text-slate-300">
              #{cancelTargetTx.id} numaralı{" "}
              <b>
                {cancelTargetTx.tx_type === "DEPOSIT" ? "Yatırım" : "Çekim"} (
                <MoneyDisplay cents={cancelTargetTx.amount_cents} size="sm" />)
              </b>{" "}
              işlemini iptal etmek istediğinize emin misiniz? İptal edilen işlem kasa bakiyesinden anında düşülecektir.
            </p>
            <div className="pt-2 flex items-center justify-end gap-2">
              <button
                onClick={() => setCancelTargetTx(null)}
                className="px-4 py-2 text-xs font-medium text-slate-400 hover:text-slate-200"
              >
                Vazgeç
              </button>
              <button
                onClick={() => cancelTxMutation.mutate(cancelTargetTx.id)}
                disabled={cancelTxMutation.isPending}
                className="px-4 py-2 bg-rose-500 text-slate-950 font-bold text-xs rounded-xl hover:opacity-90 transition disabled:opacity-50"
              >
                Evet, İptal Et
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
