/**
 * Host for a persistent plugin panel body.
 *
 * Mounts the renderer a plugin registered (the ``renderer`` id from its
 * ``ui/manifest.json`` ``panels`` entry) without any tool call, so the same
 * panel can live in a chat dock tab and in the agent-card drawer.
 */
import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { createPluginUiHost } from "./host";
import { findPluginPanel } from "./panelRegistry";
import { resolveToolRenderer } from "./registry";
import { ToolUiErrorBoundary } from "./ToolUiErrorBoundary";
import type { ToolRenderProps } from "./types";
import { useToolRendererVersion } from "./useToolRendererVersion";

function PanelUnavailable({ hint }: { hint: string }) {
  return (
    <div
      style={{
        padding: 16,
        fontSize: 12.5,
        color: "var(--fn-text-secondary, #6b7280)",
      }}
    >
      {hint}
    </div>
  );
}

export default function PluginPanelContent({
  pluginId,
  panelId,
  agentId,
}: {
  pluginId: string;
  panelId: string;
  agentId: string | null;
}) {
  const { t } = useTranslation();
  // Plugin UIs load async after the host mounts — the version bump re-resolves.
  const rendererVersion = useToolRendererVersion();
  const panel = useMemo(
    () => findPluginPanel(pluginId, panelId),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- rendererVersion invalidates registry lookups
    [pluginId, panelId, rendererVersion],
  );
  const registration = useMemo(
    () =>
      panel
        ? resolveToolRenderer({
            pluginId,
            parsed: {
              isJson: true,
              raw: {},
              octopUi: { renderer: panel.renderer },
            },
          })
        : null,
    // eslint-disable-next-line react-hooks/exhaustive-deps -- rendererVersion invalidates registry lookups
    [pluginId, panel, rendererVersion],
  );
  const host = useMemo(() => createPluginUiHost(pluginId), [pluginId]);

  if (!panel || !registration) {
    return (
      <PanelUnavailable
        hint={t(
          "chat.panelUnavailable",
          "该面板所属插件未加载，请确认插件已安装并启用。",
        )}
      />
    );
  }

  const props: ToolRenderProps = {
    pluginId,
    toolName: "",
    status: "done",
    args: undefined,
    data: undefined,
    host,
    isStreaming: false,
    agentId,
  };
  const Component = registration.component;

  return (
    <ToolUiErrorBoundary propsForFallback={props}>
      <Component {...props} />
    </ToolUiErrorBoundary>
  );
}
