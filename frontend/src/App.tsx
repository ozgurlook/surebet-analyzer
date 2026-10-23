import React, { useCallback, useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, setCsrfToken, setUnauthorizedHandler } from "./api/client";
import { Layout } from "./components/layout/Layout";
import { NavTab } from "./components/layout/BottomNav";
import { OpportunitiesPage } from "./pages/OpportunitiesPage";
import { OpportunityDetailPage } from "./pages/OpportunityDetailPage";
import { AllEventsPage } from "./pages/AllEventsPage";
import { AccountingPage } from "./pages/AccountingPage";
import { SourcesHealthPage } from "./pages/SourcesHealthPage";
import { SettingsPage } from "./pages/SettingsPage";
import { MappingAdminPage } from "./pages/MappingAdminPage";
import { LoginPage } from "./pages/LoginPage";
import { AuthState, CalculatedOpportunity } from "./types/api";

type SettingsSubview = "mapping" | null;

const DEFAULT_TAB: NavTab = "opportunities";

export const App: React.FC = () => {
  const queryClient = useQueryClient();
  const [activeTab, setActiveTab] = useState<NavTab>(DEFAULT_TAB);
  const [selectedOpportunity, setSelectedOpportunity] = useState<CalculatedOpportunity | null>(null);
  const [settingsSubview, setSettingsSubview] = useState<SettingsSubview>(null);

  // Check auth state
  const { data: authState, isLoading: isAuthLoading } = useQuery({
    queryKey: ["auth"],
    queryFn: api.getAuthState,
    retry: false,
  });

  // The single 401 boundary: the API client's request() helper calls this on
  // any real HTTP 401. It clears the full query cache, the in-memory CSRF
  // token, and personal transient App-level state, then publishes an
  // unauthenticated auth state so the login screen renders immediately.
  // Explicit logout (below) reuses this same function. A normal `/auth/me`
  // 200-with-authenticated:false response is not a 401, so it never reaches
  // this callback and cannot recursively re-trigger it.
  //
  // Idempotency guard: reads the *current* cached auth state (not a stale
  // closure) so that a call arriving after an equivalent reset already ran
  // in this same tick -- e.g. a logout request that itself 401s: the shared
  // request() helper's 401 branch invokes this via the registered
  // unauthorizedHandler, and then the ApiError it throws is also caught by
  // handleLogout's catch below, which calls this again -- is a no-op rather
  // than a second full reset (double queryClient.clear(), double CSRF
  // clear, double auth-state publish).
  const resetToUnauthenticated = useCallback(() => {
    const previousAuth = queryClient.getQueryData<AuthState>(["auth"]);
    if (previousAuth?.authenticated === false) {
      return;
    }
    queryClient.clear();
    setCsrfToken(null);
    setSelectedOpportunity(null);
    setSettingsSubview(null);
    setActiveTab(DEFAULT_TAB);
    queryClient.setQueryData<AuthState>(["auth"], {
      authenticated: false,
      registration_enabled: previousAuth?.registration_enabled ?? true,
      user_id: null,
      username: null,
      csrf_token: null,
    });
  }, [queryClient]);

  useEffect(() => {
    setUnauthorizedHandler(resetToUnauthenticated);
    return () => setUnauthorizedHandler(null);
  }, [resetToUnauthenticated]);

  // Fetch global dashboard state for status pill & badges
  const { data: dashboard } = useQuery({
    queryKey: ["dashboard"],
    queryFn: api.getDashboard,
    enabled: authState?.authenticated === true,
    refetchInterval: 10000,
  });

  if (isAuthLoading) {
    return (
      <div className="min-h-screen bg-navy-950 flex items-center justify-center">
        <div className="w-8 h-8 rounded-full border-2 border-brand-primary border-t-transparent animate-spin" />
      </div>
    );
  }

  if (!authState?.authenticated || authState.user_id === null) {
    return (
      <LoginPage
        onLoginSuccess={() => queryClient.invalidateQueries({ queryKey: ["auth"] })}
        registrationEnabled={authState?.registration_enabled ?? true}
      />
    );
  }

  const userId = authState.user_id;

  const handleTabChange = (tab: NavTab) => {
    setSelectedOpportunity(null);
    setSettingsSubview(null);
    setActiveTab(tab);
  };

  const handleLogout = async () => {
    try {
      await api.logout();
    } catch {
      // The local reset below still runs even if the network logout request
      // fails -- the user must never get stuck on an authenticated-looking
      // screen just because the logout call itself couldn't reach the server.
    }
    resetToUnauthenticated();
  };

  return (
    <Layout
      activeTab={activeTab}
      onTabChange={handleTabChange}
      dashboard={dashboard}
    >
      {/* If an opportunity is selected, show its full-screen detail route */}
      {selectedOpportunity ? (
        <OpportunityDetailPage
          opportunity={selectedOpportunity}
          onBack={() => setSelectedOpportunity(null)}
        />
      ) : activeTab === "opportunities" ? (
        <OpportunitiesPage
          userId={userId}
          onSelectOpportunity={(opp) => setSelectedOpportunity(opp)}
          onGoToAllEvents={() => setActiveTab("events")}
        />
      ) : activeTab === "events" ? (
        <AllEventsPage userId={userId} />
      ) : activeTab === "accounting" ? (
        <AccountingPage />
      ) : activeTab === "sources" ? (
        <SourcesHealthPage />
      ) : settingsSubview === "mapping" ? (
        <MappingAdminPage onBack={() => setSettingsSubview(null)} />
      ) : (
        <SettingsPage
          userId={userId}
          username={authState.username}
          onLogout={handleLogout}
          onOpenMappingAdmin={() => setSettingsSubview("mapping")}
        />
      )}
    </Layout>
  );
};
