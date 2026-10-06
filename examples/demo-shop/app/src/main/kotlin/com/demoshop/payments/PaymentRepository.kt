package com.demoshop.payments

import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Repository

@Repository
class PaymentRepository(private val jdbcTemplate: JdbcTemplate) {

    fun recordAttempt(orderId: Long, status: String, mode: String, gatewayRef: String) {
        val query = """
            insert into payments (order_id, attempt, status, mode, gateway_ref, created_at)
            select ?, coalesce(max(attempt), 0) + 1, ?, ?, ?, now()
            from payments
            where order_id = ?
        """.trimIndent()
        jdbcTemplate.update(query, orderId, status, mode, gatewayRef, orderId)
    }

    fun countFailedAttempts(orderId: Long): Int =
        jdbcTemplate.queryForObject(
            "select count(*) from payments " +
                "where order_id = ? and status = 'FAILED'",
            Int::class.java, orderId
        )!!
}
