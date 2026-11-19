# Merchant Risk Monitoring and Repeat Disputes

> **Fictional policy.** Written for the DisputeDesk demo project. These are not real card-network,
> issuer or regulatory rules, and must not be used to decide real disputes.

## Merchant dispute rate

A merchant's dispute rate is:

> disputes filed in the last 90 days ÷ settled transactions in the last 90 days

Refunded transactions are excluded from the denominator.

## Risk tiers

| Tier | Dispute rate | Effect |
|---|---|---|
| Low | Below 0.75% | Standard handling |
| Medium | 0.75% to 1.5% | Merchant is placed on watch and reviewed monthly |
| High | Above 1.5% | Enhanced scrutiny of merchant evidence; no response extensions |

A merchant also moves to the high tier if it has more than 25 disputes in 90 days, whatever its
dispute rate.

## How risk tier affects a case

Risk tier changes **how carefully** merchant evidence is checked, not **which rules** apply:

- For high-tier merchants, carrier tracking must be confirmed directly with the carrier, even
  when it comes from an approved carrier.
- High-tier merchants cannot receive the 5-business-day response extension.
- A case is never accepted or rejected **because of** the merchant's tier alone. The decision
  must still rest on the reason-code rules and the evidence.

## Repeat-dispute cardholders

A cardholder who has filed **three or more disputes in the last 90 days** is flagged for a
first-party misuse check:

- the case is escalated to a human approver (see `11-approval-thresholds-and-escalation.md`),
- provisional credit waits for approver review, and
- the approver checks for a pattern, such as repeated "not received" claims on orders later
  shown as delivered.

Being flagged is not proof of misuse. Each dispute is still decided on its own evidence.

## Reporting

Merchant tier changes and repeat-dispute flags are recorded in the audit log with the date,
the metric values that caused the change, and the actor.
