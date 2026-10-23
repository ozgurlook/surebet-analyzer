import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { StickyControlBar } from "./StickyControlBar";

function renderBar(onBudgetChange = vi.fn()) {
  render(
    <StickyControlBar
      budgetLiras={1000}
      onBudgetChange={onBudgetChange}
      includeSourceA={false}
      onIncludeSourceAChange={vi.fn()}
      filterOption="all"
      onFilterChange={vi.fn()}
      selectedTaxonomyFilterCount={0}
      onOpenTaxonomyFilters={vi.fn()}
    />
  );
  fireEvent.click(screen.getByText("1.000"));
  return onBudgetChange;
}

describe("StickyControlBar budget editor", () => {
  it.each(["1e3", "10.5", "9", "10000001", "abc"])(
    "rejects invalid whole-TL budget %s",
    (value) => {
      const onBudgetChange = renderBar();
      const input = screen.getByLabelText("Toplam bütçe (TL)");

      fireEvent.change(input, { target: { value } });
      fireEvent.submit(input.closest("form")!);

      expect(onBudgetChange).not.toHaveBeenCalled();
      expect(screen.getByRole("alert")).toHaveTextContent(
        "10 ile 10.000.000 TL arasında tam sayı girin"
      );
    }
  );

  it("accepts a valid whole-TL budget", () => {
    const onBudgetChange = renderBar();
    const input = screen.getByLabelText("Toplam bütçe (TL)");

    fireEvent.change(input, { target: { value: "2500" } });
    fireEvent.submit(input.closest("form")!);

    expect(onBudgetChange).toHaveBeenCalledWith(2500);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("stacks budget controls and keeps the Kaynak A toggle shrink-safe on mobile", () => {
    renderBar();

    expect(screen.getByTestId("budget-controls-row")).toHaveClass("flex-col");
    expect(screen.getByTestId("quick-budgets")).toHaveClass("grid", "grid-cols-4", "w-full");
    expect(screen.getByTestId("source_a-copy")).toHaveClass("min-w-0", "flex-1");
    expect(screen.getByLabelText("Kaynak A yan oran switch")).toHaveClass("shrink-0");
  });

  it("keeps every quick-filter chip at its natural width inside the scroll strip", () => {
    renderBar();

    for (const name of ["Ülke/Lig", "Tüm Fırsatlar", "Bugün", "Yaklaşan", "%2+ Fark"]) {
      expect(screen.getByRole("button", { name })).toHaveClass("shrink-0");
    }
  });
});
