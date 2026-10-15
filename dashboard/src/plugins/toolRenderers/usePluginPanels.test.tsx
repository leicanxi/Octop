import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import {
  clearPluginPanels,
  registerPluginPanel,
  unregisterPluginPanels,
} from "./panelRegistry";
import { usePluginPanels } from "./usePluginPanels";

const phonePanel = {
  pluginId: "phone",
  id: "phone",
  renderer: "phone_card",
  title: "小手机",
  icon: "/api/plugins/phone/ui/icon.svg",
};

/** Stands in for the chat toolbar / agent card, which only list panels. */
function PanelListProbe() {
  const panels = usePluginPanels();
  return (
    <ul data-testid="panels">
      {panels.map((panel) => (
        <li key={`${panel.pluginId}:${panel.id}`}>{panel.id}</li>
      ))}
    </ul>
  );
}

function renderedPanelIds(): string[] {
  return [...screen.getByTestId("panels").querySelectorAll("li")].map(
    (node) => node.textContent ?? "",
  );
}

afterEach(() => {
  clearPluginPanels();
});

describe("usePluginPanels", () => {
  it("starts empty and fills in when a plugin registers after mount", () => {
    render(<PanelListProbe />);
    expect(renderedPanelIds()).toEqual([]);

    // Plugin UI modules are fetched asynchronously, so registration always
    // lands after the first render of the page that shows the entries.
    act(() => {
      registerPluginPanel(phonePanel);
    });

    expect(renderedPanelIds()).toEqual(["phone"]);
  });

  it("drops the entries when a plugin is unloaded", () => {
    render(<PanelListProbe />);
    act(() => {
      registerPluginPanel(phonePanel);
    });
    expect(renderedPanelIds()).toEqual(["phone"]);

    act(() => {
      unregisterPluginPanels("phone");
    });

    expect(renderedPanelIds()).toEqual([]);
  });
});
