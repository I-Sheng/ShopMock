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

function luhnOk(digits) {
  let sum = 0;
  let dbl = false;
  for (let i = digits.length - 1; i >= 0; i--) {
    let d = digits.charCodeAt(i) - 48;
    if (dbl) {
      d *= 2;
      if (d > 9) d -= 9;
    }
    sum += d;
    dbl = !dbl;
  }
  return sum % 10 === 0;
}

function cardBrand(digits) {
  if (/^4/.test(digits)) return 'visa';
  if (/^5[1-5]/.test(digits)) return 'mastercard';
  if (/^3[47]/.test(digits)) return 'amex';
  return 'card';
}

// The PAN stays in the browser: derive brand + last4 and mint an opaque mock
// gateway token; only those are ever sent to the backend (finance DB rule:
// never store PANs).
function tokenizeCard(digits) {
  return {
    brand: cardBrand(digits),
    last4: digits.slice(-4),
    token: `tok_mock_${crypto.randomUUID()}`,
  };
}

function validateCheckout(form) {
  const digits = form.card.replace(/[\s-]/g, '');
  if (!/^\d{13,19}$/.test(digits) || !luhnOk(digits)) {
    return { error: 'Enter a valid card number.' };
  }
  const m = form.exp.trim().match(/^(\d{1,2})\s*\/\s*(\d{2}|\d{4})$/);
  if (!m) return { error: 'Enter the card expiry as MM/YY.' };
  const month = Number(m[1]);
  const year = m[2].length === 2 ? 2000 + Number(m[2]) : Number(m[2]);
  const now = new Date();
  if (
    month < 1 || month > 12 ||
    year < now.getFullYear() ||
    (year === now.getFullYear() && month < now.getMonth() + 1)
  ) {
    return { error: 'The card expiry date is invalid or in the past.' };
  }
  if (!form.line1.trim() || !form.city.trim() || !form.country.trim()) {
    return { error: 'Address line, city and country are required.' };
  }
  return { digits, month, year };
}

export default function CheckoutPage() {
  const { items, count, clear } = useCart();
  const { ready, authenticated, profile, login, authFetch } = useAuth();
  const [products, setProducts] = useState(null);
  const [placing, setPlacing] = useState(false);
  const [error, setError] = useState(null);
  const [orderId, setOrderId] = useState(null);
  const [form, setForm] = useState({
    card: '', exp: '',
    line1: '', city: '', region: '', postal: '', country: 'US',
  });

  const field = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));

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
    setError(null);
    const checked = validateCheckout(form);
    if (checked.error) {
      setError(checked.error);
      return;
    }
    setPlacing(true);
    try {
      // One call: the internal-service-backend (Django) runs the whole flow —
      // customer upsert, address, tokenized card, order + items, payment.
      // The card itself never leaves the browser (tokenizeCard sends
      // brand/last4/opaque token only).
      const lineItems = rows.map((p) => ({
        product_sku: p.sku,
        qty: items[p.id],
        unit_price_cents: p.price_cents,
      }));
      const result = await rpc(authFetch, '/api/internal/checkout', {
        address: {
          line1: form.line1.trim(),
          city: form.city.trim(),
          region: form.region.trim() || null,
          postal: form.postal.trim() || null,
          country: form.country.trim(),
        },
        payment: {
          ...tokenizeCard(checked.digits),
          exp_month: checked.month,
          exp_year: checked.year,
        },
        items: lineItems,
      });
      clear();
      setOrderId(result.order_id);
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
            <h3>Mailing address</h3>
            <p className="co-hint">Shipping to {profile?.name || profile?.username}</p>
            <div className="co-form">
              <input className="co-input co-wide" placeholder="Street address"
                autoComplete="shipping address-line1"
                value={form.line1} onChange={field('line1')} />
              <input className="co-input" placeholder="City"
                autoComplete="shipping address-level2"
                value={form.city} onChange={field('city')} />
              <input className="co-input" placeholder="State / region"
                autoComplete="shipping address-level1"
                value={form.region} onChange={field('region')} />
              <input className="co-input" placeholder="Postal code"
                autoComplete="shipping postal-code" inputMode="numeric"
                value={form.postal} onChange={field('postal')} />
              <input className="co-input" placeholder="Country"
                autoComplete="shipping country"
                value={form.country} onChange={field('country')} />
            </div>
          </div>
          <div className="co-block">
            <h3>Payment</h3>
            <p className="co-hint">
              Mock gateway — the card number is tokenized in your browser and never
              stored; no real charge is made.
            </p>
            <div className="co-form">
              <input className="co-input co-wide" placeholder="Card number"
                autoComplete="cc-number" inputMode="numeric"
                value={form.card} onChange={field('card')} />
              <input className="co-input" placeholder="Expiry (MM/YY)"
                autoComplete="cc-exp"
                value={form.exp} onChange={field('exp')} />
            </div>
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
