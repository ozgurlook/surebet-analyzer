import React from "react";
import { BottomNav, NavTab } from "./BottomNav";
import { TopHeader } from "./TopHeader";
import { OfflineBanner } from "../common/OfflineBanner";
import { DashboardSummary } from "../../types/api";

interface LayoutProps {
  children: React.ReactNode;
  activeTab: NavTab;
  onTabChange: (tab: NavTab) => void;
  dashboard?: DashboardSummary;
}

export const Layout: React.FC<LayoutProps> = ({
  children,
  activeTab,
  onTabChange,
  dashboard,
}) => {
  return (
    <div
      data-testid="app-shell"
      className="min-h-screen w-full min-w-0 overflow-x-hidden bg-navy-950 flex flex-col max-w-md mx-auto relative shadow-2xl border-x border-navy-900/50"
    >
      <OfflineBanner lastUpdated={dashboard?.last_refresh_at} />
      <TopHeader
        status={dashboard?.overall_status ?? "unavailable"}
        lastRefreshAt={dashboard?.last_refresh_at}
      />
      <main className="min-w-0 flex-1 pb-20 overflow-y-auto px-4 py-3">{children}</main>
      <BottomNav
        activeTab={activeTab}
        onTabChange={onTabChange}
        opportunityCount={dashboard?.positive_opportunities_count}
      />
    </div>
  );
};
