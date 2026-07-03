'use client';

import { useEffect, useState } from 'react';
import { useCart } from '../cart-context';
import { useAuth } from '../auth-context';

function money(cents) {
  return `$${(cents / 100).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

async function rpc(authFetch, path, body) {
  const res = await authFetch(path, { method: 'POST', body: JSON.stringify(body || {}) });
  if (!res.ok) throw new Error(`${path} -> ${res.status} ${await res.text()}`);
  const text = await res.text();
  return text ? JSON.parse(text) : null;
}

export default function CheckoutPage() {
  const { items, count, clear } = useCart();
  const { ready, authenticated, profile, login, authFetch } = useAuth();
  const [products, setProducts] = useState(null);
  const [placing, setPlacing] = useState(false);
  const [error, setError] = useState(null);
  const [orderId, setOrderId] = useState(null);

  const idKey = Object.keys(items).sort().join(',');

  useEffect(() => {
    const ids = Object.keys(items);
    if (ids.length === 0) {
      setProducts([]);
      return;
    }
    fetch(
      `/api/catalog/products?id=in.(${ids.join(',')})&select=id,sku,name,price_cents`,
      { cache: 'no-store' }
    )
      .then((r) => (r.ok ? r.json() : []))
      .then(setProducts)
      .catch(() => setProducts([]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [idKey]);

  const rows = (products || []).filter((p) => items[p.id]);
  const total = rows.reduce((s, p) => s + p.price_cents * items[p.id], 0);

  const placeOrder = async () => {
    setPlacing(true);
    setError(null);
    try {
      // 1) make sure this Keycloak user has a customer row; get its id
      const cid = await rpc(authFetch, '/api/customers/rpc/ensure_customer', {});
      // 2) place the order atomically (order + items)
      const lineItems = rows.map((p) => ({
        product_sku: p.sku,
        qty: items[p.id],
        unit_price_cents: p.price_cents,
      }));
      const oid = await rpc(authFetch, '/api/orders/rpc/place_order', {
        customer_ref: cid,
        items: lineItems,
      });
      // 3) record the mock payment (best-effort saga step; cross-DB, non-atomic)
      try {
        await rpc(authFetch, '/api/checkout/rpc/record_payment', {
          order_ref: oid,
          amount_cents: total,
        });
      } catch {
        /* payment record failed — order still placed; lab tolerates this */
      }
      clear();
      setOrderId(oid);
    } catch (e) {
      setError(String(e.message || e));
    } finally {
      setPlacing(false);
    }
  };

  // --- render states ---
  if (orderId) {
    return (
      <main className="main">
        <div className="confirm">
          <h1>✅ Order placed</h1>
          <p>
            Thank you{profile?.name ? `, ${profile.name.split(' ')[0]}` : ''} — your
            order <strong>#{orderId}</strong> has been recorded.
          </p>
          <p className="confirm-links">
            <a className="checkout-btn" href="/orders">View your orders</a>
            <a className="linky" href="/">Continue shopping</a>
          </p>
        </div>
      </main>
    );
  }

  if (!ready) return <main className="main"><p className="results-line">Loading…</p></main>;

  if (!authenticated) {
    return (
      <main className="main">
        <div className="signin-gate">
          <h1>Sign in to check out</h1>
          <p>You need a ShopMock account to place an order.</p>
          <button className="checkout-btn" onClick={login}>Sign in or create an account</button>
        </div>
      </main>
    );
  }

  if (count === 0) {
    return (
      <main className="main">
        <div className="error-box">
          <h2>Nothing to check out</h2>
          <p><a className="linky" href="/">Browse products</a></p>
        </div>
      </main>
    );
  }

  return (
    <main className="main">
      <h1 className="page-title">Checkout</h1>
      <div className="cartwrap">
        <div className="cart-items">
          <div className="co-block">
            <h3>Shipping to</h3>
            <p>{profile?.name || profile?.username} · Lab address, SEA1 (mock)</p>
          </div>
          <div className="co-block">
            <h3>Payment</h3>
            <p>Mock card ending 4242 — no real charge is made.</p>
          </div>
          <div className="co-block">
            <h3>Items</h3>
            {rows.map((p) => (
              <div className="co-line" key={p.id}>
                <span>{items[p.id]} × {p.name}</span>
                <span>{money(p.price_cents * items[p.id])}</span>
              </div>
            ))}
          </div>
        </div>

        <aside className="summary">
          <p className="summary-sub">
            Order total: <strong>{money(total)}</strong>
          </p>
          {error && <p className="co-error">{error}</p>}
          <button className="checkout-btn" onClick={placeOrder} disabled={placing}>
            {placing ? 'Placing order…' : 'Place your order'}
          </button>
        </aside>
      </div>
    </main>
  );
}
