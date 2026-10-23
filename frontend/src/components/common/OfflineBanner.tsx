import React, { useEffect, useState } from "react";
import { WifiOff } from "lucide-react";

export const OfflineBanner: React.FC<{ lastUpdated?: string | null }> = ({ lastUpdated }) => {
  const [isOffline, setIsOffline] = useState(!navigator.onLine);

  useEffect(() => {
    const handleOnline = () => setIsOffline(false);
    const handleOffline = () => setIsOffline(true);

    window.addEventListener("online", handleOnline);
    window.addEventListener("offline", handleOffline);

    return () => {
      window.removeEventListener("online", handleOnline);
      window.removeEventListener("offline", handleOffline);
    };
  }, []);

  if (!isOffline) return null;

  return (
    <div className="bg-amber-500/15 border-b border-amber-500/30 px-4 py-2 text-xs text-amber-300 flex items-center justify-between gap-2 sticky top-0 z-50 backdrop-blur-md">
      <div className="flex items-center gap-2">
        <WifiOff className="w-4 h-4 text-amber-400 shrink-0" />
        <span>
          <strong>Çevrimdışı Mod</strong> — Gösterilen veriler salt-okunurdur.
        </span>
      </div>
      {lastUpdated && (
        <span className="text-[11px] opacity-75 font-mono">
          {new Date(lastUpdated).toLocaleTimeString("tr-TR", { hour: "2-digit", minute: "2-digit" })}
        </span>
      )}
    </div>
  );
};
