import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import { OPEN_MODEL_CREDENTIAL_EVENT } from "../utils/modelCredential";

vi.mock("@/utils/antdMessage", () => ({
  message: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("../api/modules/auth", () => ({
  authApi: {
    logout: vi.fn(),
    updateProfile: vi.fn(),
    changePassword: vi.fn(),
    me: vi.fn(),
    getOauthStatus: vi.fn(),
    setAvatarIcon: vi.fn(),
    uploadAvatar: vi.fn(),
    deleteAvatar: vi.fn(),
    startOauthBind: vi.fn(),
    unbindOauth: vi.fn(),
  },
}));

vi.mock("../api/modules/preferences", () => ({
  preferencesApi: { setLocale: vi.fn() },
}));

vi.mock("../api/modules/provider", () => ({
  providerApi: {
    getMyCredential: vi.fn(),
    setMyCredential: vi.fn(),
    clearMyCredential: vi.fn(),
  },
}));

vi.mock("../api/request", () => ({
  clearAuthToken: vi.fn(),
}));

vi.mock("../utils/locale", () => ({
  applyGuestLocale: vi.fn(() => Promise.resolve()),
  applyUserLocale: vi.fn(() => Promise.resolve()),
}));

vi.mock("../utils/apiError", () => ({
  apiErrorMessage: (_e: unknown, fallback: string) => fallback,
}));

vi.mock("../utils/ssoPopup", () => ({
  isSsoPopupMessage: () => false,
  openSsoPopup: () => null,
}));

vi.mock("../utils/passwordPolicy", () => ({
  MIN_PASSWORD_LENGTH: 8,
  passwordPolicyIssue: () => null,
}));

vi.mock("../utils/permissions", () => ({
  userCan: () => true,
}));

vi.mock("../hooks/useUserRole", () => ({
  useUserRole: () => "user",
}));

vi.mock("../hooks/useIsMobile", () => ({
  useIsMobile: () => false,
}));

vi.mock("../context/LayoutModeContext", () => ({
  useLayoutMode: () => ({ layoutMode: "classic", setLayoutMode: vi.fn() }),
}));

vi.mock("./ThemeSwitcher", () => ({ default: () => null }));
vi.mock("./PaletteSwitcher", () => ({ default: () => null }));
vi.mock("../pages/Admin/Users/ProfileAvatar", () => ({
  ProfileAvatar: () => null,
  ProfileAvatarPicker: () => null,
}));

import { providerApi } from "../api/modules/provider";
import type { OctopUser } from "../api/modules/auth";
import AvatarDropdown from "./AvatarDropdown";

const testUser = {
  id: 7,
  username: "ada",
  display_name: "Ada",
  avatar_url: null,
  avatar_icon: null,
  role: "user",
  permissions: [],
  has_password: true,
  sso_identities: [],
  sso_kind: null,
  locale: "zh",
} as unknown as OctopUser;

/** Open the avatar menu, then the "My API Key" panel. */
async function openCredentialPanel() {
  // ``delay: null`` types instantly — antd re-renders on every keystroke and
  // the default per-key delay can outrun the ``waitFor`` timeout below.
  const user = userEvent.setup({ delay: null });
  render(
    <MemoryRouter>
      <AvatarDropdown user={testUser} placement="sidebar" />
    </MemoryRouter>,
  );
  await user.click(screen.getByRole("button", { name: /Ada/ }));
  await user.click(await screen.findByText("account.credentialMenu"));

  const baseInput = await screen.findByPlaceholderText(
    "account.credentialBaseUrlPlaceholder",
  );
  // The panel keeps its form disabled until the stored credential has loaded.
  await waitFor(() => expect(baseInput).toBeEnabled());
  return { user, baseInput };
}

describe("AvatarDropdown — My API Key", () => {
  beforeEach(() => {
    vi.mocked(providerApi.getMyCredential).mockReset();
    vi.mocked(providerApi.setMyCredential).mockReset();
    vi.mocked(providerApi.clearMyCredential).mockReset();
  });

  it("opens the panel from the menu and loads the stored credential", async () => {
    vi.mocked(providerApi.getMyCredential).mockResolvedValue({
      base_url: "https://api.mine/v1",
      has_api_key: true,
    });

    await openCredentialPanel();

    expect(
      await screen.findByText("account.credentialStatus"),
    ).toBeInTheDocument();
    expect(providerApi.getMyCredential).toHaveBeenCalledTimes(1);

    await waitFor(() =>
      expect(
        screen.getByPlaceholderText("account.credentialBaseUrlPlaceholder"),
      ).toHaveValue("https://api.mine/v1"),
    );
    expect(
      screen.getByText("account.credentialConfigured"),
    ).toBeInTheDocument();
    // The key itself is never echoed back into the form.
    expect(
      screen.getByPlaceholderText("account.credentialApiKeyPlaceholder"),
    ).toHaveValue("");
  });

  it("submits the entered credential and clears the key field", async () => {
    vi.mocked(providerApi.getMyCredential).mockResolvedValue({
      base_url: null,
      has_api_key: false,
    });
    vi.mocked(providerApi.setMyCredential).mockResolvedValue({
      base_url: "https://api.mine/v1",
      has_api_key: true,
    });

    const { user, baseInput } = await openCredentialPanel();

    // With nothing stored, the FlowBee endpoint is pre-filled for the user.
    await waitFor(() =>
      expect(baseInput).toHaveValue("https://flowbee.top/v1"),
    );
    await user.clear(baseInput);
    await user.type(baseInput, "https://api.mine/v1");
    const keyInput = screen.getByPlaceholderText(
      "account.credentialApiKeyPlaceholder",
    );
    await user.type(keyInput, "sk-mine");
    await user.click(
      screen.getByRole("button", { name: "account.credentialSave" }),
    );

    await waitFor(() =>
      expect(providerApi.setMyCredential).toHaveBeenCalledWith({
        base_url: "https://api.mine/v1",
        api_key: "sk-mine",
      }),
    );
    expect(keyInput).toHaveValue("");
    expect(
      screen.getByText("account.credentialConfigured"),
    ).toBeInTheDocument();
  });

  it("clears a stored credential after confirmation", async () => {
    vi.mocked(providerApi.getMyCredential).mockResolvedValue({
      base_url: null,
      has_api_key: true,
    });
    vi.mocked(providerApi.clearMyCredential).mockResolvedValue(undefined);

    const { user } = await openCredentialPanel();

    await user.click(
      await screen.findByRole("button", { name: "account.credentialClear" }),
    );
    await user.click(
      await screen.findByRole("button", { name: "common.confirm" }),
    );

    await waitFor(() =>
      expect(providerApi.clearMyCredential).toHaveBeenCalledTimes(1),
    );
    expect(
      await screen.findByText("account.credentialNotConfigured"),
    ).toBeInTheDocument();
  });

  it("opens the panel when a refused model call asks for the key", async () => {
    vi.mocked(providerApi.getMyCredential).mockResolvedValue({
      base_url: null,
      has_api_key: false,
    });

    render(
      <MemoryRouter>
        <AvatarDropdown user={testUser} placement="sidebar" />
      </MemoryRouter>,
    );
    // Nothing loads until the panel opens — the event is what opens it.
    expect(providerApi.getMyCredential).not.toHaveBeenCalled();

    await act(async () => {
      window.dispatchEvent(new Event(OPEN_MODEL_CREDENTIAL_EVENT));
    });

    const baseInput = await screen.findByPlaceholderText(
      "account.credentialBaseUrlPlaceholder",
    );
    await waitFor(() => expect(baseInput).toBeEnabled());
    expect(providerApi.getMyCredential).toHaveBeenCalledTimes(1);
  });
});
