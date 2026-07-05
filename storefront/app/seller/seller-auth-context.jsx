'use client';

import { createContext, useContext, useEffect, useRef, useState } from 'react';
import Keycloak from 'keycloak-js';

// Seller Central signs in through the `seller-dashboard` Keycloak client, which
// stamps `role: seller` into the token — the customer storefront uses the
// `storefront` client (`role: customer`). Same realm, same edge-routed /auth,
// different client and different landing page. The customer AuthProvider skips
// initialization under /seller so this instance owns the OIDC callback here.
const SellerAuthContext = createContext(null);

export function SellerAuthProvider({ children }) {
  const kcRef = useRef(null);
  const [ready, setReady] = useState(false);
  const [authenticated, setAuthenticated] = useState(false);
  const [profile, setProfile] = useState(null);

  useEffect(() => {
    if (kcRef.current) return; // guard StrictMode double-invoke
    const kc = new Keycloak({
      url: `${window.location.origin}/auth`,
      realm: 'shopmock',
      clientId: 'seller-dashboard',
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

  const login = () =>
    kcRef.current?.login({ redirectUri: window.location.origin + '/seller' });
  const logout = () =>
    kcRef.current?.logout({ redirectUri: window.location.origin + '/seller' });

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
    <SellerAuthContext.Provider
      value={{ ready, authenticated, profile, login, logout, authFetch }}
    >
      {children}
    </SellerAuthContext.Provider>
  );
}

export const useSellerAuth = () => useContext(SellerAuthContext);
