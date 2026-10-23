import React, { useState } from "react";
import { Activity, Lock, User, KeyRound, ArrowRight, ShieldCheck } from "lucide-react";
import clsx from "clsx";
import { api } from "../api/client";

interface LoginPageProps {
  onLoginSuccess: () => void;
  registrationEnabled: boolean;
}

type AuthTab = "login" | "register";

export const LoginPage: React.FC<LoginPageProps> = ({ onLoginSuccess, registrationEnabled }) => {
  const [activeTab, setActiveTab] = useState<AuthTab>("login");

  const [loginUsername, setLoginUsername] = useState("");
  const [loginPassword, setLoginPassword] = useState("");

  const [registerUsername, setRegisterUsername] = useState("");
  const [registerPassword, setRegisterPassword] = useState("");
  const [registrationCode, setRegistrationCode] = useState("");

  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  // The register tab only ever renders when registration is enabled, but this
  // guard also protects against submitting a stale register form if the tab
  // somehow stays selected after registration is disabled mid-session.
  const showRegisterTab = registrationEnabled;
  const isLoginTab = activeTab === "login" || !showRegisterTab;

  const selectTab = (tab: AuthTab) => {
    setActiveTab(tab);
    setError(null);
  };

  const handleLoginSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!loginUsername || !loginPassword) return;

    setError(null);
    setIsSubmitting(true);
    try {
      await api.login(loginUsername, loginPassword);
      onLoginSuccess();
    } catch (err: unknown) {
      setError((err as Error).message || "Giriş başarısız. Lütfen bilgilerinizi kontrol edin.");
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleRegisterSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!registerUsername || !registerPassword || !registrationCode) return;

    setError(null);
    setIsSubmitting(true);
    try {
      await api.register(registerUsername, registerPassword, registrationCode);
      onLoginSuccess();
    } catch (err: unknown) {
      setError((err as Error).message || "Kayıt başarısız. Lütfen bilgilerinizi kontrol edin.");
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="min-h-screen bg-navy-950 flex flex-col justify-center px-4 max-w-sm mx-auto w-full overflow-x-hidden">
      <div className="text-center mb-8">
        <div className="w-14 h-14 rounded-2xl bg-gradient-to-br from-brand-primary/20 to-blue-600/30 border border-brand-primary/40 flex items-center justify-center mx-auto mb-3 shadow-xl">
          <Activity className="w-7 h-7 text-brand-primary" />
        </div>
        <h1 className="text-xl font-bold text-white tracking-tight">Surebet Analyzer</h1>
        <p className="text-xs text-slate-400 mt-1">Hesabınıza erişmek için giriş yapın</p>
      </div>

      <div className="bg-navy-900 rounded-2xl border border-navy-800 shadow-2xl overflow-hidden">
        {showRegisterTab && (
          <div className="flex" role="tablist">
            <button
              type="button"
              role="tab"
              aria-selected={isLoginTab}
              onClick={() => selectTab("login")}
              className={clsx(
                "touch-target flex-1 text-xs font-bold transition",
                isLoginTab
                  ? "bg-brand-primary/15 text-brand-primary border-b-2 border-brand-primary"
                  : "text-slate-400 border-b border-navy-800"
              )}
            >
              Giriş Yap
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={!isLoginTab}
              onClick={() => selectTab("register")}
              className={clsx(
                "touch-target flex-1 text-xs font-bold transition",
                !isLoginTab
                  ? "bg-brand-primary/15 text-brand-primary border-b-2 border-brand-primary"
                  : "text-slate-400 border-b border-navy-800"
              )}
            >
              Kayıt Ol
            </button>
          </div>
        )}

        <div className="p-5">
          {isLoginTab ? (
            <form onSubmit={handleLoginSubmit} className="space-y-4">
              <div>
                <label
                  htmlFor="login-username"
                  className="text-xs font-semibold text-slate-300 block mb-1.5 flex items-center gap-1"
                >
                  <User className="w-3.5 h-3.5 text-brand-primary" />
                  <span>Kullanıcı Adı</span>
                </label>
                <input
                  id="login-username"
                  type="text"
                  value={loginUsername}
                  onChange={(e) => setLoginUsername(e.target.value)}
                  placeholder="kullanici_adi"
                  autoComplete="username"
                  autoFocus
                  disabled={isSubmitting}
                  className="w-full bg-navy-950 border border-navy-800 rounded-xl p-3 text-sm text-white placeholder-slate-600 outline-none focus:border-brand-primary transition disabled:opacity-50"
                />
              </div>

              <div>
                <label
                  htmlFor="login-password"
                  className="text-xs font-semibold text-slate-300 block mb-1.5 flex items-center gap-1"
                >
                  <Lock className="w-3.5 h-3.5 text-brand-primary" />
                  <span>Parola</span>
                </label>
                <input
                  id="login-password"
                  type="password"
                  value={loginPassword}
                  onChange={(e) => setLoginPassword(e.target.value)}
                  placeholder="••••••••"
                  autoComplete="current-password"
                  disabled={isSubmitting}
                  className="w-full bg-navy-950 border border-navy-800 rounded-xl p-3 text-sm text-white placeholder-slate-600 outline-none focus:border-brand-primary transition disabled:opacity-50"
                />
              </div>

              {error && (
                <div role="alert" className="p-2.5 rounded-xl bg-rose-500/10 border border-rose-500/30 text-xs text-rose-300">
                  {error}
                </div>
              )}

              <button
                type="submit"
                disabled={isSubmitting || !loginUsername || !loginPassword}
                className="touch-target w-full py-3 rounded-xl bg-brand-primary hover:bg-brand-primary/90 active:scale-[0.99] text-navy-950 font-bold text-xs flex items-center justify-center gap-1.5 shadow-lg transition disabled:opacity-50"
              >
                <span>{isSubmitting ? "Doğrulanıyor..." : "Giriş Yap"}</span>
                <ArrowRight className="w-4 h-4" />
              </button>
            </form>
          ) : (
            <form onSubmit={handleRegisterSubmit} className="space-y-4">
              <div>
                <label
                  htmlFor="register-username"
                  className="text-xs font-semibold text-slate-300 block mb-1.5 flex items-center gap-1"
                >
                  <User className="w-3.5 h-3.5 text-brand-primary" />
                  <span>Kullanıcı Adı</span>
                </label>
                <input
                  id="register-username"
                  type="text"
                  value={registerUsername}
                  onChange={(e) => setRegisterUsername(e.target.value)}
                  placeholder="kullanici_adi"
                  autoComplete="username"
                  autoFocus
                  disabled={isSubmitting}
                  className="w-full bg-navy-950 border border-navy-800 rounded-xl p-3 text-sm text-white placeholder-slate-600 outline-none focus:border-brand-primary transition disabled:opacity-50"
                />
              </div>

              <div>
                <label
                  htmlFor="register-password"
                  className="text-xs font-semibold text-slate-300 block mb-1.5 flex items-center gap-1"
                >
                  <Lock className="w-3.5 h-3.5 text-brand-primary" />
                  <span>Yeni Parola</span>
                </label>
                <input
                  id="register-password"
                  type="password"
                  value={registerPassword}
                  onChange={(e) => setRegisterPassword(e.target.value)}
                  placeholder="••••••••"
                  autoComplete="new-password"
                  disabled={isSubmitting}
                  className="w-full bg-navy-950 border border-navy-800 rounded-xl p-3 text-sm text-white placeholder-slate-600 outline-none focus:border-brand-primary transition disabled:opacity-50"
                />
              </div>

              <div>
                <label
                  htmlFor="register-code"
                  className="text-xs font-semibold text-slate-300 block mb-1.5 flex items-center gap-1"
                >
                  <KeyRound className="w-3.5 h-3.5 text-brand-primary" />
                  <span>Kayıt Kodu</span>
                </label>
                <input
                  id="register-code"
                  type="text"
                  value={registrationCode}
                  onChange={(e) => setRegistrationCode(e.target.value)}
                  placeholder="Davet kodu"
                  autoComplete="off"
                  disabled={isSubmitting}
                  className="w-full bg-navy-950 border border-navy-800 rounded-xl p-3 text-sm text-white placeholder-slate-600 outline-none focus:border-brand-primary transition disabled:opacity-50"
                />
              </div>

              {error && (
                <div role="alert" className="p-2.5 rounded-xl bg-rose-500/10 border border-rose-500/30 text-xs text-rose-300">
                  {error}
                </div>
              )}

              <button
                type="submit"
                disabled={isSubmitting || !registerUsername || !registerPassword || !registrationCode}
                className="touch-target w-full py-3 rounded-xl bg-brand-primary hover:bg-brand-primary/90 active:scale-[0.99] text-navy-950 font-bold text-xs flex items-center justify-center gap-1.5 shadow-lg transition disabled:opacity-50"
              >
                <span>{isSubmitting ? "Kaydediliyor..." : "Kayıt Ol"}</span>
                <ArrowRight className="w-4 h-4" />
              </button>
            </form>
          )}
        </div>
      </div>

      <div className="mt-8 text-center text-[11px] text-slate-500 flex items-center justify-center gap-1">
        <ShieldCheck className="w-3.5 h-3.5 text-emerald-500" />
        <span>Argon2id Korumalı Güvenli Oturum</span>
      </div>
    </div>
  );
};
