package com.demoshop.common

object OrderStatus {
    const val CREATED = "CREATED"      // order placed, waiting for payment
    const val PAID = "PAID"            // payment received, waiting for settlement
    const val SETTLED = "SETTLED"      // units delivered to the customer (T+1)
    const val CANCELLED = "CANCELLED"  // cancelled by the customer or by the system
    const val FAILED = "FAILED"        // payment failed after all retries
}

object Limits {
    const val MIN_ORDER_AMOUNT = 1000          // rupees
    const val MAX_PAYMENT_RETRIES = 2          // after the first attempt, so 3 attempts in total
    const val SETTLEMENT_CUTOFF_HOUR_IST = 15  // orders paid after 3 pm IST settle one business day later
    const val MAX_KYC_ATTEMPTS = 3
    const val REFUND_SLA_WORKING_DAYS = 5
}
