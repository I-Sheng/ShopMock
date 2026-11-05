'use client';

import { createContext, useContext, useEffect, useRef, useState } from 'react';
import Keycloak from 'keycloak-js';
import WrongRoleGate from '../wrong-role-gate';

// Seller Central signs in through the `seller-dashboard` Keycloak client, which
// derives `role: seller` from explicit seller client-role membership. The
// storefront derives `role: customer` from membership in the CIAM realm.
// different client and different landing page. The customer AuthProvider skips
// initialization under /seller so this instance owns the OIDC callback here.
const SellerAuthContext = createContext(null);

export function SellerAuthProvider({ children }) {
  const kcRef = useRef(null);
  const [ready, setReady] = useState(false);
  const [authenticated, setAuthenticated] = useState(false);
  const [profile, setProfile] = useState(null);
  // An SSO session alone is not enough: a customer signed in on the storefront
  // gets a seller-dashboard token via check-sso, but without the seller client
  // role. Such a session is authenticated yet NOT authorized for Seller Central.
  const [authorized, setAuthorized] = useState(false);

  useEffect(() => {
    if (kcRef.current) return; // guard StrictMode double-invoke
    const kc = new Keycloak({
      url: `${window.location.origin}/auth`,
      realm: 'shopmock-ciam',
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
          setAuthorized(kc.hasResourceRole('seller', 'seller-dashboard'));
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
    if (!kc.hasResourceRole('seller', 'seller-dashboard')) throw new Error('not a seller account');
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
      value={{ ready, authenticated, authorized, profile, login, logout, authFetch }}
    >
      {ready && authenticated && !authorized ? (
        <WrongRoleGate
          area="Seller Central"
          required="seller"
          profile={profile}
          logout={logout}
          homeHref="/"
          homeLabel="Go to the store"
        />
      ) : (
        children
      )}
    </SellerAuthContext.Provider>
  );
}

export const useSellerAuth = () => useContext(SellerAuthContext);
