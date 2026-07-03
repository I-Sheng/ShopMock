'use client';

import { useCallback, useEffect, useState } from 'react';
import { useAuth } from '../auth-context';

function money(cents) {
  return `$${(cents / 100).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

export default function OrdersPage() {
  const { ready, authenticated, login, authFetch } = useAuth();
  const [orders, setOrders] = useState(null);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    try {
      // resolve this user's customer id, then fetch their orders with the token
      const cidRes = await authFetch('/api/customers/rpc/ensure_customer', {
        method: 'POST',
        body: '{}',
      });
      const cid = JSON.parse(await cidRes.text());
      const res = await authFetch(
        `/api/orders/orders?customer_ref=eq.${cid}&order=id.desc`
      );
      setOrders(res.ok ? await res.json() : []);
    } catch (e) {
      setError(String(e.message || e));
      setOrders([]);
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
          <h1>Sign in to see your orders</h1>
          <button className="checkout-btn" onClick={login}>Sign in</button>
        </div>
      </main>
    );
  }

  return (
    <main className="main">
      <h1 className="page-title">Your Orders</h1>
      {error && <p className="co-error">{error}</p>}
      {orders === null ? (
        <p className="results-line">Loading orders…</p>
      ) : orders.length === 0 ? (
        <div className="error-box">
          <h2>No orders yet</h2>
          <p><a className="linky" href="/">Start shopping</a></p>
        </div>
      ) : (
        <div className="order-list">
          {orders.map((o) => (
            <div className="order-card" key={o.id}>
              <div>
                <span className="order-id">Order #{o.id}</span>
                <span className={`order-status ${o.status}`}>{o.status}</span>
              </div>
              <div className="order-meta">
                <span>{new Date(o.placed_at).toLocaleDateString()}</span>
                <strong>{money(o.total_cents)}</strong>
              </div>
            </div>
          ))}
        </div>
      )}
    </main>
  );
}
