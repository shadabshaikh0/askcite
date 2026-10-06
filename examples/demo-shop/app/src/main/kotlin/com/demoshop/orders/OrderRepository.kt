package com.demoshop.orders

import com.demoshop.common.OrderStatus
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Repository
import java.sql.Timestamp

@Repository
class OrderRepository(private val jdbcTemplate: JdbcTemplate) {

    fun insert(userId: Long, productId: Long, amount: Long): Order {
        val query = """
            insert into orders (user_id, product_id, amount, status, created_at)
            values (?, ?, ?, '${OrderStatus.CREATED}', now())
            returning order_id
        """.trimIndent()
        val orderId = jdbcTemplate.queryForObject(query, Long::class.java, userId, productId, amount)!!
        return findById(orderId)!!
    }

    fun updateStatus(orderId: Long, status: String, cancelReason: String? = null) {
        jdbcTemplate.update(
            "update orders set status = ?, cancel_reason = coalesce(?, cancel_reason) " +
                "where order_id = ?",
            status, cancelReason, orderId
        )
    }

    fun findById(orderId: Long): Order? {
        val query = """
            select order_id, user_id, product_id, amount, status, created_at, paid_at, settled_at
            from orders
            where order_id = ?
        """.trimIndent()
        return jdbcTemplate.query(query, { rs, _ ->
            Order(rs.getLong("order_id"), rs.getLong("user_id"), rs.getLong("product_id"), rs.getLong("amount"),
                rs.getString("status"), rs.getTimestamp("created_at").toInstant(),
                rs.getTimestamp("paid_at")?.toInstant(), rs.getTimestamp("settled_at")?.toInstant())
        }, orderId).firstOrNull()
    }

    fun isProductActive(productId: Long): Boolean =
        jdbcTemplate.queryForObject(
            "select count(*) from products where product_id = ? and status = 'ACTIVE'",
            Int::class.java, productId
        )!! > 0

    /** Orders paid in a time window, optionally for one product (used by settlement and reports). */
    fun findPaidBetween(start: Timestamp, end: Timestamp, productId: Long?): List<Long> {
        val productFilter = if (productId != null) "and o.product_id = $productId" else ""
        val query = """
            select o.order_id
            from orders o
            join products p on p.product_id = o.product_id
            where o.status = '${OrderStatus.PAID}'
              and o.paid_at >= ?
              and o.paid_at < ?
              $productFilter
            order by o.paid_at
        """.trimIndent()
        return jdbcTemplate.queryForList(query, Long::class.java, start, end)
    }
}
