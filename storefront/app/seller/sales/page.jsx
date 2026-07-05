'use client';

import { useCallback, useEffect, useState } from 'react';
import { useSellerAuth } from '../seller-auth-context';

function money(cents) {
  return `$${(cents / 100).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

export default function SellerSalesPage() {
  const { ready, authenticated, login, authFetch } = useSellerAuth();
  const [sales, setSales] = useState(null);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    try {
      const res = await authFetch('/api/seller-backend/sales');
      if (!res.ok) throw new Error(`sales -> ${res.status} ${await res.text()}`);
      setSales(await res.json());
    } catch (e) {
      setError(String(e.message || e));
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
          <h1>Sign in to see your sales</h1>
          <button className="checkout-btn" onClick={login}>Sign in as a seller</button>
        </div>
      </main>
    );
  }

  return (
    <main className="main">
      <h1 className="page-title">Your Sales</h1>
      {error && <p className="co-error">{error}</p>}
      {sales === null ? (
        <p className="results-line">Loading sales…</p>
      ) : (
        <>
          <div className="tiles">
            <div className="tile">
              <span className="tile-num">{sales.total_units}</span>
              <span className="tile-label">Units sold</span>
            </div>
            <div className="tile">
              <span className="tile-num">{money(sales.gross_cents)}</span>
              <span className="tile-label">Gross sales</span>
            </div>
          </div>
          {sales.lines.length === 0 ? (
            <div className="error-box">
              <h2>No sales yet</h2>
              <p>
                Orders that include your SKUs will show up here.{' '}
                <a className="linky" href="/seller/listings">Manage listings</a>
              </p>
            </div>
          ) : (
            <div className="seller-tablewrap">
              <table className="seller-table">
                <thead>
                  <tr>
                    <th>Order</th>
                    <th>Date</th>
                    <th>Status</th>
                    <th>SKU</th>
                    <th className="num">Qty</th>
                    <th className="num">Unit price</th>
                    <th className="num">Line total</th>
                  </tr>
                </thead>
                <tbody>
                  {sales.lines.map((l, i) => (
                    <tr key={i}>
                      <td>#{l.order_id}</td>
                      <td>{new Date(l.placed_at).toLocaleDateString()}</td>
                      <td>
                        <span className={`order-status ${l.status}`}>{l.status}</span>
                      </td>
                      <td>{l.sku}</td>
                      <td className="num">{l.qty}</td>
                      <td className="num">{money(l.unit_price_cents)}</td>
                      <td className="num">{money(l.qty * l.unit_price_cents)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </main>
  );
}
