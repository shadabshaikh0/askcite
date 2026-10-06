package com.demoshop.kyc

import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Repository

@Repository
class KycRepository(private val jdbcTemplate: JdbcTemplate) {

    fun status(userId: Long): String? =
        jdbcTemplate.queryForList("select status from kyc_status where user_id = ?", String::class.java, userId)
            .firstOrNull()

    fun rejectedAttempts(userId: Long): Int =
        jdbcTemplate.queryForObject(
            "select coalesce(max(attempts), 0) from kyc_status where user_id = ? and status = 'REJECTED'",
            Int::class.java, userId
        )!!

    fun saveDecision(userId: Long, status: String, reason: String?) {
        val query = """
            insert into kyc_status (user_id, status, rejection_reason, attempts, updated_at)
            values (?, ?, ?, 1, now())
            on conflict (user_id) do update set
                status = excluded.status,
                rejection_reason = excluded.rejection_reason,
                attempts = kyc_status.attempts + case when excluded.status = 'APPROVED' then 0 else 1 end,
                updated_at = now()
        """.trimIndent()
        jdbcTemplate.update(query, userId, status, reason)
    }
}
