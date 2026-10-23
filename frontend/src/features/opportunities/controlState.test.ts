import { beforeEach, describe, expect, it } from "vitest";
import {
  clearOpportunityControls,
  loadOpportunityControls,
  MAX_BUDGET_LIRAS,
  MIN_BUDGET_LIRAS,
  saveOpportunityControls,
} from "./controlState";

const USER_A = 1;
const USER_B = 2;

beforeEach(() => {
  localStorage.clear();
});

describe("saveOpportunityControls / loadOpportunityControls", () => {
  it("uses a storage key scoped to the given user id", () => {
    saveOpportunityControls(USER_A, { budgetLiras: 500, includeSourceA: true });

    expect(localStorage.getItem("surebet.opportunities.controls.1")).toContain("500");
  });

  it("round-trips a saved selection for one user", () => {
    saveOpportunityControls(USER_A, { budgetLiras: 2500, includeSourceA: true });

    expect(loadOpportunityControls(USER_A)).toEqual({ budgetLiras: 2500, includeSourceA: true });
  });

  it("keeps two different users' controls fully isolated", () => {
    saveOpportunityControls(USER_A, { budgetLiras: 1000, includeSourceA: false });
    saveOpportunityControls(USER_B, { budgetLiras: 5000, includeSourceA: true });

    expect(loadOpportunityControls(USER_A)).toEqual({ budgetLiras: 1000, includeSourceA: false });
    expect(loadOpportunityControls(USER_B)).toEqual({ budgetLiras: 5000, includeSourceA: true });
  });

  it("returns null when nothing was ever stored for this user", () => {
    expect(loadOpportunityControls(USER_A)).toBeNull();
  });

  it("returns null on invalid JSON", () => {
    localStorage.setItem("surebet.opportunities.controls.1", "{not valid json");

    expect(loadOpportunityControls(USER_A)).toBeNull();
  });

  it("returns null when the stored budget is out of the supported range", () => {
    localStorage.setItem(
      "surebet.opportunities.controls.1",
      JSON.stringify({ budgetLiras: MAX_BUDGET_LIRAS + 1, includeSourceA: true })
    );
    expect(loadOpportunityControls(USER_A)).toBeNull();

    localStorage.setItem(
      "surebet.opportunities.controls.1",
      JSON.stringify({ budgetLiras: MIN_BUDGET_LIRAS - 1, includeSourceA: true })
    );
    expect(loadOpportunityControls(USER_A)).toBeNull();
  });

  it("returns null on wrong-shaped data", () => {
    localStorage.setItem("surebet.opportunities.controls.1", JSON.stringify({ budgetLiras: "1000" }));

    expect(loadOpportunityControls(USER_A)).toBeNull();
  });
});

describe("clearOpportunityControls", () => {
  it("removes only the given user's stored controls", () => {
    saveOpportunityControls(USER_A, { budgetLiras: 1000, includeSourceA: false });
    saveOpportunityControls(USER_B, { budgetLiras: 5000, includeSourceA: true });

    clearOpportunityControls(USER_A);

    expect(loadOpportunityControls(USER_A)).toBeNull();
    expect(loadOpportunityControls(USER_B)).toEqual({ budgetLiras: 5000, includeSourceA: true });
  });
});
