import './globals.css';
import { AuthProvider } from './auth-context';
import { CartProvider } from './cart-context';
import Header from './header';

export const metadata = {
  title: 'ShopMock — Low prices on laptops, audio & home',
  description: 'ShopMock storefront — mock e-commerce lab, not a real store.',
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>
        <AuthProvider>
        <CartProvider>
          <Header />
          {children}
          <footer className="footer">
            <a className="footer-top" href="#top">Back to top</a>
            <div className="footer-cols">
              <div>
                <h4>Get to Know Us</h4>
                <span>About ShopMock</span>
                <span>Careers</span>
                <span>Press Releases</span>
              </div>
              <div>
                <h4>Make Money with Us</h4>
                <span><a href="/seller">Sell on ShopMock</a></span>
                <span>Become an Affiliate</span>
                <span>Advertise Your Products</span>
              </div>
              <div>
                <h4>Let Us Help You</h4>
                <span>Your Account</span>
                <span>Your Orders</span>
                <span>Returns &amp; Replacements</span>
              </div>
            </div>
            <p className="footer-note">
              ShopMock is a mock e-commerce lab environment. Nothing here is for sale.
            </p>
          </footer>
        </CartProvider>
        </AuthProvider>
      </body>
    </html>
  );
}
