import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({
  captcha: { provider: "none" } as {
    provider: string;
    local_login_enabled?: boolean;
  },
  providers: [{ kind: "flowbee", display_name: "FlowBee", enabled: true }],
}));

vi.mock("../../context/ThemeContext", () => ({
  useTheme: () => ({ isDark: false }),
}));

vi.mock("../../api/modules/auth", () => ({
  authApi: {
    getAuthStatus: () => Promise.resolve({ setup_required: false }),
    getOauthStatus: () => Promise.resolve({ providers: state.providers }),
    getLdapStatus: () => Promise.resolve({ enabled: false, display_name: "" }),
    getCaptcha: () => Promise.resolve(state.captcha),
  },
}));

vi.mock("../../utils/locale", () => ({
  applyGuestLocale: () => Promise.resolve(),
  applyUserLocale: () => Promise.resolve(),
}));

vi.mock("./CaptchaField", () => ({
  default: () => null,
}));

import LoginPage from "./index";

const renderLogin = () =>
  render(
    <MemoryRouter>
      <LoginPage />
    </MemoryRouter>,
  );

const usernameField = () =>
  screen.queryByPlaceholderText(/login\.username|用户名|username/i);

const SSO_ONLY_HINT = /login\.ssoOnlyHint|仅支持单点登录|single sign-on only/i;

describe("Login page in SSO-only mode", () => {
  beforeEach(() => {
    state.captcha = { provider: "none" };
  });

  it("hides the password form and explains why", async () => {
    state.captcha = { provider: "none", local_login_enabled: false };
    renderLogin();

    await waitFor(() => {
      expect(screen.getByText(SSO_ONLY_HINT)).toBeInTheDocument();
    });
    expect(usernameField()).toBeNull();
    expect(
      screen.queryByPlaceholderText(/login\.password|密码|password/i),
    ).toBeNull();
    expect(screen.queryByTestId("login-forgot-password-toggle")).toBeNull();
    // The single sign-on button is the only way in.
    expect(
      screen.getByRole("button", { name: /login\.oidcWith|FlowBee|继续/i }),
    ).toBeInTheDocument();
  });

  it("keeps the form when the server allows local sign-in", async () => {
    state.captcha = { provider: "none", local_login_enabled: true };
    renderLogin();

    await waitFor(() => {
      expect(usernameField()).toBeInTheDocument();
    });
    expect(screen.queryByText(SSO_ONLY_HINT)).toBeNull();
  });
});
