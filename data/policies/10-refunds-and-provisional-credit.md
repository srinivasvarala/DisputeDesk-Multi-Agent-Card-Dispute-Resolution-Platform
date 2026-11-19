# Refunds and Provisional Credit

> **Fictional policy.** Written for the DisputeDesk demo project. These are not real card-network,
> issuer or regulatory rules, and must not be used to decide real disputes.

## Provisional credit

DisputeDesk credits the disputed amount to the cardholder's account within 10 business days of
the filing date while the dispute is investigated. Provisional credit is **not** given when:

- the dispute is filed outside its window,
- the transaction was already fully refunded by the merchant, or
- the cardholder has three or more open disputes (see `13-merchant-risk-monitoring.md`), in
  which case the credit waits for approver review.

## Reversing provisional credit

If the dispute is rejected, the provisional credit is reversed. The cardholder must receive at
least 5 business days' written notice before the reversal, stating the decision, the rule relied
on, and the date the reversal will post.

## Calculating the refund amount

The final refund is:

> disputed amount, minus any amount the merchant has already refunded for the same purchase

and is subject to these limits:

- The refund can never exceed the original transaction amount.
- For partial non-delivery or partial defects, refund only the price of the affected items plus
  a proportional share of shipping.
- For partial services (R21), refund the unused portion on a pro-rata basis by days or units.
- For P30, refund the amount of one duplicate charge per extra charge.

## Fees and interest

When a dispute is accepted, any interest and late fees charged on the disputed amount are also
reversed. Foreign transaction fees are refunded in proportion to the refunded amount.

## Currency and destination

- Refunds are made in the original transaction currency.
- Refunds always go back to the card used for the original transaction. They are never paid to a
  different card, a bank account, store credit or a gift card.

## Transactions already refunded

If the transaction status shows it was already refunded in full, the dispute is closed with no
further refund. If it was partially refunded, only the remaining balance may be disputed.

## Who may approve a refund

The amount limits for automated refunds, and when a human approver must sign off, are set in
`11-approval-thresholds-and-escalation.md`.
