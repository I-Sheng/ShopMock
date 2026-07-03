'use client';

import { createContext, useContext, useEffect, useRef, useState } from 'react';
import Keycloak from 'keycloak-js';

// Same-origin as the storefront: the edge routes /auth -> Keycloak, so there is
// no CORS and the realm is reached at http://<host>/auth.
const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const kcRef = useRef(null);
  const [ready, setReady] = useState(false);
  const [authenticated, setAuthenticated] = useState(false);
  const [profile, setProfile] = useState(null);

  useEffect(() => {
    if (kcRef.current) return; // guard StrictMode double-invoke
    const kc = new Keycloak({
      url: `${window.location.origin}/auth`,
      realm: 'shopmock',
      clientId: 'storefront',
    });
    kcRef.current = kc;

    kc.init({
      onLoad: 'check-sso',
      pkceMethod: 'S256',
      checkLoginIframe: false,
      silentCheckSsoRedirectUri: `${window.location.origin}/silent-check-sso.html`,
    })
      .then((auth) => {
        setAuthenticated(auth);
        if (auth) {
          const t = kc.tokenParsed || {};
          setProfile({ name: t.name, email: t.email, username: t.preferred_username });
        }
        setReady(true);
      })
      .catch(() => setReady(true));
  }, []);

  const login = () => kcRef.current?.login();
  const register = () => kcRef.current?.register();
  const logout = () =>
    kcRef.current?.logout({ redirectUri: window.location.origin + '/' });

  // Ensure a fresh token, then issue a same-origin API call with the bearer.
  const authFetch = async (path, opts = {}) => {
    const kc = kcRef.current;
    if (!kc || !kc.authenticated) throw new Error('not authenticated');
    await kc.updateToken(30).catch(() => {});
    return fetch(path, {
      ...opts,
      headers: {
        'Content-Type': 'application/json',
        ...(opts.headers || {}),
        Authorization: `Bearer ${kc.token}`,
      },
    });
  };

  return (
    <AuthContext.Provider
      value={{ ready, authenticated, profile, login, register, logout, authFetch }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export const useAuth = () => useContext(AuthContext);
