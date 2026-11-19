# F11: Card-Present Fraud

> **Fictional policy.** Written for the DisputeDesk demo project. These are not real card-network,
> issuer or regulatory rules, and must not be used to decide real disputes.

## When F11 applies

F11 covers transactions made at a physical terminal (channel `card_present`) that the cardholder
says they did not make. The cardholder requirements of F10 (fraud declaration, card blocked and
reissued, no credentials shared) apply to F11 as well.

## How the card was read

The terminal entry mode is the most important fact in an F11 dispute.

### Chip and PIN

A chip read verified with the correct PIN creates a presumption that the cardholder or someone
they gave the card and PIN to made the transaction. The dispute is **rejected** unless one of the
following is true:

- The cardholder reported the card lost or stolen **before** the transaction time.
- DisputeDesk had already notified the cardholder that the card was exposed in a known
  compromise before the transaction date.

### Chip without PIN

A chip read with signature or no cardholder verification does not create the presumption. Decide
on the remaining evidence.

### Magnetic stripe fallback or key-entered

If the terminal fell back to magnetic stripe, or the card number was typed in manually, the
transaction is treated as high risk for counterfeit cards. Without other evidence of cardholder
participation, the dispute is **accepted**.

### Contactless

Contactless transactions of $100.00 or less with no cardholder verification are **accepted** when
the cardholder reported the card lost or stolen within 2 calendar days after the transaction.
Above $100.00, contactless transactions follow the chip-without-PIN rule.

## Lost or stolen cards

Any card-present transaction made **after** the time the cardholder reported the card lost or
stolen is always **accepted**, regardless of entry mode. The merchant cannot defeat this with
any evidence.

## Merchant evidence for F11

Useful merchant evidence includes a terminal receipt showing entry mode and verification method,
CCTV stills linked to the transaction time, and a signed receipt matching the cardholder's
signature on file. CCTV and signatures are supporting evidence only and never override the
lost-or-stolen rule.
