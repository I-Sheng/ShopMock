'use client';

import { useState } from 'react';
import { useCart } from './cart-context';

const CATEGORY_EMOJI = {
  Laptops: '💻',
  Audio: '🎧',
  Home: '💡',
  Electronics: '🔌',
};

function Stars({ id }) {
  // Deterministic mock rating so cards look real without a reviews table.
  const rating = 3.5 + ((id * 7) % 15) / 10;
  const reviews = 120 + ((id * 137) % 4000);
  const full = Math.round(rating);
  return (
    <div className="stars" title={`${rating.toFixed(1)} out of 5`}>
      <span className="stars-icons">
        {'★'.repeat(full)}
        {'☆'.repeat(5 - full)}
      </span>
      <span className="stars-count">{reviews.toLocaleString()}</span>
    </div>
  );
}

export default function ProductCard({ product, qty, categoryName }) {
  const { add } = useCart();
  const [added, setAdded] = useState(false);

  const dollars = Math.floor(product.price_cents / 100);
  const cents = String(product.price_cents % 100).padStart(2, '0');
  const emoji = CATEGORY_EMOJI[categoryName] || '📦';
  const hue = (product.id * 67) % 360;

  const onAdd = () => {
    add(product.id);
    setAdded(true);
    setTimeout(() => setAdded(false), 1200);
  };

  return (
    <div className="card">
      <div
        className="card-img"
        style={{
          background: `linear-gradient(135deg, hsl(${hue} 45% 88%), hsl(${(hue + 40) % 360} 50% 76%))`,
        }}
      >
        <span>{emoji}</span>
      </div>
      <div className="card-body">
        <span className="card-cat">{categoryName || 'General'}</span>
        <h3>{product.name}</h3>
        <p className="card-desc">{product.description}</p>
        <Stars id={product.id} />
        <div className="price">
          <sup>$</sup>
          <span className="price-whole">{dollars.toLocaleString()}</span>
          <sup>{cents}</sup>
        </div>
        <p className="delivery">
          FREE delivery <strong>tomorrow</strong>
        </p>
        {qty > 0 ? (
          qty < 50 ? (
            <p className="stock low">Only {qty} left in stock</p>
          ) : (
            <p className="stock">In Stock</p>
          )
        ) : (
          <p className="stock out">Out of stock</p>
        )}
        <button className="add-btn" onClick={onAdd} disabled={qty === 0}>
          {added ? 'Added ✓' : 'Add to Cart'}
        </button>
      </div>
    </div>
  );
}
