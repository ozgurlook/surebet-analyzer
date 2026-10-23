import React from "react";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { Layout } from "./Layout";

describe("Layout dashboard status", () => {
  it("does not claim the service is live before dashboard data exists", () => {
    const client = new QueryClient();
    render(
      <QueryClientProvider client={client}>
        <Layout activeTab="opportunities" onTabChange={vi.fn()}>
          content
        </Layout>
      </QueryClientProvider>
    );

    expect(screen.getByText("Ulaşılamıyor")).toBeInTheDocument();
    expect(screen.queryByText("Canlı")).not.toBeInTheDocument();
  });

  it("constrains the application shell to the mobile viewport", () => {
    const client = new QueryClient();
    render(
      <QueryClientProvider client={client}>
        <Layout activeTab="opportunities" onTabChange={vi.fn()}>
          <span>viewport content</span>
        </Layout>
      </QueryClientProvider>
    );

    expect(screen.getByTestId("app-shell")).toHaveClass(
      "w-full",
      "min-w-0",
      "overflow-x-hidden"
    );
  });
});
