'use client';

import { useCallback, useEffect, useState } from 'react';
import { useAuth } from '../auth-context';
import WrongRoleGate from '../wrong-role-gate';

function money(cents) {
  return `$${(cents / 100).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

// One order row. "Details" lazily loads the line items (orders DB) and their
// product names (catalog DB, keyed by SKU — the two live in separate databases).
function OrderCard({ order, authFetch }) {
  const [open, setOpen] = useState(false);
  const [lines, setLines] = useState(null);
  const [error, setError] = useState(null);

  const toggle = async () => {
    const next = !open;
    setOpen(next);
    if (!next || lines) return;
    try {
      const res = await authFetch(
        `/api/orders/order_items?order_id=eq.${order.id}&select=id,product_sku,qty,unit_price_cents&order=id`
      );
      if (!res.ok) throw new Error(`order items -> ${res.status}`);
      const items = await res.json();
      const skus = [...new Set(items.map((i) => i.product_sku))];
      let names = {};
      if (skus.length) {
        const q = skus.map((s) => `"${s.replace(/"/g, '')}"`).join(',');
        const pRes = await fetch(
          `/api/catalog/products?sku=in.(${encodeURIComponent(q)})&select=sku,name`,
          { cache: 'no-store' }
        );
        if (pRes.ok) names = Object.fromEntries((await pRes.json()).map((p) => [p.sku, p.name]));
      }
      setLines(items.map((i) => ({ ...i, name: names[i.product_sku] || i.product_sku })));
    } catch (e) {
      setError(String(e.message || e));
    }
  };

  const subtotal = (lines || []).reduce((s, l) => s + l.qty * l.unit_price_cents, 0);

  return (
    <div className="order-card">
      <div>
        <span className="order-id">Order #{order.id}</span>
        <span className={`order-status ${order.status}`}>{order.status}</span>
      </div>
      <div className="order-meta">
        <span>{new Date(order.placed_at).toLocaleDateString()}</span>
        <strong>{money(order.total_cents)}</strong>
        <button
          className="order-detail-btn"
          onClick={toggle}
          aria-expanded={open}
          aria-controls={`order-${order.id}-details`}
        >
          {open ? 'Hide details' : 'Details'}
        </button>
      </div>
      {open && (
        <div className="order-details" id={`order-${order.id}-details`}>
          {error ? (
            <p className="co-error">{error}</p>
          ) : lines === null ? (
            <p className="results-line">Loading items…</p>
          ) : (
            <>
              <div className="od-row od-head">
                <span>Item</span><span>Qty</span><span>Price</span><span>Subtotal</span>
              </div>
              {lines.map((l) => (
                <div className="od-row" key={l.id}>
                  <span className="od-name">
                    {l.name}
                    <small>{l.product_sku}</small>
                  </span>
                  <span>{l.qty}</span>
                  <span>{money(l.unit_price_cents)}</span>
                  <span>{money(l.qty * l.unit_price_cents)}</span>
                </div>
              ))}
              <div className="od-total od-sub">
                <span>Items subtotal</span><span>{money(subtotal)}</span>
              </div>
              <div className="od-total od-grand">
                <span>Order total</span><strong>{money(order.total_cents)}</strong>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}

export default function OrdersPage() {
  const { ready, authenticated, authorized, profile, login, logout, authFetch } = useAuth();
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
    if (ready && authenticated && authorized) load();
  }, [ready, authenticated, authorized, load]);

  if (!ready) return <main className="main"><p className="results-line">Loading…</p></main>;

  if (authenticated && !authorized) {
    return (
      <WrongRoleGate
        area="the ShopMock store"
        required="customer"
        profile={profile}
        logout={logout}
        homeHref="/seller"
        homeLabel="Go to Seller Central"
      />
    );
  }

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
            <OrderCard key={o.id} order={o} authFetch={authFetch} />
          ))}
        </div>
      )}
    </main>
  );
}
