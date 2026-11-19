# F10: Card-Absent Fraud

> **Fictional policy.** Written for the DisputeDesk demo project. These are not real card-network,
> issuer or regulatory rules, and must not be used to decide real disputes.

## When F10 applies

F10 covers online, phone and mail-order transactions (channel `online`) that the cardholder says
they neither made nor authorised, and from which they received no benefit. If the cardholder
admits placing the order, F10 does not apply; see the reclassification rules in the reason code
catalog.

## Cardholder requirements

Before an F10 dispute can be decided, the cardholder must:

1. Confirm in a signed fraud declaration that they did not make, authorise or benefit from the
   transaction.
2. Have the card blocked and a replacement issued. A decision may not be finalised while the
   original card is still active.
3. Confirm they did not share their card details or one-time passcodes with anyone.

A dispute missing any of these items stays open and the cardholder is asked to complete it.

## Signals that support the cardholder

These signals make an F10 claim more likely to be accepted:

- The address verification result was a mismatch, or the security code check failed.
- The order shipped to an address never previously associated with the cardholder.
- The transaction was the first ever between this card and this merchant.
- Several transactions at unrelated merchants were made within a short period from the same new
  device.

## Compelling evidence of cardholder participation

The merchant can defeat an F10 claim only with compelling evidence. Any one of the following is
compelling:

- **Repeat customer match**: two or more earlier, undisputed transactions with the same merchant
  in the 120 days before the disputed transaction, using the same device identifier or the same
  shipping address as the disputed order.
- **Strong authentication plus known address**: the transaction passed SecureVerify step-up
  authentication **and** the goods were delivered to the cardholder's billing address on file.
- **Digital goods on a known device**: the purchased digital item was downloaded or used on a
  device that the cardholder has used for earlier undisputed purchases.

SecureVerify success on its own is **not** compelling evidence. It must be paired with delivery
to the billing address on file.

## Decision rules

- Compelling evidence present and not contradicted: **reject**.
- No compelling evidence and cardholder requirements met: **accept**.
- Compelling evidence present but the cardholder shows the card was reported lost or stolen
  before the transaction time: **accept**.
- Evidence conflicts in a way the rules above do not settle: **escalate**.
