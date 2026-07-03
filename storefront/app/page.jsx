import ProductCard from './product-card';

// Catalog data comes from PostgREST via the Traefik edge (same-origin /api/catalog
// from the browser's point of view; server-side we call the edge container directly).
const BASE = process.env.CATALOG_BASE || 'http://edge';

// Always render at request time — the catalog API isn't reachable at build time.
export const dynamic = 'force-dynamic';

async function fetchJson(path) {
  try {
    const res = await fetch(`${BASE}${path}`, { cache: 'no-store' });
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

export default async function Home({ searchParams }) {
  const { q, cat } = await searchParams;

  let productPath = '/api/catalog/products?active=is.true&order=id';
  if (q) productPath += `&name=ilike.*${encodeURIComponent(q)}*`;
  if (cat) productPath += `&category_id=eq.${encodeURIComponent(cat)}`;

  const [products, categories, inventory] = await Promise.all([
    fetchJson(productPath),
    fetchJson('/api/catalog/categories?order=id'),
    fetchJson('/api/catalog/inventory'),
  ]);

  const catName = new Map((categories || []).map((c) => [c.id, c.name]));
  const stock = new Map((inventory || []).map((i) => [i.product_id, i.qty]));
  const activeCat = cat ? Number(cat) : null;

  return (
    <>
      <nav className="catnav">
        <a href="/" className={!activeCat ? 'active' : ''}>All</a>
        {(categories || []).map((c) => (
          <a
            key={c.id}
            href={`/?cat=${c.id}`}
            className={activeCat === c.id ? 'active' : ''}
          >
            {c.name}
          </a>
        ))}
        <span className="catnav-right">Today&apos;s Deals · Customer Service · Gift Cards</span>
      </nav>

      <main className="main">
        {!q && !activeCat && (
          <section className="hero">
            <div>
              <h1>Mid-year Lab Sale</h1>
              <p>Save big on laptops, audio and home essentials. Mock deals, real packets.</p>
            </div>
            <span className="hero-art">📦🚚💨</span>
          </section>
        )}

        {q && (
          <p className="results-line">
            Results for <strong>&ldquo;{q}&rdquo;</strong>
          </p>
        )}

        {products === null ? (
          <div className="error-box">
            <h2>Catalog temporarily unavailable</h2>
            <p>The catalog service could not be reached through the edge. Try again shortly.</p>
          </div>
        ) : products.length === 0 ? (
          <div className="error-box">
            <h2>No products found</h2>
            <p>Try a different search or browse all categories.</p>
          </div>
        ) : (
          <div className="grid">
            {products.map((p) => (
              <ProductCard
                key={p.id}
                product={p}
                qty={stock.get(p.id) ?? 0}
                categoryName={catName.get(p.category_id)}
              />
            ))}
          </div>
        )}
      </main>
    </>
  );
}
