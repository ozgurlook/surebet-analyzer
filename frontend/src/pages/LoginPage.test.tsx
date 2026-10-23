import React from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "../api/client";
import { LoginPage } from "./LoginPage";

function setViewportWidth(width: number) {
  Object.defineProperty(window, "innerWidth", {
    writable: true,
    configurable: true,
    value: width,
  });
  window.dispatchEvent(new Event("resize"));
}

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
  sessionStorage.clear();
});

describe("LoginPage login submission", () => {
  it("submits username and password to api.login and calls onLoginSuccess", async () => {
    const login = vi.spyOn(api, "login").mockResolvedValue({
      user_id: 1,
      username: "alice",
      csrf_token: "token-1",
    });
    const onLoginSuccess = vi.fn();

    render(<LoginPage onLoginSuccess={onLoginSuccess} registrationEnabled={true} />);

    fireEvent.change(screen.getByLabelText(/Kullanıcı Adı/i), { target: { value: "alice" } });
    fireEvent.change(screen.getByLabelText(/^Parola$/i), { target: { value: "s3cret-pass" } });
    fireEvent.click(screen.getByRole("button", { name: /Giriş Yap/i }));

    await waitFor(() => expect(login).toHaveBeenCalledWith("alice", "s3cret-pass"));
    await waitFor(() => expect(onLoginSuccess).toHaveBeenCalledTimes(1));
  });

  it("does not submit when a field is empty", () => {
    const login = vi.spyOn(api, "login").mockResolvedValue({
      user_id: 1,
      username: "alice",
      csrf_token: "token-1",
    });
    render(<LoginPage onLoginSuccess={vi.fn()} registrationEnabled={true} />);

    fireEvent.click(screen.getByRole("button", { name: /Giriş Yap/i }));

    expect(login).not.toHaveBeenCalled();
  });
});

describe("LoginPage registration submission", () => {
  it("submits username, password, and registration code to api.register and calls onLoginSuccess", async () => {
    const register = vi.spyOn(api, "register").mockResolvedValue({
      user_id: 2,
      username: "bob",
      csrf_token: "token-2",
    });
    const onLoginSuccess = vi.fn();

    render(<LoginPage onLoginSuccess={onLoginSuccess} registrationEnabled={true} />);

    fireEvent.click(screen.getByRole("tab", { name: /Kayıt Ol/i }));

    fireEvent.change(screen.getByLabelText(/Kullanıcı Adı/i), { target: { value: "bob" } });
    fireEvent.change(screen.getByLabelText(/^Yeni Parola$/i), { target: { value: "n3w-pass" } });
    fireEvent.change(screen.getByLabelText(/Kayıt Kodu/i), { target: { value: "invite-42" } });
    fireEvent.click(screen.getByRole("button", { name: /Kayıt Ol/i }));

    await waitFor(() => expect(register).toHaveBeenCalledWith("bob", "n3w-pass", "invite-42"));
    await waitFor(() => expect(onLoginSuccess).toHaveBeenCalledTimes(1));
  });
});

describe("LoginPage registration visibility", () => {
  it("hides the registration tab and fields when registration is disabled", () => {
    render(<LoginPage onLoginSuccess={vi.fn()} registrationEnabled={false} />);

    expect(screen.queryByRole("tab", { name: /Kayıt Ol/i })).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/Kayıt Kodu/i)).not.toBeInTheDocument();
    expect(screen.getByLabelText(/Kullanıcı Adı/i)).toBeInTheDocument();
  });

  it("shows the registration tab when registration is enabled", () => {
    render(<LoginPage onLoginSuccess={vi.fn()} registrationEnabled={true} />);

    expect(screen.getByRole("tab", { name: /Kayıt Ol/i })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Giriş Yap/i })).toBeInTheDocument();
  });
});

describe("LoginPage pending-state protection", () => {
  it("disables the login form while a request is in flight", async () => {
    let resolveLogin!: (value: { user_id: number; username: string; csrf_token: string }) => void;
    vi.spyOn(api, "login").mockImplementation(
      () => new Promise((resolve) => { resolveLogin = resolve; })
    );

    render(<LoginPage onLoginSuccess={vi.fn()} registrationEnabled={true} />);

    fireEvent.change(screen.getByLabelText(/Kullanıcı Adı/i), { target: { value: "alice" } });
    fireEvent.change(screen.getByLabelText(/^Parola$/i), { target: { value: "s3cret-pass" } });
    fireEvent.click(screen.getByRole("button", { name: /Giriş Yap/i }));

    await waitFor(() => expect(screen.getByLabelText(/Kullanıcı Adı/i)).toBeDisabled());
    expect(screen.getByLabelText(/^Parola$/i)).toBeDisabled();
    expect(screen.getByRole("button", { name: /Doğrulanıyor/i })).toBeDisabled();

    await act(async () => {
      resolveLogin({ user_id: 1, username: "alice", csrf_token: "token-1" });
    });
  });
});

