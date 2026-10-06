# Runbook: payment failures

For the support and operations team.

## What the customer sees
After a failed payment the customer is asked to retry. They get **3 attempts in total**
(the first try plus 2 retries). After the third failure the order is marked **FAILED**.

## Money debited but payment failed
Sometimes the bank debits the customer even though the gateway reports a failure. In that case a
refund is created automatically with reason `PAYMENT_FAILED_AFTER_DEBIT`.
Refunds reach the customer within **5 working days**.

## How to check an order
1. Look up the order status (CREATED, PAID, SETTLED, CANCELLED or FAILED).
2. Look at the payment attempts for the order: each has a status, a payment mode and a gateway reference.
3. If there is a refund, check whether it is INITIATED or COMPLETED.

## When to escalate
- A refund still INITIATED after 7 days → escalate to the payments team.
- More than 5% of payments failing in one hour → possible gateway incident; page the on-call engineer.
