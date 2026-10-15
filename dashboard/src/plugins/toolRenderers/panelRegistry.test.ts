import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  clearPluginPanels,
  findPluginPanel,
  listPluginPanels,
  parsePluginPanelTabId,
  pluginPanelTabId,
  pluginPanelTitle,
  registerPluginPanel,
  subscribePluginPanels,
  unregisterPluginPanels,
} from "./panelRegistry";

const phonePanel = {
  pluginId: "phone",
  id: "phone",
  renderer: "phone_card",
  title: "小手机",
  icon: "/api/plugins/phone/ui/icon.svg",
};

describe("plugin panel registry", () => {
  beforeEach(() => {
    clearPluginPanels();
  });

  it("registers a panel and finds it by plugin + panel id", () => {
    registerPluginPanel(phonePanel);
    expect(listPluginPanels()).toEqual([phonePanel]);
    expect(findPluginPanel("phone", "phone")).toEqual(phonePanel);
    expect(findPluginPanel("phone", "missing")).toBeNull();
    expect(findPluginPanel("other", "phone")).toBeNull();
  });

  it("keeps the last declaration for the same plugin + panel", () => {
    registerPluginPanel(phonePanel);
    registerPluginPanel({ ...phonePanel, renderer: "other_renderer" });
    expect(listPluginPanels()).toHaveLength(1);
    expect(findPluginPanel("phone", "phone")?.renderer).toBe("other_renderer");
  });

  it("drops every panel of one plugin on unregister", () => {
    registerPluginPanel(phonePanel);
    registerPluginPanel({ ...phonePanel, id: "second" });
    registerPluginPanel({ ...phonePanel, pluginId: "other" });
    unregisterPluginPanels("phone");
    expect(listPluginPanels().map((p) => `${p.pluginId}:${p.id}`)).toEqual([
      "other:phone",
    ]);
  });

  it("notifies subscribers on register and unregister", () => {
    const listener = vi.fn();
    const unsubscribe = subscribePluginPanels(listener);
    registerPluginPanel(phonePanel);
    expect(listener).toHaveBeenCalledTimes(1);
    unregisterPluginPanels("phone");
    expect(listener).toHaveBeenCalledTimes(2);
    unsubscribe();
    registerPluginPanel(phonePanel);
    expect(listener).toHaveBeenCalledTimes(2);
  });

  it("round-trips panel dock tab ids and rejects other ids", () => {
    expect(parsePluginPanelTabId(pluginPanelTabId("phone", "phone"))).toEqual({
      pluginId: "phone",
      panelId: "phone",
    });
    expect(parsePluginPanelTabId("workspace")).toBeNull();
    expect(parsePluginPanelTabId("panel:phone")).toBeNull();
    expect(parsePluginPanelTabId("panel::phone")).toBeNull();
  });

  it("prefers the dashboard label and falls back to the plugin title", () => {
    const translated = vi.fn((_key: string, _fallback: string) => "小手机");
    expect(pluginPanelTitle(phonePanel, translated)).toBe("小手机");
    expect(translated).toHaveBeenCalledWith("chat.panel.phone", "小手机");

    const untranslated = vi.fn((_key: string, fallback: string) => fallback);
    expect(pluginPanelTitle(phonePanel, untranslated)).toBe("小手机");
    expect(
      pluginPanelTitle({ ...phonePanel, title: undefined }, untranslated),
    ).toBe("phone");
  });
});
