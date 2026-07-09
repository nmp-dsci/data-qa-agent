// Auth abstraction: one interface, two backends chosen at runtime by /auth/config.
//   dev    -> the local dev-auth stub (pick a seeded test user)
//   google -> Google Sign-in via Google Identity Services (real OIDC sign-in)
// The GIS script is loaded lazily so dev never pays for it.
import { AuthConfig, devLogin, getAuthConfig, getMe, setToken, User } from "./api";

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

/**
 * Render the official Google button into `el`. On sign-in, GIS hands back an
 * ID token (the credential); we set it as the bearer token and exchange it for
 * our app profile via /me (which validates it and JIT-provisions the user).
 */
export async function renderGoogleButton(
  el: HTMLElement,
  onUser: (user: User) => void,
  onError: (error: Error) => void,
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
          setToken(resp.credential);
          onUser(await getMe());
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

export async function logout(): Promise<void> {
  setToken(null);
  window.google?.accounts.id.disableAutoSelect?.();
}
