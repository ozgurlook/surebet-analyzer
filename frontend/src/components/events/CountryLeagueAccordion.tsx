import React, { useEffect, useState } from "react";
import { ChevronDown } from "lucide-react";
import clsx from "clsx";
import { CountryEventGroup, countryFlagEmoji } from "../../features/taxonomy/groupEvents";
import { loadExpandedEventGroups, saveExpandedEventGroups } from "../../features/taxonomy/filterState";
import { EventCard } from "./EventCard";

interface CountryLeagueAccordionProps {
  groups: readonly CountryEventGroup[];
  /** True while a search/filter narrowing is active. The API already filtered
   * `groups` down to matches, so every group currently present is a match - this
   * forces every group open without touching the persisted manual expansion set. */
  searchActive: boolean;
}

function countEvents(group: CountryEventGroup): number {
  return group.leagues.reduce((sum, league) => sum + league.events.length, 0);
}

/** Mobile country -> league -> matches accordion for All Matches. Countries/leagues
 * with a positive opportunity start expanded; everything else starts collapsed.
 * Manual toggles persist for the session via filterState's expansion helpers; an
 * active search temporarily expands every visible group without overwriting that
 * stored manual state. */
export const CountryLeagueAccordion: React.FC<CountryLeagueAccordionProps> = ({ groups, searchActive }) => {
  const [manualExpanded, setManualExpanded] = useState<ReadonlySet<string>>(
    () => new Set(loadExpandedEventGroups())
  );
  const [defaultsApplied, setDefaultsApplied] = useState(false);

  useEffect(() => {
    if (defaultsApplied || groups.length === 0) {
      return;
    }
    setDefaultsApplied(true);
    // Only seed defaults when nothing was already restored from this session's
    // storage - a previously stored (even empty) manual selection wins.
    if (manualExpanded.size > 0) {
      return;
    }
    const positiveIds = new Set<string>();
    for (const country of groups) {
      if (country.positiveCount > 0) {
        positiveIds.add(country.id);
      }
      for (const league of country.leagues) {
        if (league.positiveCount > 0) {
          positiveIds.add(league.id);
        }
      }
    }
    if (positiveIds.size > 0) {
      setManualExpanded(positiveIds);
    }
    // Deliberately not persisted here: this is a computed default, not a user action.
  }, [groups, defaultsApplied, manualExpanded]);

  const toggle = (id: string) => {
    setManualExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      saveExpandedEventGroups([...next]);
      return next;
    });
  };

  const isExpanded = (id: string) => searchActive || manualExpanded.has(id);

  return (
    <div className="space-y-2">
      {groups.map((group) => {
        const expanded = isExpanded(group.id);
        const flag = countryFlagEmoji(group.isoCode);
        const isUnmapped = group.regionClass === "unmapped";

        return (
          <div key={group.id} className="space-y-1.5">
            <button
              type="button"
              id={`country-toggle-${group.id}`}
              aria-expanded={expanded}
              aria-controls={`country-panel-${group.id}`}
              onClick={() => toggle(group.id)}
              className={clsx(
                "touch-target w-full flex items-center justify-between gap-2 rounded-xl px-3 py-2.5 border",
                isUnmapped
                  ? "bg-amber-950/30 border-amber-800/50"
                  : "bg-navy-900 border-navy-800"
              )}
            >
              <span className="flex items-center gap-2 text-sm font-semibold text-white min-w-0">
                {flag && <span aria-hidden="true">{flag}</span>}
                <span className="truncate">{group.name}</span>
              </span>
              <span className="flex items-center gap-2 shrink-0">
                {group.positiveCount > 0 && (
                  <span className="text-[10px] font-mono font-bold px-1.5 py-0.5 rounded bg-emerald-500/20 text-emerald-300 border border-emerald-500/40">
                    {group.positiveCount} pozitif
                  </span>
                )}
                <span className="text-[10px] text-slate-400 font-mono">{countEvents(group)}</span>
                <ChevronDown
                  className={clsx("w-4 h-4 text-slate-400 transition-transform", expanded && "rotate-180")}
                  aria-hidden="true"
                />
              </span>
            </button>

            {expanded && (
              <div id={`country-panel-${group.id}`} className="pl-2 space-y-1.5 border-l border-navy-800">
                {group.leagues.map((league) => {
                  const leagueExpanded = isExpanded(league.id);
                  return (
                    <div key={league.id} className="space-y-1.5">
                      <button
                        type="button"
                        id={`league-toggle-${league.id}`}
                        aria-expanded={leagueExpanded}
                        aria-controls={`league-panel-${league.id}`}
                        onClick={() => toggle(league.id)}
                        className="touch-target w-full flex items-center justify-between gap-2 rounded-lg px-2.5 py-2 bg-navy-950/60 border border-navy-850"
                      >
                        <span className="text-xs font-medium text-slate-200 truncate">{league.name}</span>
                        <span className="flex items-center gap-2 shrink-0">
                          {league.positiveCount > 0 && (
                            <span className="text-[10px] font-mono font-bold px-1.5 py-0.5 rounded bg-emerald-500/20 text-emerald-300 border border-emerald-500/40">
                              {league.positiveCount}
                            </span>
                          )}
                          <span className="text-[10px] text-slate-500 font-mono">{league.events.length}</span>
                          <ChevronDown
                            className={clsx(
                              "w-3.5 h-3.5 text-slate-500 transition-transform",
                              leagueExpanded && "rotate-180"
                            )}
                            aria-hidden="true"
                          />
                        </span>
                      </button>

                      {leagueExpanded && (
                        <div id={`league-panel-${league.id}`} className="space-y-2 pl-2">
                          {league.events.map((event) => (
                            <EventCard key={event.matched_id} event={event} />
                          ))}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
};
