'use client';

import { useEffect, useState } from 'react';
import { useCart } from '../cart-context';

function money(cents) {
  return `$${(cents / 100).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

export default function CartPage() {
  const { items, setQty, remove, count } = useCart();
  const [products, setProducts] = useState(null);

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

  return (
    <main className="main">
      <h1 className="page-title">Shopping Cart</h1>

      {count === 0 ? (
        <div className="error-box">
          <h2>Your ShopMock cart is empty</h2>
          <p>
            <a className="linky" href="/">Continue shopping</a>
          </p>
        </div>
      ) : products === null ? (
        <p className="results-line">Loading cart…</p>
      ) : (
        <div className="cartwrap">
          <div className="cart-items">
            {rows.map((p) => (
              <div className="cart-row" key={p.id}>
                <div className="cart-thumb">📦</div>
                <div className="cart-info">
                  <h3>{p.name}</h3>
                  <span className="cart-sku">{p.sku}</span>
                  <div className="qty">
                    <button onClick={() => setQty(p.id, items[p.id] - 1)} aria-label="Decrease">−</button>
                    <span>{items[p.id]}</span>
                    <button onClick={() => setQty(p.id, items[p.id] + 1)} aria-label="Increase">+</button>
                    <button className="qty-remove" onClick={() => remove(p.id)}>Delete</button>
                  </div>
                </div>
                <div className="cart-line">{money(p.price_cents * items[p.id])}</div>
              </div>
            ))}
          </div>

          <aside className="summary">
            <p className="summary-sub">
              Subtotal ({count} item{count === 1 ? '' : 's'}):{' '}
              <strong>{money(total)}</strong>
            </p>
            <a className="checkout-btn" href="/checkout">Proceed to checkout</a>
          </aside>
        </div>
      )}
    </main>
  );
}
