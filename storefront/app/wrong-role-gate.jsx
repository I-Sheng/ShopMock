'use client';

// Shown when a valid SSO session exists but lacks the client role this area
// requires (customer <-> seller). Accounts are single-role, so the only way in
// is to sign out and sign back in with the right account.
export default function WrongRoleGate({ area, required, profile, logout, homeHref, homeLabel }) {
  const who = profile?.username || profile?.email || 'This account';
  return (
    <main className="main">
      <div className="signin-gate">
        <h1>Access denied</h1>
        <p>
          <strong>{who}</strong> is not a {required} account, so it can’t use {area}.
          Sign out and sign in with a {required} account to continue.
        </p>
        <button className="checkout-btn" onClick={logout}>Sign out</button>
        <p className="co-hint" style={{ marginTop: 14 }}>
          <a className="linky" href={homeHref}>{homeLabel}</a>
        </p>
      </div>
    </main>
  );
}
