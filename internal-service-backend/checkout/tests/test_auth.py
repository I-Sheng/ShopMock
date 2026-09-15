import importlib
import json
import os
import sys
import unittest
from types import SimpleNamespace

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa


class CustomerIdentityBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(cls.key.public_key()))
        os.environ['PGRST_JWT_SECRET'] = json.dumps(jwk)
        os.environ['CIAM_ISSUER'] = 'https://shop.test/auth/realms/shopmock-ciam'
        sys.modules.pop('checkout.auth', None)
        cls.auth = importlib.import_module('checkout.auth')

    def token(self, **overrides):
        claims = {
            'exp': 4_102_444_800, 'iat': 1_700_000_000,
            'iss': os.environ['CIAM_ISSUER'], 'sub': 'customer-1',
            'typ': 'Bearer', 'azp': 'storefront', 'role': 'customer',
            'resource_access': {'storefront': {'roles': ['customer']}},
        }
        claims.update(overrides)
        return jwt.encode(claims, self.key, algorithm='RS256')

    def request(self, **overrides):
        return SimpleNamespace(headers={'Authorization': 'Bearer ' + self.token(**overrides)})

    def test_customer_storefront_allowed(self):
        self.assertEqual(self.auth.require_customer(self.request())['sub'], 'customer-1')

    def test_seller_dashboard_token_denied(self):
        with self.assertRaises(self.auth.AuthError):
            self.auth.require_customer(self.request(azp='seller-dashboard'))

    def test_workforce_realm_denied(self):
        with self.assertRaises(self.auth.AuthError):
            self.auth.require_customer(self.request(iss='https://shop.test/auth/realms/shopmock-workforce'))

    def test_non_bearer_type_denied(self):
        with self.assertRaises(self.auth.AuthError):
            self.auth.require_customer(self.request(typ='ID'))

    def test_missing_membership_is_forbidden(self):
        with self.assertRaises(self.auth.ForbiddenError):
            self.auth.require_customer(self.request(resource_access={}))


if __name__ == '__main__':
    unittest.main()
