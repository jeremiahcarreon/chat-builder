-- A small sample ERP database and a read-only login for the end-to-end tests.
-- Run as a superuser: psql -v ON_ERROR_STOP=1 -f scripts/e2e-erp.sql

DROP DATABASE IF EXISTS e2e_erp;
DROP ROLE IF EXISTS e2e_reader;
CREATE ROLE e2e_reader LOGIN PASSWORD 'e2e-reader-password';
CREATE DATABASE e2e_erp;
\connect e2e_erp

CREATE TABLE customers (
    id integer PRIMARY KEY,
    name text NOT NULL,
    city text NOT NULL
);
CREATE TABLE orders (
    id integer PRIMARY KEY,
    customer_id integer NOT NULL REFERENCES customers (id),
    sku text NOT NULL,
    quantity integer NOT NULL,
    total numeric(10, 2) NOT NULL,
    ordered_on date NOT NULL
);
COMMENT ON TABLE orders IS 'Customer orders for heat pumps and parts';

INSERT INTO customers VALUES (1, 'Valley Mechanical', 'Fresno'), (2, 'Coastal Air', 'Ventura');
INSERT INTO orders VALUES
    (1, 1, 'XR-500', 2, 7400.00, '2026-09-01'),
    (2, 2, 'FS161', 10, 412.50, '2026-09-03'),
    (3, 1, 'CC161', 4, 980.00, '2026-09-10');

REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT CONNECT ON DATABASE e2e_erp TO e2e_reader;
GRANT USAGE ON SCHEMA public TO e2e_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO e2e_reader;
