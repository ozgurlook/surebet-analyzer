import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import { TopHeader } from "./TopHeader";

afterEach(() => vi.restoreAllMocks());

describe("TopHeader refresh", () => {
  it("shows the manual refresh error", async () => {
    vi.spyOn(api, "refreshSources").mockRejectedValue(new Error("refresh failed"));
    const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <TopHeader status="unavailable" />
      </QueryClientProvider>
    );

    fireEvent.click(screen.getByRole("button", { name: "Verileri Yenile" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("refresh failed");
  });
});
