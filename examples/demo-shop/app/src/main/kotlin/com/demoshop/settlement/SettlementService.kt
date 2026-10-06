package com.demoshop.settlement

import com.demoshop.common.OrderStatus
import org.slf4j.LoggerFactory
import org.springframework.stereotype.Service
import org.springframework.transaction.annotation.Transactional
import java.time.LocalDate

/**
 * T+1 settlement: an order paid before 3 pm IST settles on the next business day; an order paid
 * after the cut-off settles one business day later. Weekends and exchange holidays are skipped.
 * Settling an order marks it SETTLED and records a row in `settlements`.
 */
@Service
class SettlementService(
    private val settlementRepository: SettlementRepository,
    private val calendar: SettlementCalendar,
) {
    private val log = LoggerFactory.getLogger(javaClass)

    @Transactional
    fun settleDueOrders(today: LocalDate = LocalDate.now(calendar.zone)): SettlementReport {
        if (!calendar.isBusinessDay(today)) return SettlementReport(today, 0, 0)
        val due = settlementRepository.findOrdersDueOn(today, calendar.cutoffWindowEnding(today))
        var failed = 0
        for (orderId in due) {
            try {
                settlementRepository.settle(orderId, today)
            } catch (e: Exception) {
                failed++
                log.error("Settlement failed for order {} — it stays ${OrderStatus.PAID} and is retried tomorrow", orderId, e)
            }
        }
        return SettlementReport(today, due.size - failed, failed)
    }
}

data class SettlementReport(val date: LocalDate, val settled: Int, val failed: Int)
