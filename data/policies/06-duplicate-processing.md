# P30: Duplicate Processing

> **Fictional policy.** Written for the DisputeDesk demo project. These are not real card-network,
> issuer or regulatory rules, and must not be used to decide real disputes.

## When P30 applies

P30 covers a single purchase that was charged to the card more than once. Two charges are
treated as possible duplicates only when all of the following match:

- the same card,
- the same merchant,
- the same amount and currency, and
- both charges settled within **3 calendar days** of each other.

If the amounts differ, P30 does not apply. Reclassify the dispute as described in the reason
code catalog.

## Cardholder requirements

The cardholder must identify both charges and state that only one purchase was made. Unlike Q40,
the cardholder does **not** need to contact the merchant before filing a P30 dispute.

## Which charge is disputed

The dispute is always raised against the **later** of the two charges. The earlier charge is
treated as the valid purchase.

## Merchant evidence

The merchant defeats a P30 claim with either of the following:

- **Two separate orders**: distinct order numbers or invoices, each with its own items, and
  proof that both orders were fulfilled (for example, two shipments with different tracking
  numbers).
- **Already reversed**: proof that one of the two charges was already refunded or voided before
  the filing date.

## Decision rules

- Charges match the P30 definition and the merchant does not prove two separate orders:
  **accept**, refunding the amount of one charge.
- Merchant proves two separate orders: **reject**.
- One of the charges was already refunded: **reject**, because the cardholder has already been
  made whole. No double recovery is allowed.
- More than two identical charges: each extra charge after the first is treated as its own
  duplicate within the same case.

## Common mistakes

- A pre-authorisation hold that dropped off without settling is not a charge and cannot be a
  duplicate.
- A split shipment billed in two parts, with different amounts, is not a duplicate.
