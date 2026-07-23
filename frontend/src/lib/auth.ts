// Auth abstraction: one interface, two backends chosen at runtime by /auth/config.
//   dev    -> the local dev-auth stub (pick a seeded test user)
//   google -> Google Sign-in via Google Identity Services (real OIDC sign-in)
// The GIS script is loaded lazily so dev never pays for it.
import {
  ApiError,
  AuthConfig,
  devLogin,
  getAuthConfig,
  getMe,
  logoutSession,
  setToken,
  User,
  WARMING_MAX_MS,
  WARMING_RETRY_MS,
} from "./api";

/** Sign-in progress the login card narrates (s29): "signing" the moment Google
 *  hands back a credential, "warming" while the backend waits out an Aurora
 *  resume. Terminal outcomes still arrive via onUser/onError. */
export type LoginStatus = { phase: "signing" } | { phase: "warming"; waitedS: number };

// Minimal typing for the slice of Google Identity Services we use.
interface GoogleIdApi {
  initialize(config: {
    client_id: string;
    callback: (resp: { credential?: string }) => void;
  }): void;
  renderButton(el: HTMLElement, options: Record<string, unknown>): void;
  disableAutoSelect?(): void;
}

declare global {
  interface Window {
    google?: { accounts: { id: GoogleIdApi } };
  }
}

let cachedConfig: AuthConfig | null = null;
let gisLoading: Promise<void> | null = null;

export async function loadAuthConfig(): Promise<AuthConfig> {
  if (!cachedConfig) cachedConfig = await getAuthConfig();
  return cachedConfig;
}

/** Load the Google Identity Services client script once. */
function loadGis(): Promise<void> {
  if (gisLoading) return gisLoading;
  gisLoading = new Promise<void>((resolve, reject) => {
    if (window.google?.accounts?.id) {
      resolve();
      return;
    }
    const script = document.createElement("script");
    script.src = "https://accounts.google.com/gsi/client";
    script.async = true;
    script.defer = true;
    script.onload = () => resolve();
    script.onerror = () => reject(new Error("Failed to load Google Sign-in"));
    document.head.appendChild(script);
  });
  return gisLoading;
}

// s29: while Aurora Serverless resumes from auto-pause the backend answers
// 503 db_warming (a classified connect failure, not a real error). getMe skips
// the transport-level retry in apiFetch so the loop below sees every 503 and
// can narrate the wait — the credential is already in hand, so waiting costs
// the user nothing and never re-opens the Google prompt.
function isDbWarming(e: unknown): boolean {
  return e instanceof ApiError && e.status === 503 && e.detail === "db_warming";
}

/** Exchange the stored credential for the app profile, waiting out a database
 *  wake: retry /me while the backend reports db_warming, narrating progress
 *  through onStatus. Anything else — or exhausting the window — is terminal. */
async function exchangeCredential(
  onUser: (user: User) => void,
  onError: (error: Error) => void,
  onStatus: (status: LoginStatus) => void,
): Promise<void> {
  const started = Date.now();
  for (;;) {
    try {
      onUser(await getMe());
      return;
    } catch (e) {
      const waited = Date.now() - started;
      if (!isDbWarming(e)) {
        onError(e as Error);
        return;
      }
      if (waited >= WARMING_MAX_MS) {
        onError(new Error("The database is taking unusually long to wake — please try again."));
        return;
      }
      onStatus({ phase: "warming", waitedS: Math.round(waited / 1000) });
      await new Promise((resolve) => setTimeout(resolve, WARMING_RETRY_MS));
    }
  }
}

/**
 * Render the official Google button into `el`. On sign-in, GIS hands back an
 * ID token (the credential); we set it as the bearer token and exchange it for
 * our app profile via /me (which validates it and JIT-provisions the user).
 * `onStatus` drives the card's pending UI from the instant the credential
 * arrives — without it a login that waits on the database looks identical to a
 * dead button, which is what produced prod's 4-5-attempt sessions (s29).
 */
export async function renderGoogleButton(
  el: HTMLElement,
  onUser: (user: User) => void,
  onError: (error: Error) => void,
  onStatus: (status: LoginStatus) => void,
): Promise<void> {
  const config = await loadAuthConfig();
  if (config.auth_mode !== "google" || !config.client_id) return;
  await loadGis();
  const id = window.google?.accounts.id;
  if (!id) throw new Error("Google Sign-in unavailable");
  id.initialize({
    client_id: config.client_id,
    callback: (resp) => {
      void (async () => {
        try {
          if (!resp.credential) throw new Error("No credential returned by Google");
          onStatus({ phase: "signing" });
          setToken(resp.credential);
          await exchangeCredential(onUser, onError, onStatus);
        } catch (e) {
          onError(e as Error);
        }
      })();
    },
  });
  id.renderButton(el, { theme: "filled_blue", size: "large", type: "standard", width: 260 });
}

/** Dev-auth stub sign-in as a seeded test user. */
export async function loginDev(username: string): Promise<User> {
  const { access_token, user } = await devLogin(username);
  setToken(access_token);
  return user;
}

/**
 * Resume a session left over from a previous page load. The in-memory bearer
 * token (`setToken`) does not survive a reload — it is a plain JS variable —
 * but dev-mode also sets an httpOnly session cookie on login, which does. This
 * is what lets a reload skip the login screen instead of dropping back to it
 * even though the session is still valid for hours.
 *
 * Google mode has no equivalent yet: the ID token lives only in memory, so a
 * reload always requires signing in again there. Returns null (not a throw)
 * on any failure, including "no session" — the 401 from a cookie-less /me is
 * the expected, common case on a first visit, not an error to log.
 */
export async function resumeSession(): Promise<User | null> {
  try {
    return await getMe();
  } catch {
    return null;
  }
}

export async function logout(): Promise<void> {
  setToken(null);
  window.google?.accounts.id.disableAutoSelect?.();
  // Best-effort: the dev-mode session cookie (if any) is httpOnly, so clearing
  // it needs a round trip. A failed call (e.g. Google mode, where the endpoint
  // has nothing to clear but still exists) must not stop local sign-out.
  try {
    await logoutSession();
  } catch {
    // already signed out locally; nothing more to do
  }
}
