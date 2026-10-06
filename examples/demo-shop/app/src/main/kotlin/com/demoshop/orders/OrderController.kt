package com.demoshop.orders

import org.springframework.http.ResponseEntity
import org.springframework.web.bind.annotation.*

@RestController
@RequestMapping("/orders")
class OrderController(private val orderService: OrderService) {

    @PostMapping
    fun placeOrder(@RequestBody request: PlaceOrderRequest): ResponseEntity<Order> =
        ResponseEntity.ok(orderService.placeOrder(request.userId, request.productId, request.amount))

    @PostMapping("/{orderId}/cancel")
    fun cancelOrder(@PathVariable orderId: Long, @RequestBody request: CancelRequest): ResponseEntity<Order> =
        ResponseEntity.ok(orderService.cancelOrder(orderId, request.reason))

    @GetMapping("/{orderId}")
    fun getOrder(@PathVariable orderId: Long): ResponseEntity<Order> =
        ResponseEntity.ok(orderService.getOrder(orderId))
}

data class PlaceOrderRequest(val userId: Long, val productId: Long, val amount: Long)
data class CancelRequest(val reason: String)
