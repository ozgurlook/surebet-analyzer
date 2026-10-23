import { CalculatedOpportunity } from "../../types/api";

export type OpportunityQuickFilter = "all" | "today" | "upcoming" | "high_margin";

const istanbulDateFormatter = new Intl.DateTimeFormat("en-CA", {
  timeZone: "Europe/Istanbul",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

export function istanbulDateKey(value: Date | string): string {
  const parts = istanbulDateFormatter.formatToParts(
    typeof value === "string" ? new Date(value) : value
  );
  const part = (type: Intl.DateTimeFormatPartTypes) =>
    parts.find((item) => item.type === type)?.value ?? "";
  return `${part("year")}-${part("month")}-${part("day")}`;
}

export function filterOpportunities(
  opportunities: CalculatedOpportunity[],
  filter: OpportunityQuickFilter,
  now = new Date()
): CalculatedOpportunity[] {
  const today = istanbulDateKey(now);

  return opportunities.filter((opportunity) => {
    if (filter === "high_margin") {
      return opportunity.min_adjusted_profit_percent >= 2;
    }
    const kickoffDay = istanbulDateKey(opportunity.kickoff);
    if (filter === "today") return kickoffDay === today;
    if (filter === "upcoming") return kickoffDay > today;
    return true;
  });
}
