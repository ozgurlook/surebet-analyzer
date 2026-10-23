import React from "react";
import { Zap, ListFilter, Wallet, Server, Settings } from "lucide-react";
import clsx from "clsx";

export type NavTab = "opportunities" | "events" | "accounting" | "sources" | "settings";

interface BottomNavProps {
  activeTab: NavTab;
  onTabChange: (tab: NavTab) => void;
  opportunityCount?: number;
}

export const BottomNav: React.FC<BottomNavProps> = ({
  activeTab,
  onTabChange,
  opportunityCount = 0,
}) => {
  const tabs: Array<{
    id: NavTab;
    label: string;
    icon: React.ComponentType<{ className?: string }>;
    badge?: number;
  }> = [
    {
      id: "opportunities",
      label: "Fırsatlar",
      icon: Zap,
      badge: opportunityCount > 0 ? opportunityCount : undefined,
    },
    {
      id: "events",
      label: "Tüm Maçlar",
      icon: ListFilter,
    },
    {
      id: "accounting",
      label: "Kasa",
      icon: Wallet,
    },
    {
      id: "sources",
      label: "Kaynaklar",
      icon: Server,
    },
    {
      id: "settings",
      label: "Ayarlar",
      icon: Settings,
    },
  ];

  return (
    <nav className="fixed bottom-0 left-0 right-0 z-40 bg-navy-950/95 backdrop-blur-lg border-t border-navy-850 px-2 py-1 flex items-center justify-around shadow-2xl safe-area-pb">
      {tabs.map((tab) => {
        const Icon = tab.icon;
        const isActive = activeTab === tab.id;

        return (
          <button
            key={tab.id}
            onClick={() => onTabChange(tab.id)}
            className={clsx(
              "touch-target flex-1 flex flex-col items-center justify-center py-1 px-2 rounded-xl transition-all duration-200 relative group",
              isActive
                ? "text-brand-primary"
                : "text-slate-400 hover:text-slate-200 active:scale-95"
            )}
            aria-label={tab.label}
          >
            <div className="relative">
              <Icon
                className={clsx(
                  "w-5 h-5 transition-transform",
                  isActive ? "scale-110" : "group-hover:scale-105"
                )}
              />
              {tab.badge !== undefined && (
                <span className="absolute -top-1 -right-2.5 bg-brand-positive text-navy-950 font-bold text-[9px] px-1 py-0.2 rounded-full min-w-4 text-center leading-tight shadow-sm font-mono">
                  {tab.badge}
                </span>
              )}
            </div>
            <span
              className={clsx(
                "text-[10px] mt-1 font-medium tracking-tight",
                isActive ? "font-bold text-white" : ""
              )}
            >
              {tab.label}
            </span>
            {isActive && (
              <span className="absolute bottom-0 w-8 h-0.5 bg-brand-primary rounded-full shadow-[0_0_8px_#38bdf8]" />
            )}
          </button>
        );
      })}
    </nav>
  );
};
