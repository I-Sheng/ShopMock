-- First-login provisioning. A freshly self-registered Keycloak user has no
-- commerce.customers row yet; the storefront calls this RPC once after login.
-- It reads the caller's OWN identity from the verified JWT claims that PostgREST
-- exposes in request.jwt.claims, so a caller can only ever create/read its own
-- row — the customer PII table itself is never exposed. Idempotent: repeat calls
-- return the same id.
CREATE OR REPLACE FUNCTION commerce.ensure_customer()
RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = commerce
AS $$
DECLARE
  claims json := current_setting('request.jwt.claims', true)::json;
  sub    text := claims->>'sub';
  em     text := coalesce(claims->>'email', claims->>'preferred_username');
  nm     text := coalesce(
                   nullif(claims->>'name', ''),
                   nullif(trim(concat_ws(' ', claims->>'given_name', claims->>'family_name')), ''),
                   claims->>'preferred_username',
                   'Unknown');
  cid    bigint;
BEGIN
  IF sub IS NULL THEN
    RAISE EXCEPTION 'no subject (sub) claim in token';
  END IF;

  -- Already provisioned (covers seeded users whose keycloak_sub matches sub).
  SELECT id INTO cid FROM commerce.customers WHERE keycloak_sub = sub;
  IF cid IS NOT NULL THEN
    RETURN cid;
  END IF;

  INSERT INTO commerce.customers (keycloak_sub, email, full_name)
    VALUES (sub, coalesce(em, sub || '@lab.local'), nm)
    ON CONFLICT (email) DO UPDATE SET keycloak_sub = EXCLUDED.keycloak_sub
    RETURNING id INTO cid;

  INSERT INTO commerce.accounts (customer_id) VALUES (cid);
  RETURN cid;
END;
$$;

REVOKE ALL ON FUNCTION commerce.ensure_customer() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION commerce.ensure_customer() TO customer;
