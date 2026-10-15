import { useMemo, useSyncExternalStore } from "react";
import {
  getPluginPanelVersion,
  listPluginPanels,
  subscribePluginPanels,
  type PluginPanelRegistration,
} from "./panelRegistry";

/** Plugin panels declared by installed plugins (re-renders on load/unload). */
export function usePluginPanels(): PluginPanelRegistration[] {
  const version = useSyncExternalStore(
    subscribePluginPanels,
    getPluginPanelVersion,
    getPluginPanelVersion,
  );
  // eslint-disable-next-line react-hooks/exhaustive-deps -- registry version is the identity of the snapshot
  return useMemo(() => listPluginPanels(), [version]);
}
