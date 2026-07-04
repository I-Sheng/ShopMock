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

-- Checkout collects a mailing address. Same trust model as ensure_customer():
-- the caller is identified by the verified JWT sub, so a customer can only ever
-- write their own address. Keeps one shipping address per customer, updated in
-- place on repeat checkouts.
CREATE OR REPLACE FUNCTION commerce.save_shipping_address(
  line1 text, city text, region text, postal text, country text)
RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = commerce
AS $$
DECLARE
  claims   json := current_setting('request.jwt.claims', true)::json;
  sub      text := claims->>'sub';
  _line1   text := line1;
  _city    text := city;
  _region  text := region;
  _postal  text := postal;
  _country text := country;
  cid      bigint;
  aid      bigint;
BEGIN
  IF sub IS NULL THEN
    RAISE EXCEPTION 'no subject (sub) claim in token';
  END IF;
  IF coalesce(_line1, '') = '' OR coalesce(_city, '') = '' OR coalesce(_country, '') = '' THEN
    RAISE EXCEPTION 'line1, city and country are required';
  END IF;

  SELECT id INTO cid FROM commerce.customers WHERE keycloak_sub = sub;
  IF cid IS NULL THEN
    RAISE EXCEPTION 'no customer row for this token; call ensure_customer first';
  END IF;

  UPDATE commerce.addresses a
     SET line1 = _line1, city = _city, region = _region,
         postal = _postal, country = _country
   WHERE a.id = (SELECT min(id) FROM commerce.addresses
                  WHERE customer_id = cid AND kind = 'shipping')
   RETURNING a.id INTO aid;

  IF aid IS NULL THEN
    INSERT INTO commerce.addresses (customer_id, kind, line1, city, region, postal, country)
      VALUES (cid, 'shipping', _line1, _city, _region, _postal, _country)
      RETURNING id INTO aid;
  END IF;

  RETURN aid;
END;
$$;

REVOKE ALL ON FUNCTION commerce.save_shipping_address(text, text, text, text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION commerce.save_shipping_address(text, text, text, text, text) TO customer;
