-- demo-shop schema, kept by hand like many real projects (note the missing semicolon and the
-- trailing comma below: Askcite's schema reader copes with both).

create table users (
    user_id bigserial primary key,
    full_name text not null,
    mobile_number text,
    email text,
    pan_number text,
    date_of_birth date,
    marketing_opt_in boolean default false,
    created_at timestamp default now(),
)

create table products (
    product_id bigserial primary key,
    name text not null,
    product_type text not null check (product_type in ('BOND', 'FD')),
    status text not null check (status in ('ACTIVE', 'CLOSED')),
    min_amount numeric not null default 1000,
    interest_rate numeric
);

create table kyc_status (
    user_id bigint primary key references users(user_id),
    status text not null check (status in ('PENDING', 'APPROVED', 'REJECTED', 'BLOCKED')),
    rejection_reason text check (rejection_reason in ('PAN_NAME_MISMATCH', 'DOCUMENT_EXPIRED', 'UNDER_18')),
    attempts int not null default 0,
    updated_at timestamp default now()
);

create table orders (
    order_id bigserial primary key,
    user_id bigint not null references users(user_id),
    product_id bigint not null references products(product_id),
    amount numeric not null,
    status text not null check (status in ('CREATED', 'PAID', 'SETTLED', 'CANCELLED', 'FAILED')),
    cancel_reason text,
    created_at timestamp default now(),
    paid_at timestamp
);
alter table orders add column settled_at timestamp;
create index idx_orders_status on orders (status);
create index idx_orders_paid_at on orders (paid_at);

create table payments (
    payment_id bigserial primary key,
    order_id bigint not null references orders(order_id),
    attempt int not null,
    status text not null check (status in ('SUCCESS', 'FAILED', 'PENDING')),
    mode text not null check (mode in ('UPI', 'NETBANKING', 'CARD')),
    gateway_ref text,
    upi_id text,
    created_at timestamp default now()
);

create table refunds (
    refund_id bigserial primary key,
    order_id bigint not null references orders(order_id),
    amount numeric not null,
    reason text not null check (reason in ('ORDER_CANCELLED', 'PAYMENT_FAILED_AFTER_DEBIT')),
    status text not null check (status in ('INITIATED', 'COMPLETED')),
    created_at timestamp default now(),
    completed_at timestamp
);

create table settlements (
    settlement_id bigserial primary key,
    order_id bigint not null references orders(order_id),
    settlement_date date not null,
    amount numeric not null,
    created_at timestamp default now()
);

comment on table orders is 'One row per customer order';
comment on column orders.amount is 'Order value in rupees';
