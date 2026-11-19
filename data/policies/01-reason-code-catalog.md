# Reason Code Catalog

> **Fictional policy.** Written for the DisputeDesk demo project. These are not real card-network,
> issuer or regulatory rules, and must not be used to decide real disputes.

## Reason code list

Every dispute is assigned exactly one DisputeDesk reason code. The code decides which rules,
evidence requirements and deadlines apply.

| Code | Name | Internal category | Rules document |
|---|---|---|---|
| F10 | Card-absent fraud | fraud | 02-fraud-card-absent.md |
| F11 | Card-present fraud | fraud | 03-fraud-card-present.md |
| R20 | Merchandise not received | not_received | 04-merchandise-not-received.md |
| R21 | Services not rendered | not_received | 05-services-not-rendered.md |
| P30 | Duplicate processing | duplicate | 06-duplicate-processing.md |
| Q40 | Not as described | not_as_described | 07-not-as-described.md |
| Q41 | Defective or damaged goods | not_as_described | 07-not-as-described.md |
| Q42 | Counterfeit goods | not_as_described | 07-not-as-described.md |

## Choosing a reason code

Ask these questions in order and stop at the first "yes":

1. Does the cardholder say they did not make or authorise the transaction? Use **F10** if the
   transaction was online, or **F11** if the card was physically present at a terminal.
2. Does the cardholder recognise the merchant but report being charged more than once for the
   same purchase? Use **P30**.
3. Did the cardholder pay for physical goods that never arrived? Use **R20**.
4. Did the cardholder pay for a service, booking, subscription or digital product that was not
   provided? Use **R21**.
5. Did the goods arrive but differ from the description (**Q40**), arrive broken or stop working
   (**Q41**), or turn out to be fake (**Q42**)?

## Reclassification

An analyst may change the reason code once, before the merchant is notified, if the evidence
shows the first code was wrong. Typical examples:

- A "fraud" claim where the cardholder later confirms they placed the order but it never arrived
  becomes **R20**.
- A "duplicate" claim where the two charges have different amounts becomes **Q40** or is treated
  as a billing question, because P30 requires identical amounts.

After the merchant has been notified, the reason code is fixed. A wrong code at that stage means
the dispute is decided on the existing code or withdrawn and refiled within the original filing
window.

## Disputes that do not qualify

The following never qualify under any reason code:

- The cardholder changed their mind after a valid purchase (buyer's remorse).
- A quality complaint about food or services that were fully consumed.
- A disagreement about a price the cardholder agreed to at the time of sale.
- A transaction already fully refunded by the merchant before the filing date.
