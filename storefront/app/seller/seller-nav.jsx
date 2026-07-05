'use client';

import { useSellerAuth } from './seller-auth-context';

export default function SellerNav() {
  const { ready, authenticated, profile, login, logout } = useSellerAuth();

  const name =
    (profile?.name && profile.name.split(' ')[0]) || profile?.username || null;

  return (
    <nav className="seller-nav">
      <a className="seller-brand" href="/seller">
        Seller <span>Central</span>
      </a>
      <a href="/seller">Dashboard</a>
      <a href="/seller/listings">Listings</a>
      <a href="/seller/sales">Sales</a>
      <span className="seller-nav-right">
        {authenticated ? (
          <>
            <span className="seller-hello">Hello, {name}</span>
            <button className="seller-link-btn" onClick={logout}>Sign out</button>
          </>
        ) : (
          <button className="seller-link-btn" onClick={() => ready && login()}>
            {ready ? 'Seller sign in' : '…'}
          </button>
        )}
        <a className="seller-backlink" href="/">← Back to shopping</a>
      </span>
    </nav>
  );
}
