package com.demoshop.settlement

import org.springframework.http.ResponseEntity
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController

/** Internal endpoint. The scheduler calls it every business day at 18:00 IST (CRON). */
@RestController
@RequestMapping("/internal/settlement")
class SettlementJobController(private val settlementService: SettlementService) {

    @PostMapping("/run")
    fun runSettlement(): ResponseEntity<SettlementReport> = ResponseEntity.ok(settlementService.settleDueOrders())
}
