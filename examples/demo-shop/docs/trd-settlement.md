# Settlement TRD

Owner: Engineering · Reviewed by: Operations

## Overview
Settlement delivers the units of a paid order to the customer. We follow **T+1**: an order paid
today settles on the next business day.

## Cut-off
- Orders paid **before 3:00 pm IST** settle on the next business day.
- Orders paid **after 3:00 pm IST** settle one business day later (T+2 from payment).
- Weekends and exchange holidays are not business days.

## The settlement job
- Runs every business day at **6:00 pm IST** (internal endpoint `POST /internal/settlement/run`).
- Picks every PAID order that was paid before the previous business day's cut-off and is not settled yet.
- For each order: inserts a row into `settlements` and marks the order **SETTLED** (with `settled_at`).
- The job is idempotent: an order is never settled twice.

## Failures
- If settling one order fails, the job continues with the others.
- The failed order stays **PAID** and is retried in the next run. Operations get an alert if an
  order is still unsettled two business days after payment.

## Cancellation window
- A paid order can be cancelled until 3:00 pm IST on its settlement day.
