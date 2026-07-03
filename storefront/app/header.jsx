'use client';

import { useCart } from './cart-context';
import { useAuth } from './auth-context';

export default function Header() {
  const { count } = useCart();
  const { ready, authenticated, profile, login, register, logout } = useAuth();

  const firstName =
    (profile?.name && profile.name.split(' ')[0]) || profile?.username || null;

  return (
    <header className="header" id="top">
      <a className="logo" href="/">
        Shop<span>Mock</span>
      </a>
      <div className="deliver">
        <small>Deliver to</small>
        <strong>📍 Lab, SEA1</strong>
      </div>
      <form className="search" action="/" method="get">
        <input
          type="text"
          name="q"
          placeholder="Search ShopMock"
          aria-label="Search products"
        />
        <button type="submit" aria-label="Search">🔍</button>
      </form>

      {authenticated ? (
        <>
          <div className="account" onClick={logout} title="Sign out">
            <small>Hello, {firstName}</small>
            <strong>Sign out</strong>
          </div>
          <a className="account" href="/orders">
            <small>Returns</small>
            <strong>&amp; Orders</strong>
          </a>
        </>
      ) : (
        <>
          <div
            className="account"
            onClick={() => ready && login()}
            title="Sign in"
          >
            <small>Hello, sign in</small>
            <strong>{ready ? 'Account & Lists' : '…'}</strong>
          </div>
          <div
            className="account"
            onClick={() => ready && register()}
            title="Create an account"
          >
            <small>New customer?</small>
            <strong>Start here</strong>
          </div>
        </>
      )}

      <a className="cart" href="/cart">
        🛒
        <span className="cart-badge">{count}</span>
        <strong>Cart</strong>
      </a>
    </header>
  );
}
