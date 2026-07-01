// Auth abstraction: one interface, two backends chosen at runtime by /auth/config.
//   dev   -> the local dev-auth stub (pick a seeded test user)
//   entra -> Microsoft Entra External ID via MSAL (real OIDC sign-in)
// The MSAL library is imported lazily so dev never pays for it.
import type {
  AuthenticationResult,
  IPublicClientApplication,
} from "@azure/msal-browser";
import { AuthConfig, devLogin, getAuthConfig, getMe, setToken, User } from "./api";

let cachedConfig: AuthConfig | null = null;
let msal: IPublicClientApplication | null = null;
let entraScopes: string[] = [];

export async function loadAuthConfig(): Promise<AuthConfig> {
  if (!cachedConfig) cachedConfig = await getAuthConfig();
  return cachedConfig;
}

async function getMsal(config: AuthConfig): Promise<IPublicClientApplication> {
  if (msal) return msal;
  const { PublicClientApplication } = await import("@azure/msal-browser");
  entraScopes = config.scopes;
  msal = new PublicClientApplication({
    auth: {
      clientId: config.client_id ?? "",
      authority: config.authority ?? "",
      knownAuthorities: config.authority ? [new URL(config.authority).host] : [],
      redirectUri: window.location.origin,
    },
    cache: { cacheLocation: "sessionStorage" },
  });
  await msal.initialize();
  return msal;
}

async function hydrateFromResult(result: AuthenticationResult): Promise<User> {
  setToken(result.accessToken);
  return getMe();
}

/** Restore an existing session on page load. Returns the user, or null if none. */
export async function bootstrap(): Promise<User | null> {
  const config = await loadAuthConfig();
  if (config.auth_mode !== "entra") return null;
  const client = await getMsal(config);
  const account = client.getAllAccounts()[0];
  if (!account) return null;
  try {
    const result = await client.acquireTokenSilent({ scopes: entraScopes, account });
    return await hydrateFromResult(result);
  } catch {
    return null; // silent renewal failed -> user must sign in again
  }
}

/** Interactive Entra sign-in (popup). */
export async function loginEntra(): Promise<User> {
  const config = await loadAuthConfig();
  const client = await getMsal(config);
  const result = await client.loginPopup({ scopes: entraScopes });
  client.setActiveAccount(result.account);
  return hydrateFromResult(result);
}

/** Dev-auth stub sign-in as a seeded test user. */
export async function loginDev(username: string): Promise<User> {
  const { access_token, user } = await devLogin(username);
  setToken(access_token);
  return user;
}

export async function logout(): Promise<void> {
  setToken(null);
  if (msal) {
    const account = msal.getAllAccounts()[0];
    await msal.logoutPopup({ account }).catch(() => {});
  }
}