describe("LoginPage safe API errors", () => {
  it("shows exactly the server's error message without inventing a more specific one", async () => {
    vi.spyOn(api, "login").mockRejectedValue(new Error("Geçersiz kullanıcı adı veya parola"));

    render(<LoginPage onLoginSuccess={vi.fn()} registrationEnabled={true} />);

    fireEvent.change(screen.getByLabelText(/Kullanıcı Adı/i), { target: { value: "alice" } });
    fireEvent.change(screen.getByLabelText(/^Parola$/i), { target: { value: "wrong-pass" } });
    fireEvent.click(screen.getByRole("button", { name: /Giriş Yap/i }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Geçersiz kullanıcı adı veya parola");
  });

  it("shows the server's registration error message", async () => {
    vi.spyOn(api, "register").mockRejectedValue(new Error("Bu kullanıcı adı kullanılıyor"));

    render(<LoginPage onLoginSuccess={vi.fn()} registrationEnabled={true} />);

    fireEvent.click(screen.getByRole("tab", { name: /Kayıt Ol/i }));
    fireEvent.change(screen.getByLabelText(/Kullanıcı Adı/i), { target: { value: "bob" } });
    fireEvent.change(screen.getByLabelText(/^Yeni Parola$/i), { target: { value: "n3w-pass" } });
    fireEvent.change(screen.getByLabelText(/Kayıt Kodu/i), { target: { value: "invite-42" } });
    fireEvent.click(screen.getByRole("button", { name: /Kayıt Ol/i }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Bu kullanıcı adı kullanılıyor");
  });
});

describe("LoginPage autocomplete attributes", () => {
  it("sets username/current-password autocomplete on the login form", () => {
    render(<LoginPage onLoginSuccess={vi.fn()} registrationEnabled={true} />);

    expect(screen.getByLabelText(/Kullanıcı Adı/i)).toHaveAttribute("autocomplete", "username");
    expect(screen.getByLabelText(/^Parola$/i)).toHaveAttribute("autocomplete", "current-password");
  });

  it("sets username/new-password/off autocomplete on the registration form", () => {
    render(<LoginPage onLoginSuccess={vi.fn()} registrationEnabled={true} />);
    fireEvent.click(screen.getByRole("tab", { name: /Kayıt Ol/i }));

    expect(screen.getByLabelText(/Kullanıcı Adı/i)).toHaveAttribute("autocomplete", "username");
    expect(screen.getByLabelText(/^Yeni Parola$/i)).toHaveAttribute("autocomplete", "new-password");
    expect(screen.getByLabelText(/Kayıt Kodu/i)).toHaveAttribute("autocomplete", "off");
  });
});

describe("LoginPage secrets are never persisted", () => {
  it("never writes the password or registration code to localStorage or sessionStorage", async () => {
    vi.spyOn(api, "login").mockResolvedValue({ user_id: 1, username: "alice", csrf_token: "token-1" });
    vi.spyOn(api, "register").mockResolvedValue({ user_id: 2, username: "bob", csrf_token: "token-2" });

    render(<LoginPage onLoginSuccess={vi.fn()} registrationEnabled={true} />);

    fireEvent.change(screen.getByLabelText(/Kullanıcı Adı/i), { target: { value: "alice" } });
    fireEvent.change(screen.getByLabelText(/^Parola$/i), { target: { value: "super-secret-pw" } });
    fireEvent.click(screen.getByRole("button", { name: /Giriş Yap/i }));
    await waitFor(() => expect(api.login).toHaveBeenCalled());

    fireEvent.click(screen.getByRole("tab", { name: /Kayıt Ol/i }));
    fireEvent.change(screen.getByLabelText(/Kullanıcı Adı/i), { target: { value: "bob" } });
    fireEvent.change(screen.getByLabelText(/^Yeni Parola$/i), { target: { value: "another-secret-pw" } });
    fireEvent.change(screen.getByLabelText(/Kayıt Kodu/i), { target: { value: "top-secret-code" } });
    fireEvent.click(screen.getByRole("button", { name: /Kayıt Ol/i }));
    await waitFor(() => expect(api.register).toHaveBeenCalled());

    const dumpedStorage = (storage: Storage) =>
      Object.keys(storage).map((key) => storage.getItem(key)).join("|");

    expect(dumpedStorage(localStorage)).not.toContain("super-secret-pw");
    expect(dumpedStorage(localStorage)).not.toContain("another-secret-pw");
    expect(dumpedStorage(localStorage)).not.toContain("top-secret-code");
    expect(dumpedStorage(sessionStorage)).not.toContain("super-secret-pw");
    expect(dumpedStorage(sessionStorage)).not.toContain("another-secret-pw");
    expect(dumpedStorage(sessionStorage)).not.toContain("top-secret-code");
  });
});

describe.each([360, 390])("LoginPage at %ipx viewport", (width) => {
  it("renders without document-level horizontal overflow", () => {
    setViewportWidth(width);

    render(<LoginPage onLoginSuccess={vi.fn()} registrationEnabled={true} />);

    expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(width);
    expect(document.body.scrollWidth).toBeLessThanOrEqual(width);
  });
});
