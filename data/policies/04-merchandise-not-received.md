# R20: Merchandise Not Received

> **Fictional policy.** Written for the DisputeDesk demo project. These are not real card-network,
> issuer or regulatory rules, and must not be used to decide real disputes.

## When R20 applies

R20 covers physical goods that the cardholder paid for and did not receive by the expected
delivery date. For services, bookings, subscriptions and digital products, use R21 instead.

## Waiting period before filing

The cardholder must wait until **15 calendar days after the expected delivery date** before
filing an R20 dispute. The waiting period is waived if:

- the merchant confirmed in writing that the order will not be shipped, or
- the merchant has stopped trading or cannot be contacted through any published channel.

A dispute filed during the waiting period is held, not rejected, and becomes active when the
period ends.

## Cardholder requirements

The cardholder must state the expected delivery date, the shipping address on the order, and the
date and method of at least one attempt to contact the merchant about the missing order.

## Delivery proof

The merchant defeats an R20 claim by proving delivery. Valid proof of delivery:

- comes from an approved carrier (see `09-evidence-standards.md`),
- shows a status of delivered,
- shows a delivery address that matches the shipping address on the order, and
- is dated on or before the filing date.

### Signature confirmation above $250

For orders above **$250.00**, carrier tracking alone is not conclusive. The merchant must also
provide signature confirmation from the carrier. Without a signature, an order above $250.00 is
treated as not delivered.

### Address mismatch

If the carrier shows delivery to an address that differs from the shipping address on the order,
delivery is not proven and the dispute is **accepted**, even if the address is nearby.

## Decision rules

- No shipment was created, or tracking never shows a carrier scan: **accept**.
- Valid proof of delivery: **reject**. The decision letter tells the cardholder they may open a
  claim with the carrier.
- Shipment is in transit and still within 15 days of the expected delivery date: hold the case.
- Only part of the order was delivered: **accept** a partial refund for the undelivered items
  (see `10-refunds-and-provisional-credit.md`).

## Item arrives after filing

If the item arrives while the dispute is open, the cardholder must notify DisputeDesk within 5
calendar days. The dispute is then withdrawn, unless the item is not as described, in which case
it may be reclassified to Q40 before the merchant is notified.
