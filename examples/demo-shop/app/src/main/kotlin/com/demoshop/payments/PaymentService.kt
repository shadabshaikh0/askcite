package com.demoshop.payments

import com.demoshop.common.Limits
import com.demoshop.common.OrderStatus
import com.demoshop.orders.OrderRepository
import org.slf4j.LoggerFactory
import org.springframework.stereotype.Service

@Service
class PaymentService(
    private val paymentRepository: PaymentRepository,
    private val orderRepository: OrderRepository,
    private val refundService: RefundService,
    private val notifier: CustomerNotifier,
) {
    private val log = LoggerFactory.getLogger(javaClass)

    /**
     * What happens when the gateway reports a payment:
     *  - SUCCESS: the order becomes PAID (and will settle on T+1).
     *  - FAILED: the customer is asked to retry. After MAX_PAYMENT_RETRIES failed retries
     *    (3 attempts in total) the order is marked FAILED and the customer is notified.
     *    If money was debited on a failed attempt, it is refunded automatically.
     */
    fun handleGatewayCallback(event: GatewayEvent) {
        paymentRepository.recordAttempt(event.orderId, event.status, event.mode, event.gatewayRef)
        when (event.status) {
            "SUCCESS" -> orderRepository.updateStatus(event.orderId, OrderStatus.PAID)
            "FAILED" -> handleFailure(event)
            else -> log.info("Payment for order {} is still pending", event.orderId)
        }
    }

    private fun handleFailure(event: GatewayEvent) {
        if (event.amountDebited > 0) {
            refundService.refund(event.orderId, event.amountDebited, RefundService.REASON_PAYMENT_FAILED_AFTER_DEBIT)
        }
        val failedAttempts = paymentRepository.countFailedAttempts(event.orderId)
        if (failedAttempts > Limits.MAX_PAYMENT_RETRIES) {
            orderRepository.updateStatus(event.orderId, OrderStatus.FAILED, "payment failed $failedAttempts times")
            notifier.paymentFailedFinal(event.orderId)
        } else {
            notifier.askToRetryPayment(event.orderId, Limits.MAX_PAYMENT_RETRIES - failedAttempts + 1)
        }
    }
}

interface CustomerNotifier {
    fun askToRetryPayment(orderId: Long, attemptsLeft: Int)
    fun paymentFailedFinal(orderId: Long)
}
