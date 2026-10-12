/**
 * Shared trigger for the avatar menu's "My API Key" panel.
 *
 * The backend refuses model calls from a user who has not stored their own key
 * (``MODEL_CREDENTIAL_REQUIRED``). When a live stream reports that code, the
 * chat store asks the avatar menu to open its credential panel, so the user
 * lands on the form instead of hunting for it.
 */

/** Error code raised by the backend when the user's own key is required. */
export const MODEL_CREDENTIAL_REQUIRED_CODE = "MODEL_CREDENTIAL_REQUIRED";

/** Dispatched on ``window`` to open the credential panel. */
export const OPEN_MODEL_CREDENTIAL_EVENT = "octop:open-model-credential";

/** Ask the avatar menu to open the credential panel. */
export function openModelCredentialPanel(): void {
  window.dispatchEvent(new Event(OPEN_MODEL_CREDENTIAL_EVENT));
}
