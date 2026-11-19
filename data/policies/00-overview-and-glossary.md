# DisputeDesk Rulebook: Overview and Glossary

> **Fictional policy.** Written for the DisputeDesk demo project. These are not real card-network,
> issuer or regulatory rules, and must not be used to decide real disputes.

## Purpose of the rulebook

The DisputeDesk Rulebook is the single source of rules that DisputeDesk analysts and automated
agents apply when a cardholder disputes a card transaction. It defines which disputes qualify,
what evidence each side must provide, the deadlines that apply, how refunds are calculated and
when a human approver must sign off.

Every decision and every letter must cite the rulebook section it relies on, using the document
name and section heading (for example: `04-merchandise-not-received.md`, "Delivery proof").

## How the rulebook is organised

| Document | Covers |
|---|---|
| 01-reason-code-catalog | The list of reason codes and how to choose one |
| 02 to 07 | One document per reason-code family (fraud, not received, services, duplicate, not as described) |
| 08-time-limits | All filing, response and decision deadlines |
| 09-evidence-standards | What counts as evidence and how much weight it carries |
| 10-refunds-and-provisional-credit | How refund amounts are calculated and paid |
| 11-approval-thresholds-and-escalation | When a human approver is required |
| 12-merchant-response-and-representment | How merchants answer a dispute and rebut a decision |
| 13-merchant-risk-monitoring | Merchant dispute rates and repeat-dispute cardholders |
| 14-decision-letters | Content and data rules for letters to cardholders and merchants |

## Order of precedence

When two sections appear to conflict, apply them in this order:

1. `08-time-limits.md` is authoritative for every deadline.
2. `11-approval-thresholds-and-escalation.md` is authoritative for who may finalise a decision.
3. The reason-code document (02 to 07) is authoritative for qualification and evidence specific to
   that code.
4. General documents (09, 10, 12, 13, 14) apply where the reason-code document is silent.

If a conflict cannot be resolved with this order, the case is escalated to a human approver.

## Glossary

- **Settlement date**: the date the transaction posted to the cardholder's account. Most filing
  windows are counted from this date.
- **Expected delivery date**: the delivery or service date the merchant promised at the time of
  sale. If none was given, it is 30 calendar days after the settlement date.
- **Filing date**: the date the cardholder submitted a complete dispute.
- **Notification date**: the date DisputeDesk notified the merchant of the dispute.
- **Provisional credit**: a temporary credit to the cardholder while the dispute is open.
- **Compelling evidence**: merchant evidence strong enough, on its own, to show that the
  cardholder participated in or benefited from the transaction.
- **Representment**: the merchant's single formal rebuttal of a decision made against it.
- **Analyst**: a person or automated agent that investigates and recommends a decision.
- **Approver**: a person with authority to finalise decisions above the automated limit.
- **Accept**: the dispute is upheld and the cardholder is refunded.
- **Reject**: the dispute is denied and any provisional credit is reversed.
- **Escalate**: the case is passed to a human approver without an automated decision.
