import importlib
import json
import os
import sys
import unittest
from types import SimpleNamespace

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa


class SellerIdentityBoundaryRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(cls.private_key.public_key()))
        os.environ['PGRST_JWT_SECRET'] = json.dumps(public_jwk)
        os.environ['CIAM_ISSUER'] = 'http://localhost/auth/realms/shopmock-ciam'
        sys.modules.pop('portal.auth', None)
        cls.auth = importlib.import_module('portal.auth')

    def test_customer_cannot_become_seller_by_using_seller_client(self):
        token = jwt.encode(
            {
                'exp': 4_102_444_800,
                'iat': 1_700_000_000,
                'iss': 'http://localhost/auth/realms/shopmock-ciam',
                'sub': 'customer-123',
                'typ': 'Bearer',
                'azp': 'seller-dashboard',
                # This is the vulnerable hardcoded claim. The subject has no
                # seller client role membership.
                'role': 'seller',
                'resource_access': {'storefront': {'roles': ['customer']}},
            },
            self.private_key,
            algorithm='RS256',
        )
        request = SimpleNamespace(headers={'Authorization': f'Bearer {token}'})

        with self.assertRaises(self.auth.ForbiddenError):
            self.auth.require_seller(request)

    def test_explicit_seller_membership_is_allowed(self):
        token = jwt.encode(
            {'exp': 4_102_444_800, 'iat': 1_700_000_000,
             'iss': os.environ['CIAM_ISSUER'], 'sub': 'seller-1',
             'typ': 'Bearer', 'azp': 'seller-dashboard', 'role': 'seller',
             'resource_access': {'seller-dashboard': {'roles': ['seller']}}},
            self.private_key, algorithm='RS256')
        request = SimpleNamespace(headers={'Authorization': f'Bearer {token}'})
        self.assertEqual(self.auth.require_seller(request)['sub'], 'seller-1')


if __name__ == '__main__':
    unittest.main()
