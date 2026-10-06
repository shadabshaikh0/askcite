package com.demoshop.payments

import org.springframework.http.ResponseEntity
import org.springframework.web.bind.annotation.*

@RestController
@RequestMapping("/payments")
class PaymentController(private val paymentService: PaymentService) {

    /** Called by the payment gateway for every payment attempt. */
    @PostMapping("/webhook")
    fun gatewayCallback(@RequestBody event: GatewayEvent): ResponseEntity<Unit> {
        paymentService.handleGatewayCallback(event)
        return ResponseEntity.ok().build()
    }
}

data class GatewayEvent(val orderId: Long, val status: String, val mode: String, val gatewayRef: String,
                        val amountDebited: Long)
