import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { BottomNav, NavTab } from "./BottomNav";

describe("BottomNav", () => {
  it("renders all 4 tabs and displays active state", () => {
    const handleTabChange = vi.fn();
    render(
      <BottomNav
        activeTab="opportunities"
        onTabChange={handleTabChange}
        opportunityCount={3}
      />
    );

    expect(screen.getByText("Fırsatlar")).toBeInTheDocument();
    expect(screen.getByText("Tüm Maçlar")).toBeInTheDocument();
    expect(screen.getByText("Kasa")).toBeInTheDocument();
    expect(screen.getByText("Kaynaklar")).toBeInTheDocument();
    expect(screen.getByText("Ayarlar")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.getAllByRole("button")).toHaveLength(5);

    fireEvent.click(screen.getByText("Kasa"));
    expect(handleTabChange).toHaveBeenCalledWith("accounting");
  });
});
