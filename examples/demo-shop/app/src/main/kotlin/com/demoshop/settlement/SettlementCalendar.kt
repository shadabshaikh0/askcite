package com.demoshop.settlement

import com.demoshop.common.Limits
import org.springframework.stereotype.Component
import java.time.DayOfWeek
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.time.ZonedDateTime

@Component
class SettlementCalendar(private val holidays: Set<LocalDate> = emptySet()) {
    val zone: ZoneId = ZoneId.of("Asia/Kolkata")

    fun isBusinessDay(day: LocalDate): Boolean =
        day.dayOfWeek !in setOf(DayOfWeek.SATURDAY, DayOfWeek.SUNDAY) && day !in holidays

    /** The settlement day of a payment: next business day, or one more if paid after the 3 pm cut-off. */
    fun settlementDay(paidAt: Instant): LocalDate {
        val local = ZonedDateTime.ofInstant(paidAt, zone)
        var day = nextBusinessDay(local.toLocalDate())
        if (local.hour >= Limits.SETTLEMENT_CUTOFF_HOUR_IST) day = nextBusinessDay(day)
        return day
    }

    fun isPastCutoff(paidAt: Instant, now: Instant): Boolean {
        val cutoff = settlementDay(paidAt).atTime(Limits.SETTLEMENT_CUTOFF_HOUR_IST, 0).atZone(zone).toInstant()
        return now.isAfter(cutoff)
    }

    /** Payments made up to this moment are due for settlement on `day`. */
    fun cutoffWindowEnding(day: LocalDate): Instant {
        var previous = day.minusDays(1)
        while (!isBusinessDay(previous)) previous = previous.minusDays(1)
        return previous.atTime(Limits.SETTLEMENT_CUTOFF_HOUR_IST, 0).atZone(zone).toInstant()
    }

    private fun nextBusinessDay(day: LocalDate): LocalDate {
        var next = day.plusDays(1)
        while (!isBusinessDay(next)) next = next.plusDays(1)
        return next
    }
}
