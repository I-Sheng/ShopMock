'use client';

import { createContext, useContext, useEffect, useState } from 'react';

const CartContext = createContext({
  items: {},
  count: 0,
  add: () => {},
  setQty: () => {},
  remove: () => {},
  clear: () => {},
});

export function CartProvider({ children }) {
  const [items, setItems] = useState({});

  useEffect(() => {
    try {
      const saved = JSON.parse(localStorage.getItem('shopmock-cart') || '{}');
      setItems(saved);
    } catch {
      /* corrupt cart — start empty */
    }
  }, []);

  const persist = (next) => {
    localStorage.setItem('shopmock-cart', JSON.stringify(next));
    return next;
  };

  const add = (id) =>
    setItems((prev) => persist({ ...prev, [id]: (prev[id] || 0) + 1 }));

  const setQty = (id, qty) =>
    setItems((prev) => {
      const next = { ...prev };
      if (qty <= 0) delete next[id];
      else next[id] = qty;
      return persist(next);
    });

  const remove = (id) =>
    setItems((prev) => {
      const next = { ...prev };
      delete next[id];
      return persist(next);
    });

  const clear = () => setItems(persist({}));

  const count = Object.values(items).reduce((a, b) => a + b, 0);

  return (
    <CartContext.Provider value={{ items, count, add, setQty, remove, clear }}>
      {children}
    </CartContext.Provider>
  );
}

export const useCart = () => useContext(CartContext);
