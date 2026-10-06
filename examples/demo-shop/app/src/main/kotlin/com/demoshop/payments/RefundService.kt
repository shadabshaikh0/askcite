package com.demoshop.payments

import com.demoshop.common.Limits
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Service

/** Refunds go back to the original payment method within REFUND_SLA_WORKING_DAYS working days. */
@Service
class RefundService(private val jdbcTemplate: JdbcTemplate) {

    fun refund(orderId: Long, amount: Long, reason: String) {
        val query = """
            insert into refunds (order_id, amount, reason, status, created_at)
            values (?, ?, ?, 'INITIATED', now())
        """.trimIndent()
        jdbcTemplate.update(query, orderId, amount, reason)
    }

    fun overdueRefunds(): List<Long> =
        jdbcTemplate.queryForList(
            "select refund_id from refunds where status = 'INITIATED' " +
                "and created_at < now() - interval '${Limits.REFUND_SLA_WORKING_DAYS + 2} days'",
            Long::class.java
        )

    companion object {
        const val REASON_ORDER_CANCELLED = "ORDER_CANCELLED"
        const val REASON_PAYMENT_FAILED_AFTER_DEBIT = "PAYMENT_FAILED_AFTER_DEBIT"
    }
}
