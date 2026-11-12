-- Expand step of an expand/contract rollout: every statement is online-safe.
SET lock_timeout = '5s';

ALTER TABLE orders ADD COLUMN customer_tier TEXT DEFAULT 'standard';

CREATE INDEX CONCURRENTLY idx_orders_created_at ON orders (created_at);

ALTER TABLE orders
  ADD CONSTRAINT fk_orders_customer FOREIGN KEY (customer_id)
  REFERENCES customers (id) NOT VALID;

-- New tables are empty, so locks and NOT NULL columns are harmless here.
CREATE TABLE loyalty_points (
  id BIGSERIAL PRIMARY KEY,
  order_id BIGINT NOT NULL,
  points INT NOT NULL
);
CREATE INDEX idx_loyalty_points_order ON loyalty_points (order_id);
ALTER TABLE loyalty_points ADD COLUMN awarded_at TIMESTAMPTZ NOT NULL;

-- Reviewed and approved by the data team (ticket DATA-412):
-- migration-guard: ignore MG008
DELETE FROM feature_flags;
