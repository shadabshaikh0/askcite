package com.demoshop.orders

import com.demoshop.common.Limits
import com.demoshop.common.OrderStatus
import com.demoshop.kyc.KycService
import com.demoshop.payments.RefundService
import com.demoshop.settlement.SettlementCalendar
import org.springframework.stereotype.Service
import java.time.Instant

@Service
class OrderService(
    private val orderRepository: OrderRepository,
    private val kycService: KycService,
    private val refundService: RefundService,
    private val settlementCalendar: SettlementCalendar,
) {

    /**
     * A customer can order only when their KYC is approved, the product is open for investment,
     * and the amount is at least MIN_ORDER_AMOUNT (₹1,000).
     */
    fun placeOrder(userId: Long, productId: Long, amount: Long): Order {
        require(kycService.isApproved(userId)) { "KYC must be approved before ordering" }
        require(orderRepository.isProductActive(productId)) { "This product is not open for investment" }
        require(amount >= Limits.MIN_ORDER_AMOUNT) { "Minimum order is ₹${Limits.MIN_ORDER_AMOUNT}" }
        return orderRepository.insert(userId, productId, amount)
    }

    /**
     * Cancellation is allowed only before settlement:
     *  - CREATED orders (not paid yet) are cancelled immediately.
     *  - PAID orders can be cancelled until the settlement cut-off of their settlement day;
     *    the money is refunded within REFUND_SLA_WORKING_DAYS working days.
     *  - SETTLED, CANCELLED and FAILED orders cannot be cancelled.
     */
    fun cancelOrder(orderId: Long, reason: String): Order {
        val order = orderRepository.findById(orderId) ?: throw NoSuchElementException("Order $orderId not found")
        when (order.status) {
            OrderStatus.CREATED -> orderRepository.updateStatus(orderId, OrderStatus.CANCELLED, reason)
            OrderStatus.PAID -> {
                check(!settlementCalendar.isPastCutoff(order.paidAt!!, Instant.now())) {
                    "Too late to cancel: this order is already being settled"
                }
                orderRepository.updateStatus(orderId, OrderStatus.CANCELLED, reason)
                refundService.refund(orderId, order.amount, RefundService.REASON_ORDER_CANCELLED)
            }
            else -> throw IllegalStateException("Orders in status ${order.status} cannot be cancelled")
        }
        return orderRepository.findById(orderId)!!
    }

    fun getOrder(orderId: Long): Order =
        orderRepository.findById(orderId) ?: throw NoSuchElementException("Order $orderId not found")
}

data class Order(val orderId: Long, val userId: Long, val productId: Long, val amount: Long, val status: String,
                 val createdAt: Instant, val paidAt: Instant?, val settledAt: Instant?)
