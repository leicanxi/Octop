/** Catalog of App-ID OAuth providers shown under the admin SSO OAuth family. */

export type OauthAppKind = "feishu" | "dingtalk" | "wecom" | "flowbee";

export interface OauthProviderDef {
  kind: OauthAppKind;
  /** When false, show a collapsed placeholder until the adapter ships. */
  available: boolean;
  titleKey: string;
  descKey: string;
  /** Fallback login button label when display_name is empty. */
  defaultNameKey: string;
  enabledAriaKey: string;
  /** Feishu/Lark region selector. */
  hasRegion?: boolean;
  /** WeCom CorpApp Agent ID in ``extra.agent_id``. */
  hasAgentId?: boolean;
  /**
   * Shared-secret handoff provider (FlowBee): the handoff page sits in
   * ``extra.handoff_url`` and there is no client id to register.
   */
  hasHandoffUrl?: boolean;
  /** Provider registers no client id (FlowBee). Defaults to true. */
  hasClientId?: boolean;
  /** Override default App ID field label (e.g. WeCom CorpID). */
  clientIdKey?: string;
  /** Override default App Secret field label. */
  clientSecretKey?: string;
  /** Override the helper text under the secret field. */
  clientSecretHintKey?: string;
  /** Override the “credentials” section heading and its hint. */
  sectionCredentialsKey?: string;
  sectionCredentialsHintKey?: string;
  /** Override the first checklist step (defaults to “Fill App ID & secret”). */
  guideStep1Key?: string;
  /**
   * WeCom registers a host-only “授权回调域”, not the full callback path.
   * When true, the admin aside also shows the hostname to copy.
   */
  showCallbackDomain?: boolean;
  redirectHintKey?: string;
  redirectDocsKey?: string;
}

export const OAUTH_APP_PROVIDERS: OauthProviderDef[] = [
  {
    kind: "feishu",
    available: true,
    titleKey: "adminSso.feishuTitle",
    descKey: "adminSso.feishuDesc",
    defaultNameKey: "login.providerKind.feishu",
    enabledAriaKey: "adminSso.feishuEnabled",
    hasRegion: true,
  },
  {
    kind: "dingtalk",
    available: true,
    titleKey: "adminSso.dingtalkTitle",
    descKey: "adminSso.dingtalkDesc",
    defaultNameKey: "login.providerKind.dingtalk",
    enabledAriaKey: "adminSso.dingtalkEnabled",
  },
  {
    kind: "wecom",
    available: true,
    titleKey: "adminSso.wecomTitle",
    descKey: "adminSso.wecomDesc",
    defaultNameKey: "login.providerKind.wecom",
    enabledAriaKey: "adminSso.wecomEnabled",
    hasAgentId: true,
    clientIdKey: "adminSso.wecomCorpId",
    clientSecretKey: "adminSso.wecomSecret",
    showCallbackDomain: true,
    redirectHintKey: "adminSso.wecomRedirectHint",
    redirectDocsKey: "adminSso.wecomRedirectDocs",
  },
  {
    kind: "flowbee",
    available: true,
    titleKey: "adminSso.flowbeeTitle",
    descKey: "adminSso.flowbeeDesc",
    defaultNameKey: "login.providerKind.flowbee",
    enabledAriaKey: "adminSso.flowbeeEnabled",
    hasHandoffUrl: true,
    hasClientId: false,
    clientSecretKey: "adminSso.flowbeeSecret",
    clientSecretHintKey: "adminSso.flowbeeSecretHint",
    sectionCredentialsKey: "adminSso.flowbeeSectionCredentials",
    sectionCredentialsHintKey: "adminSso.flowbeeSectionCredentialsHint",
    guideStep1Key: "adminSso.flowbeeGuideStep1",
    redirectHintKey: "adminSso.flowbeeRedirectHint",
    redirectDocsKey: "adminSso.flowbeeRedirectDocs",
  },
];
