'use client';

import { useCallback, useEffect, useState } from 'react';
import { useSellerAuth } from './seller-auth-context';

function money(cents) {
  return `$${(cents / 100).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

export default function SellerDashboard() {
  const { ready, authenticated, profile, login, authFetch } = useSellerAuth();
  const [listings, setListings] = useState(null);
  const [sales, setSales] = useState(null);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    try {
      // provision (idempotent) before anything else — a first-time seller
      // gets their seller row keyed on the verified token sub
      const ens = await authFetch('/api/seller-backend/sellers/ensure', {
        method: 'POST',
        body: '{}',
      });
      if (!ens.ok) throw new Error(`ensure -> ${ens.status} ${await ens.text()}`);
      const [lRes, sRes] = await Promise.all([
        authFetch('/api/seller-backend/listings'),
        authFetch('/api/seller-backend/sales'),
      ]);
      setListings(lRes.ok ? await lRes.json() : []);
      setSales(sRes.ok ? await sRes.json() : { lines: [], total_units: 0, gross_cents: 0 });
    } catch (e) {
      setError(String(e.message || e));
      setListings([]);
      setSales({ lines: [], total_units: 0, gross_cents: 0 });
    }
  }, [authFetch]);

  useEffect(() => {
    if (ready && authenticated) load();
  }, [ready, authenticated, load]);

  if (!ready) return <main className="main"><p className="results-line">Loading…</p></main>;

  if (!authenticated) {
    return (
      <main className="main">
        <div className="signin-gate">
          <h1>ShopMock Seller Central</h1>
          <p>
            Sign in with your seller account to add products and track how they
            sell. Seller accounts are separate from customer accounts.
          </p>
          <button className="checkout-btn" onClick={login}>Sign in as a seller</button>
          <p className="co-hint" style={{ marginTop: 14 }}>
            Buying instead? <a className="linky" href="/">Go to the store</a>
          </p>
        </div>
      </main>
    );
  }

  const firstName =
    (profile?.name && profile.name.split(' ')[0]) || profile?.username || 'seller';
  const active = listings ? listings.filter((l) => l.active).length : 0;

  return (
    <main className="main">
      <h1 className="page-title">Hello, {firstName}</h1>
      {error && <p className="co-error">{error}</p>}
      {listings === null || sales === null ? (
        <p className="results-line">Loading your dashboard…</p>
      ) : (
        <>
          <div className="tiles">
            <a className="tile" href="/seller/listings">
              <span className="tile-num">{listings.length}</span>
              <span className="tile-label">
                Listings ({active} active)
              </span>
            </a>
            <a className="tile" href="/seller/sales">
              <span className="tile-num">{sales.total_units}</span>
              <span className="tile-label">Units sold</span>
            </a>
            <a className="tile" href="/seller/sales">
              <span className="tile-num">{money(sales.gross_cents)}</span>
              <span className="tile-label">Gross sales</span>
            </a>
          </div>
          <div className="order-list">
            <div className="order-card">
              <div>
                <span className="order-id">Add a product</span>
                <span className="order-meta">create a listing with price and stock</span>
              </div>
              <a className="linky" href="/seller/listings">Manage listings →</a>
            </div>
            <div className="order-card">
              <div>
                <span className="order-id">Check your numbers</span>
                <span className="order-meta">every order line for your SKUs</span>
              </div>
              <a className="linky" href="/seller/sales">View sales →</a>
            </div>
          </div>
        </>
      )}
    </main>
  );
}
