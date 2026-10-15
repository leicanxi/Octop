/**
 * Registry for persistent plugin panels.
 *
 * A panel is declared in a plugin's ``ui/manifest.json`` (``panels``) and
 * rendered by a renderer the plugin registered from its UI module, so it can
 * be hosted without any tool call: chat dock tab, chat toolbar, agent card.
 */
import type { PluginPanelDeclaration } from "./types";

export interface PluginPanelRegistration extends PluginPanelDeclaration {
  pluginId: string;
  /** Plugin icon for labels (emoji, absolute image URL, or ``/api/…`` path). */
  icon?: string | null;
}

const byKey = new Map<string, PluginPanelRegistration>();
const listeners = new Set<() => void>();
let version = 0;

function panelKey(pluginId: string, panelId: string): string {
  return `${pluginId}::${panelId}`;
}

function bump(): void {
  version += 1;
  for (const fn of listeners) {
    try {
      fn();
    } catch {
      /* ignore */
    }
  }
}

export function registerPluginPanel(panel: PluginPanelRegistration): void {
  byKey.set(panelKey(panel.pluginId, panel.id), panel);
  bump();
}

export function unregisterPluginPanels(pluginId: string): void {
  let changed = false;
  for (const [key, panel] of [...byKey.entries()]) {
    if (panel.pluginId === pluginId) {
      byKey.delete(key);
      changed = true;
    }
  }
  if (changed) bump();
}

export function listPluginPanels(): PluginPanelRegistration[] {
  return [...byKey.values()];
}

export function findPluginPanel(
  pluginId: string,
  panelId: string,
): PluginPanelRegistration | null {
  return byKey.get(panelKey(pluginId, panelId)) ?? null;
}

/** Stable dock tab id for one plugin panel. */
export function pluginPanelTabId(pluginId: string, panelId: string): string {
  return `panel:${pluginId}:${panelId}`;
}

/** Inverse of :func:`pluginPanelTabId`; ``null`` when *id* is not a panel tab. */
export function parsePluginPanelTabId(
  id: string,
): { pluginId: string; panelId: string } | null {
  const prefix = "panel:";
  if (!id.startsWith(prefix)) return null;
  const rest = id.slice(prefix.length);
  const at = rest.indexOf(":");
  if (at <= 0) return null;
  const pluginId = rest.slice(0, at);
  const panelId = rest.slice(at + 1);
  if (!pluginId || !panelId) return null;
  return { pluginId, panelId };
}

/** Translator shape shared by the panel hosts (i18next ``t`` with fallback). */
export type PanelTranslator = (key: string, defaultValue: string) => string;

/** Dashboard i18n wins; the plugin-provided title is the fallback. */
export function pluginPanelTitle(
  panel: PluginPanelRegistration,
  t: PanelTranslator,
): string {
  return t(`chat.panel.${panel.id}`, panel.title || panel.id);
}

/** Subscribe to registry changes (plugin UI load / unload). */
export function subscribePluginPanels(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function getPluginPanelVersion(): number {
  return version;
}

/** Drop every registration (tests). */
export function clearPluginPanels(): void {
  byKey.clear();
  bump();
}
