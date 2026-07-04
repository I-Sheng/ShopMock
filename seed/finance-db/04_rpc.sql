-- Mock payment. Called by the storefront right after sales.place_order() as a
-- best-effort saga step: a true cross-DB transaction is impossible under
-- database-per-service, and that split is realistic. No card data touches this
-- path — it only records that a charge settled (tokens/PANs are never handled).
-- SECURITY DEFINER so the `customer` role needs only EXECUTE, not table INSERT.
CREATE OR REPLACE FUNCTION finance.record_payment(order_ref bigint, amount_cents bigint)
RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = finance
AS $$
DECLARE
  txn_id bigint;
BEGIN
  IF amount_cents IS NULL OR amount_cents <= 0 THEN
    RAISE EXCEPTION 'amount_cents must be positive';
  END IF;

  INSERT INTO finance.transactions (order_ref, amount_cents, kind, status)
    VALUES (order_ref, amount_cents, 'charge', 'settled')
    RETURNING id INTO txn_id;

  RETURN txn_id;
END;
$$;

REVOKE ALL ON FUNCTION finance.record_payment(bigint, bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION finance.record_payment(bigint, bigint) TO customer;

-- Stores a tokenized card (schema rule: never store PANs). The storefront
-- tokenizes in the browser — brand + last4 + an opaque token is all that is
-- ever sent over the wire — and this function rejects anything PAN-shaped as a
-- second line of defence. Idempotent per (customer, brand, last4, expiry).
-- Lab honesty: customer_ref is caller-supplied, same IDOR surface as place_order.
CREATE OR REPLACE FUNCTION finance.save_payment_method(
  customer_ref bigint, brand text, last4 text, token text, exp_month int, exp_year int)
RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = finance
AS $$
DECLARE
  _cref  bigint := customer_ref;
  _brand text   := brand;
  _last4 text   := last4;
  _token text   := token;
  _month int    := exp_month;
  _year  int    := exp_year;
  pmid   bigint;
BEGIN
  IF _cref IS NULL THEN
    RAISE EXCEPTION 'customer_ref is required';
  END IF;
  IF _last4 !~ '^[0-9]{4}$' THEN
    RAISE EXCEPTION 'last4 must be exactly 4 digits';
  END IF;
  IF _token IS NULL OR _token ~ '^[0-9 -]{12,}$' THEN
    RAISE EXCEPTION 'token must be an opaque gateway token, never a card number';
  END IF;
  IF _month NOT BETWEEN 1 AND 12 OR _year NOT BETWEEN 2020 AND 2100 THEN
    RAISE EXCEPTION 'invalid expiry';
  END IF;

  -- Same card re-entered on a later checkout: reuse the stored method.
  SELECT id INTO pmid FROM finance.payment_methods pm
   WHERE pm.customer_ref = _cref AND pm.brand = _brand AND pm.last4 = _last4
     AND pm.exp_month = _month AND pm.exp_year = _year;
  IF pmid IS NOT NULL THEN
    RETURN pmid;
  END IF;

  INSERT INTO finance.payment_methods (customer_ref, brand, last4, token, exp_month, exp_year)
    VALUES (_cref, coalesce(nullif(_brand, ''), 'card'), _last4, _token, _month, _year)
    RETURNING id INTO pmid;

  RETURN pmid;
END;
$$;

REVOKE ALL ON FUNCTION finance.save_payment_method(bigint, text, text, text, int, int) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION finance.save_payment_method(bigint, text, text, text, int, int) TO customer;
