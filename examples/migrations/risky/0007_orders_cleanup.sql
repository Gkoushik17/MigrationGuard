-- A migration that looks harmless in review but would hurt production.
ALTER TABLE orders ADD COLUMN customer_tier TEXT NOT NULL;

ALTER TABLE orders ALTER COLUMN amount TYPE NUMERIC(12, 2);

CREATE INDEX idx_orders_created_at ON orders (created_at);

ALTER TABLE orders RENAME COLUMN status TO order_status;

ALTER TABLE orders
  ADD CONSTRAINT fk_orders_customer FOREIGN KEY (customer_id) REFERENCES customers (id);

UPDATE orders SET customer_tier = 'standard';

ALTER TABLE users DROP COLUMN legacy_score;

DROP TABLE audit_log_2019;
