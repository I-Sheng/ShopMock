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
