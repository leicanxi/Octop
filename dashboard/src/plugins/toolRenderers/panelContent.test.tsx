/**
 * 插件常驻面板宿主：不经过任何工具调用，直接把 ``ui/manifest.json`` 里声明的
 * renderer 挂起来（小手机是第一个用户，它读写角色工作区的 phone/state.json）。
 */
import * as React from "react";
import * as ReactJSX from "react/jsx-runtime";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import PluginPanelContent from "./PluginPanel";
import { createPluginUiHost } from "./host";
import { clearPluginPanels, registerPluginPanel } from "./panelRegistry";
import { clearToolRenderers } from "./registry";

const PHONE_UI_PATH =
  "../../../../src/octop/infra/agents/plugins/bundled/phone/ui/index.js";
const STATE_PATH = "phone/state.json";

const state = {
  version: 1,
  owner: { name: "阿离", avatar: "🦊" },
  wechat: {
    chats: [
      {
        id: "c1",
        name: "小满",
        avatar: "🙂",
        messages: [{ side: "them", text: "晚上去看电影吗", time: "20:10" }],
      },
    ],
  },
  moments: [],
  notes: [],
};

const requestMock = vi.hoisted(() => vi.fn());
vi.mock("../../api/request", () => ({
  request: requestMock,
  getAuthToken: () => "",
}));

function installPhoneRenderer(): Promise<void> {
  const win = window as unknown as {
    __OCTOP_REACT__?: unknown;
    __OCTOP_JSX__?: unknown;
  };
  win.__OCTOP_REACT__ = React;
  win.__OCTOP_JSX__ = ReactJSX;
  return import(/* @vite-ignore */ PHONE_UI_PATH).then(
    (mod: { setup: (host: unknown) => void }) => {
      // The real host writes into the tool renderer registry, as the loader does.
      mod.setup(createPluginUiHost("phone"));
    },
  );
}

describe("PluginPanelContent", () => {
  beforeEach(async () => {
    clearPluginPanels();
    clearToolRenderers();
    requestMock.mockReset();
    requestMock.mockImplementation(async (path: string) => {
      if (path === "/settings/timezone") return { timezone: "Asia/Shanghai" };
      if (/^\/agents\/[^/]+$/.test(path)) return { name: "阿离" };
      if (path.includes("/workspace/file")) {
        return { path: STATE_PATH, content: JSON.stringify(state) };
      }
      throw new Error(`unexpected request: ${path}`);
    });
  });

  it("renders the declared renderer without any tool call", async () => {
    await installPhoneRenderer();
    registerPluginPanel({
      pluginId: "phone",
      id: "phone",
      renderer: "phone_card",
      title: "小手机",
    });

    render(
      <PluginPanelContent pluginId="phone" panelId="phone" agentId="agent-1" />,
    );

    // The panel mounts on the phone home screen and reads the workspace file of
    // the agent it was given — no tool call, no callId.
    expect(await screen.findByText("阿离 的手机")).toBeInTheDocument();
    expect(
      requestMock.mock.calls.some((call) =>
        String(call[0]).includes("/agents/agent-1/workspace/file"),
      ),
    ).toBe(true);

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "微信" }));
    expect(await screen.findByText("晚上去看电影吗")).toBeInTheDocument();
  });

  it("explains itself when the panel is not declared", async () => {
    render(
      <PluginPanelContent
        pluginId="phone"
        panelId="missing"
        agentId="agent-1"
      />,
    );
    expect(await screen.findByText(/该面板所属插件未加载/)).toBeInTheDocument();
  });

  it("explains itself when the declared renderer is not registered", async () => {
    registerPluginPanel({
      pluginId: "phone",
      id: "phone",
      renderer: "phone_card",
      title: "小手机",
    });
    render(
      <PluginPanelContent pluginId="phone" panelId="phone" agentId="agent-1" />,
    );
    expect(await screen.findByText(/该面板所属插件未加载/)).toBeInTheDocument();
  });
});
