-- Checkout write path. PostgREST can't do a multi-row insert (order + its items)
-- in one transaction via plain table endpoints, so checkout is a single RPC.
-- SECURITY DEFINER: runs as the owner (postgres, which owns the tables), so the
-- token-mapped `customer` role only needs EXECUTE — not direct INSERT on tables.
--
-- Lab honesty (documented, not fixed): the caller supplies customer_ref and the
-- per-line unit_price_cents, so price tampering and IDOR are possible by design.
-- That's intended attack surface for the capstone, not a bug.
CREATE OR REPLACE FUNCTION sales.place_order(customer_ref bigint, items jsonb)
RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = sales
AS $$
DECLARE
  new_id bigint;
  total  int := 0;
  item   jsonb;
BEGIN
  IF customer_ref IS NULL THEN
    RAISE EXCEPTION 'customer_ref is required';
  END IF;
  IF items IS NULL OR jsonb_typeof(items) <> 'array' OR jsonb_array_length(items) = 0 THEN
    RAISE EXCEPTION 'items must be a non-empty JSON array';
  END IF;

  FOR item IN SELECT * FROM jsonb_array_elements(items) LOOP
    total := total + (item->>'qty')::int * (item->>'unit_price_cents')::int;
  END LOOP;

  INSERT INTO sales.orders (customer_ref, status, total_cents)
    VALUES (customer_ref, 'placed', total)
    RETURNING id INTO new_id;

  INSERT INTO sales.order_items (order_id, product_sku, qty, unit_price_cents)
  SELECT new_id,
         li->>'product_sku',
         (li->>'qty')::int,
         (li->>'unit_price_cents')::int
  FROM jsonb_array_elements(items) AS li;

  RETURN new_id;
END;
$$;

REVOKE ALL ON FUNCTION sales.place_order(bigint, jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION sales.place_order(bigint, jsonb) TO customer;
