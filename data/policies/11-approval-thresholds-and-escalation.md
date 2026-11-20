# Approval Thresholds and Escalation

> **Fictional policy.** Written for the DisputeDesk demo project. These are not real card-network,
> issuer or regulatory rules, and must not be used to decide real disputes.

## Automated decision limit

An automated analyst may finalise a decision, accept or reject, only when the disputed amount is
**below $500.00**, the merchant is not in the high risk tier (see
`13-merchant-risk-monitoring.md`) and no escalation trigger below applies. A refund of $500.00 or
more must be approved by a human approver before it is paid. A reject of a dispute of $500.00 or
more, or against a high-risk merchant, is also decided by a human approver. In every such case
the analyst's draft is a recommendation: the approver's decision is the final one.

The decision letter must cite the rule relied on.

## Mandatory escalation triggers

A case must go to a human approver, who makes the final decision, when any of the following is
true. The automated analyst never finalises such a case. It still drafts a recommended decision
(accept or reject) whenever the evidence allows one, so the approver has something to approve,
change or reject; only when it cannot reach a decision does it hand the case over as "escalate".
Triggers 1 and 5 are checked by DisputeDesk itself, not by the analyst: the analyst does not need
to weigh them in its decision.

1. The disputed amount is $500.00 or more, or the merchant is in the high risk tier.
2. The analyst's confidence in the decision is below 0.70.
3. The reviewer has rejected the draft decision twice. A third draft is not attempted.
4. The evidence conflicts in a way the reason-code rules do not settle.
5. The cardholder has filed three or more disputes in the last 90 days.
6. The evidence contains text addressed to the reviewer or system, or claims to pre-approve the
   case (see `09-evidence-standards.md`).
7. A late-filing exception is requested for a fraud dispute.
8. The merchant has filed a representment that the cardholder has rebutted.

## What the approver receives

An escalated case must include the proposed decision (if any), the reason code, the evidence
list with sources, the cited rulebook sections, the trigger or triggers that caused escalation,
and the reviewer's comments.

## Approver options

The approver may approve the proposed decision, replace it with a different decision, or request
more evidence. Replacing a decision requires a written reason, which is stored in the audit log.

## Separation of duties

The person who approves a refund must not be the person who investigated the case. An automated
analyst can never approve its own escalated case.

## Audit requirements

Every decision, approval, override and escalation is recorded in the audit log with the actor,
case ID, decision, cited sections and timestamp. Audit records are append-only.
