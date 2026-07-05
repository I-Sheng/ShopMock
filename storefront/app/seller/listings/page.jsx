'use client';

import { useCallback, useEffect, useState } from 'react';
import { useSellerAuth } from '../seller-auth-context';

function money(cents) {
  return `$${(cents / 100).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

function toCents(dollars) {
  const n = Number.parseFloat(String(dollars).replace(/[$,\s]/g, ''));
  return Number.isFinite(n) && n >= 0 ? Math.round(n * 100) : null;
}

const EMPTY_FORM = { sku: '', name: '', description: '', category_id: '', price: '', qty: '' };

function AddProductForm({ authFetch, onCreated }) {
  const [form, setForm] = useState(EMPTY_FORM);
  const [categories, setCategories] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    fetch('/api/catalog/categories')
      .then((r) => (r.ok ? r.json() : []))
      .then(setCategories)
      .catch(() => {});
  }, []);

  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });

  const submit = async (e) => {
    e.preventDefault();
    setError(null);
    const price_cents = toCents(form.price);
    const qty = form.qty === '' ? 0 : Number.parseInt(form.qty, 10);
    if (!/^[A-Z0-9][A-Z0-9-]{2,31}$/.test(form.sku.trim().toUpperCase())) {
      return setError('SKU must be 3–32 characters: letters, digits, dashes.');
    }
    if (!form.name.trim()) return setError('Product name is required.');
    if (price_cents === null) return setError('Enter a valid price.');
    if (!Number.isInteger(qty) || qty < 0) return setError('Enter a valid stock quantity.');

    setBusy(true);
    try {
      const res = await authFetch('/api/seller-backend/listings', {
        method: 'POST',
        body: JSON.stringify({
          sku: form.sku.trim().toUpperCase(),
          name: form.name.trim(),
          description: form.description.trim() || null,
          category_id: form.category_id ? Number(form.category_id) : null,
          price_cents,
          qty,
        }),
      });
      if (!res.ok) throw new Error((await res.json()).error || `HTTP ${res.status}`);
      setForm(EMPTY_FORM);
      onCreated(await res.json());
    } catch (err) {
      setError(String(err.message || err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="summary seller-add">
      <h3>Add a product</h3>
      {error && <p className="co-error">{error}</p>}
      <form className="co-form" onSubmit={submit}>
        <input className="co-input" placeholder="SKU (e.g. NWG-CAM-2)" value={form.sku} onChange={set('sku')} />
        <input className="co-input" placeholder="Product name" value={form.name} onChange={set('name')} />
        <select className="co-input" value={form.category_id} onChange={set('category_id')}>
          <option value="">Category (optional)</option>
          {categories.map((c) => (
            <option key={c.id} value={c.id}>{c.name}</option>
          ))}
        </select>
        <input className="co-input co-wide" placeholder="Description (optional)" value={form.description} onChange={set('description')} />
        <input className="co-input" placeholder="Price (e.g. 89.00)" value={form.price} onChange={set('price')} />
        <input className="co-input" placeholder="Stock qty" value={form.qty} onChange={set('qty')} />
        <button className="checkout-btn" disabled={busy} type="submit">
          {busy ? 'Creating…' : 'Create listing'}
        </button>
      </form>
    </div>
  );
}

function ListingRow({ listing, authFetch, onSaved }) {
  const [price, setPrice] = useState((listing.price_cents / 100).toFixed(2));
  const [qty, setQty] = useState(String(listing.qty));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const dirty =
    toCents(price) !== listing.price_cents || Number.parseInt(qty, 10) !== listing.qty;

  const save = async (patch) => {
    setBusy(true);
    setError(null);
    try {
      const res = await authFetch(`/api/seller-backend/listings/${listing.listing_id}`, {
        method: 'PATCH',
        body: JSON.stringify(patch),
      });
      if (!res.ok) throw new Error((await res.json()).error || `HTTP ${res.status}`);
      onSaved(await res.json());
    } catch (err) {
      setError(String(err.message || err));
    } finally {
      setBusy(false);
    }
  };

  const saveEdits = () => {
    const price_cents = toCents(price);
    const q = Number.parseInt(qty, 10);
    if (price_cents === null) return setError('Bad price');
    if (!Number.isInteger(q) || q < 0) return setError('Bad qty');
    save({ price_cents, qty: q });
  };

  return (
    <tr className={listing.active ? '' : 'seller-row-inactive'}>
      <td>{listing.sku}</td>
      <td>
        {listing.name}
        {error && <div className="co-error">{error}</div>}
      </td>
      <td className="num">
        <input
          className="co-input seller-cell-input"
          value={price}
          onChange={(e) => setPrice(e.target.value)}
          aria-label={`Price for ${listing.sku}`}
        />
      </td>
      <td className="num">
        <input
          className="co-input seller-cell-input"
          value={qty}
          onChange={(e) => setQty(e.target.value)}
          aria-label={`Stock for ${listing.sku}`}
        />
      </td>
      <td className="num">{listing.commission_pct}%</td>
      <td>
        <span className={`order-status ${listing.active ? 'delivered' : ''}`}>
          {listing.active ? 'active' : 'inactive'}
        </span>
      </td>
      <td className="seller-actions">
        <button
          className="add-btn"
          disabled={busy || !dirty}
          onClick={saveEdits}
        >
          Save
        </button>
        <button
          className="qty-remove seller-toggle"
          disabled={busy}
          onClick={() => save({ active: !listing.active })}
        >
          {listing.active ? 'Deactivate' : 'Activate'}
        </button>
      </td>
    </tr>
  );
}

export default function SellerListingsPage() {
  const { ready, authenticated, login, authFetch } = useSellerAuth();
  const [listings, setListings] = useState(null);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    try {
      // ensure first so a brand-new seller account can use this page directly
      await authFetch('/api/seller-backend/sellers/ensure', { method: 'POST', body: '{}' });
      const res = await authFetch('/api/seller-backend/listings');
      if (!res.ok) throw new Error(`listings -> ${res.status} ${await res.text()}`);
      setListings(await res.json());
    } catch (e) {
      setError(String(e.message || e));
      setListings([]);
    }
  }, [authFetch]);

  useEffect(() => {
    if (ready && authenticated) load();
  }, [ready, authenticated, load]);

  const replaceListing = (updated) =>
    setListings((ls) => ls.map((l) => (l.listing_id === updated.listing_id ? updated : l)));

  if (!ready) return <main className="main"><p className="results-line">Loading…</p></main>;

  if (!authenticated) {
    return (
      <main className="main">
        <div className="signin-gate">
          <h1>Sign in to manage your listings</h1>
          <button className="checkout-btn" onClick={login}>Sign in as a seller</button>
        </div>
      </main>
    );
  }

  return (
    <main className="main">
      <h1 className="page-title">Your Listings</h1>
      {error && <p className="co-error">{error}</p>}
      <AddProductForm
        authFetch={authFetch}
        onCreated={(l) => setListings((ls) => [...(ls || []), l])}
      />
      {listings === null ? (
        <p className="results-line">Loading listings…</p>
      ) : listings.length === 0 ? (
        <div className="error-box">
          <h2>No listings yet</h2>
          <p>Add your first product above — it goes live in the catalog immediately.</p>
        </div>
      ) : (
        <div className="seller-tablewrap">
          <table className="seller-table">
            <thead>
              <tr>
                <th>SKU</th>
                <th>Product</th>
                <th className="num">Price ($)</th>
                <th className="num">Stock</th>
                <th className="num">Commission</th>
                <th>Status</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {listings.map((l) => (
                <ListingRow
                  key={l.listing_id}
                  listing={l}
                  authFetch={authFetch}
                  onSaved={replaceListing}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </main>
  );
}
