Demo Shop tables (hand-maintained, like many real projects)

create table users (
    user_id bigserial primary key,
    full_name text not null,
    mobile_number text,
    email text,
    email_verified boolean default false,
    created_at timestamp default now(),
)

create table orders (
    order_id bigserial primary key,
    user_id bigint not null references users(user_id),
    status text not null check (status in ('CREATED', 'PAID', 'SETTLED', 'CANCELLED')),
    amount numeric not null,
    created_at timestamp default now()
);
https://example.com/a-link-someone-pasted

alter table orders add column settled_at timestamp;
alter table orders
    add column channel text,
    add column promo_code text;
alter table orders rename column promo_code to coupon_code;
alter table orders drop column channel;

create table payments (
    payment_id bigserial primary key,
    order_id bigint references orders(order_id),
    status text check status in ('SUCCESS', 'FAILED'),
    card_number text,
    created_at timestamp default(now) not null
);
create index idx_orders_status on orders (status);

create or replace function audit_proc() returns trigger as $body$
begin
    insert into audit values (new.*);
    return null;
end;
$body$ language plpgsql;

insert into orders (user_id, status, amount) values (1, 'PAID', 10);
comment on column orders.amount is 'order value in rupees';
