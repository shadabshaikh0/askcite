package com.demoshop.settlement

import com.demoshop.common.OrderStatus
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Repository
import java.sql.Timestamp
import java.time.Instant
import java.time.LocalDate

@Repository
class SettlementRepository(private val jdbcTemplate: JdbcTemplate) {

    fun findOrdersDueOn(day: LocalDate, paidUntil: Instant): List<Long> {
        val query = """
            select o.order_id
            from orders o
            left join settlements s on s.order_id = o.order_id
            where o.status = '${OrderStatus.PAID}'
              and o.paid_at < ?
              and s.settlement_id is null
            order by o.paid_at
        """.trimIndent()
        return jdbcTemplate.queryForList(query, Long::class.java, Timestamp.from(paidUntil))
    }

    fun settle(orderId: Long, day: LocalDate) {
        jdbcTemplate.update(
            "insert into settlements (order_id, settlement_date, amount, created_at) " +
                "select order_id, ?, amount, now() from orders where order_id = ?",
            day, orderId
        )
        jdbcTemplate.update(
            "update orders set status = '${OrderStatus.SETTLED}', settled_at = now() where order_id = ?",
            orderId
        )
    }
}
