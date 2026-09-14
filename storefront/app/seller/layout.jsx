import { SellerAuthProvider } from './seller-auth-context';
import SellerNav from './seller-nav';

export const metadata = {
  title: 'ShopMock Seller Central',
  description: 'Manage your ShopMock listings and sales — mock e-commerce lab.',
};

export default function SellerLayout({ children }) {
  return (
    <SellerAuthProvider>
      {/* .seller-scope re-points the shared design tokens at the plum
          operations-console palette for everything inside Seller Central. */}
      <div className="seller-scope">
        <SellerNav />
        {children}
      </div>
    </SellerAuthProvider>
  );
}
