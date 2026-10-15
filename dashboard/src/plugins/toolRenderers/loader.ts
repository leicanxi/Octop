import { getApiUrl } from "../../api/config";
import { getAuthToken } from "../../api/request";
import type { InstalledPlugin } from "../../api/modules/plugins";
import { createPluginUiHost } from "./host";
import { registerPluginPanel, unregisterPluginPanels } from "./panelRegistry";
import { unregisterPluginRenderers } from "./registry";
import type { PluginUiModule } from "./types";

const loadedPlugins = new Set<string>();
const blobUrls: string[] = [];

async function fetchAuthedText(path: string): Promise<string> {
  const url = getApiUrl(path);
  const token = getAuthToken();
  const headers: Record<string, string> = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(url, { headers });
  if (!res.ok) {
    throw new Error(`plugin UI fetch failed: ${res.status} ${path}`);
  }
  return res.text();
}

/** ``plugin.yaml`` ``ui.*`` path → its ``/api/plugins/{id}/ui/…`` API path. */
function pluginUiApiPath(pluginId: string, relPath: string): string {
  // ``ui/dist/index.js`` and ``dist/index.js`` are both served under ``…/ui/``.
  const normalized = relPath.replace(/^\/+/, "");
  const underUi = normalized.startsWith("ui/")
    ? normalized.slice(3)
    : normalized;
  return `/plugins/${encodeURIComponent(pluginId)}/ui/${underUi}`;
}

/**
 * Register the persistent panels a plugin declares in ``ui/manifest.json``.
 *
 * The manifest is optional (``plugin.yaml`` may omit ``ui.manifest``); a
 * missing or malformed one simply means the plugin ships no panel.
 */
async function registerDeclaredPanels(
  pluginId: string,
  icon: string | null | undefined,
  manifestRel: string | null | undefined,
): Promise<void> {
  if (!manifestRel) return;
  let raw: unknown;
  try {
    raw = JSON.parse(
      await fetchAuthedText(pluginUiApiPath(pluginId, manifestRel)),
    );
  } catch {
    return;
  }
  const declared = (raw as { panels?: unknown } | null)?.panels;
  if (!Array.isArray(declared)) return;
  for (const entry of declared) {
    const id = typeof entry?.id === "string" ? entry.id.trim() : "";
    const renderer =
      typeof entry?.renderer === "string" ? entry.renderer.trim() : "";
    if (!id || !renderer) continue;
    const title =
      typeof entry?.title === "string" ? entry.title.trim() : undefined;
    registerPluginPanel({
      pluginId,
      id,
      renderer,
      title: title || undefined,
      icon,
    });
  }
}

/**
 * Dynamically import a plugin ESM via authenticated fetch + blob URL.
 * Plugins must ship a self-contained ``ui/dist/index.js`` (no relative imports).
 */
async function importPluginEsm(
  pluginId: string,
  entryRel: string,
): Promise<PluginUiModule> {
  const source = await fetchAuthedText(pluginUiApiPath(pluginId, entryRel));
  const blob = new Blob([source], { type: "text/javascript" });
  const blobUrl = URL.createObjectURL(blob);
  blobUrls.push(blobUrl);
  return (await import(/* @vite-ignore */ blobUrl)) as PluginUiModule;
}

function runSetup(mod: PluginUiModule, pluginId: string): void {
  const host = createPluginUiHost(pluginId);
  const setup = mod.setup ?? mod.default?.setup;
  if (typeof setup === "function") {
    setup(host);
  }
}

/** Load UI modules for installed plugins that declare ``ui.entry``. */
export async function loadInstalledPluginUis(
  plugins: InstalledPlugin[],
): Promise<void> {
  for (const plugin of plugins) {
    if (plugin.error || !plugin.ui?.entry) continue;
    if (loadedPlugins.has(plugin.id)) continue;
    try {
      const mod = await importPluginEsm(plugin.id, plugin.ui.entry);
      unregisterPluginRenderers(plugin.id);
      unregisterPluginPanels(plugin.id);
      runSetup(mod, plugin.id);
      await registerDeclaredPanels(plugin.id, plugin.icon, plugin.ui.manifest);
      loadedPlugins.add(plugin.id);
    } catch (err) {
      console.warn(`[plugin-ui] failed to load ${plugin.id}:`, err);
    }
  }
}

/**
 * Force re-fetch and re-register UI modules (after install/uninstall in Admin).
 * Keeps builtin renderers; drops previously loaded third-party UI blobs.
 */
export async function reloadPluginToolUis(
  plugins: InstalledPlugin[],
): Promise<void> {
  resetPluginUiLoader();
  await loadInstalledPluginUis(plugins);
}

/** Drop cached load state (e.g. after uninstall); blob URLs are revoked. */
export function resetPluginUiLoader(): void {
  for (const id of loadedPlugins) {
    unregisterPluginRenderers(id);
    unregisterPluginPanels(id);
  }
  loadedPlugins.clear();
  while (blobUrls.length) {
    const url = blobUrls.pop();
    if (url) URL.revokeObjectURL(url);
  }
}

export function isPluginUiLoaded(pluginId: string): boolean {
  return loadedPlugins.has(pluginId);
}
