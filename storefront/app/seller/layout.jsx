import { SellerAuthProvider } from './seller-auth-context';
import SellerNav from './seller-nav';

export const metadata = {
  title: 'ShopMock Seller Central',
  description: 'Manage your ShopMock listings and sales — mock e-commerce lab.',
};

export default function SellerLayout({ children }) {
  return (
    <SellerAuthProvider>
      <SellerNav />
      {children}
    </SellerAuthProvider>
  );
}
