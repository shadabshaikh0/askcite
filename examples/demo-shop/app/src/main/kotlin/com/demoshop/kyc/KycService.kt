package com.demoshop.kyc

import com.demoshop.common.Limits
import org.springframework.stereotype.Service
import java.time.LocalDate
import java.time.Period

/**
 * KYC decision rules. A KYC submission is REJECTED when:
 *  - the name on the PAN does not match the name on the account,
 *  - the identity document has expired, or
 *  - the customer is younger than 18.
 * After MAX_KYC_ATTEMPTS rejected submissions the account is BLOCKED and only support can unblock it.
 */
@Service
class KycService(private val kycRepository: KycRepository) {

    fun isApproved(userId: Long): Boolean = kycRepository.status(userId) == "APPROVED"

    fun review(userId: Long, submission: KycSubmission): String {
        val reason = when {
            !namesMatch(submission.panName, submission.accountName) -> "PAN_NAME_MISMATCH"
            submission.documentExpiry.isBefore(LocalDate.now()) -> "DOCUMENT_EXPIRED"
            Period.between(submission.dateOfBirth, LocalDate.now()).years < 18 -> "UNDER_18"
            else -> null
        }
        if (reason == null) {
            kycRepository.saveDecision(userId, "APPROVED", null)
            return "APPROVED"
        }
        val attempts = kycRepository.rejectedAttempts(userId) + 1
        val status = if (attempts >= Limits.MAX_KYC_ATTEMPTS) "BLOCKED" else "REJECTED"
        kycRepository.saveDecision(userId, status, reason)
        return status
    }

    private fun namesMatch(a: String, b: String) =
        a.trim().lowercase().replace(Regex("\\s+"), " ") == b.trim().lowercase().replace(Regex("\\s+"), " ")
}

data class KycSubmission(val panName: String, val accountName: String, val documentExpiry: LocalDate,
                         val dateOfBirth: LocalDate)
