-- Reject a validly signed token unless it belongs to the CIAM storefront
-- security context. PostgREST calls this after JWT verification and SET ROLE.
CREATE SCHEMA IF NOT EXISTS security;
REVOKE ALL ON SCHEMA security FROM PUBLIC;

CREATE OR REPLACE FUNCTION security.check_ciam_customer()
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
  claims jsonb := current_setting('request.jwt.claims', true)::jsonb;
  trusted_issuer text := current_setting('app.ciam_issuer', true);
BEGIN
  IF claims IS NULL OR claims = '{}'::jsonb THEN
    RETURN; -- anonymous privileges remain governed by web_anon
  END IF;
  IF trusted_issuer = '' OR NOT (claims ?& ARRAY['exp','iat','iss','sub'])
     OR claims->>'iss' <> trusted_issuer
     OR claims->>'typ' <> 'Bearer'
     OR claims->>'azp' <> 'storefront'
     OR NOT (claims->'resource_access'->'storefront'->'roles' ? 'customer')
     OR claims->>'role' <> 'customer' THEN
    RAISE insufficient_privilege USING
      MESSAGE = 'token is outside the storefront customer security context';
  END IF;
END;
$$;

REVOKE ALL ON FUNCTION security.check_ciam_customer() FROM PUBLIC;
GRANT USAGE ON SCHEMA security TO web_anon, customer;
GRANT EXECUTE ON FUNCTION security.check_ciam_customer() TO web_anon, customer;
