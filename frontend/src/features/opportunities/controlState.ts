export const MIN_BUDGET_LIRAS = 10;
export const MAX_BUDGET_LIRAS = 10_000_000;

const STORAGE_KEY_PREFIX = "surebet.opportunities.controls";

export interface OpportunityControls {
  budgetLiras: number;
  includeSourceA: boolean;
}

// Scoped by the authenticated user's id so one device switching between
// accounts never leaks one user's budget/Source A preference into another's
// session.
function storageKey(userId: number): string {
  return `${STORAGE_KEY_PREFIX}.${userId}`;
}

export function loadOpportunityControls(userId: number): OpportunityControls | null {
  try {
    const raw = localStorage.getItem(storageKey(userId));
    if (!raw) return null;

    const value: unknown = JSON.parse(raw);
    if (
      typeof value !== "object" ||
      value === null ||
      !("budgetLiras" in value) ||
      !("includeSourceA" in value)
    ) {
      return null;
    }

    const controls = value as Partial<OpportunityControls>;
    if (
      !Number.isInteger(controls.budgetLiras) ||
      controls.budgetLiras! < MIN_BUDGET_LIRAS ||
      controls.budgetLiras! > MAX_BUDGET_LIRAS ||
      typeof controls.includeSourceA !== "boolean"
    ) {
      return null;
    }
    return controls as OpportunityControls;
  } catch {
    return null;
  }
}

export function saveOpportunityControls(userId: number, controls: OpportunityControls): void {
  localStorage.setItem(storageKey(userId), JSON.stringify(controls));
}

export function clearOpportunityControls(userId: number): void {
  localStorage.removeItem(storageKey(userId));
}
