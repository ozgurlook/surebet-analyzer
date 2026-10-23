import React from "react";

export const OpportunitySkeleton: React.FC = () => {
  return (
    <div className="space-y-3">
      {[1, 2, 3].map((i) => (
        <div
          key={i}
          className="bg-navy-900/70 rounded-2xl border border-navy-800 p-4 animate-pulse"
        >
          <div className="flex justify-between items-center mb-3">
            <div className="h-3 w-28 bg-navy-800 rounded" />
            <div className="h-3 w-16 bg-navy-800 rounded" />
          </div>
          <div className="flex justify-between items-start mb-4">
            <div className="space-y-2">
              <div className="h-5 w-44 bg-navy-800 rounded" />
              <div className="h-3 w-20 bg-navy-800 rounded" />
            </div>
            <div className="space-y-1 text-right">
              <div className="h-5 w-20 bg-navy-800 rounded ml-auto" />
              <div className="h-3 w-14 bg-navy-800 rounded ml-auto" />
            </div>
          </div>
          <div className="grid grid-cols-3 gap-2 bg-navy-950/60 rounded-xl p-2.5">
            <div className="h-10 bg-navy-850 rounded" />
            <div className="h-10 bg-navy-850 rounded" />
            <div className="h-10 bg-navy-850 rounded" />
          </div>
        </div>
      ))}
    </div>
  );
};
