# Time Limits

> **Fictional policy.** Written for the DisputeDesk demo project. These are not real card-network,
> issuer or regulatory rules, and must not be used to decide real disputes.

This document is authoritative for every deadline in the rulebook. "Days" means calendar days
unless business days are stated.

## Cardholder filing windows

| Reason codes | Window | Counted from |
|---|---|---|
| F10, F11 | 120 days | Settlement date |
| P30 | 120 days | Settlement date of the later charge |
| R20 | 120 days | Expected delivery date |
| R21 | 120 days | Scheduled service date, or settlement date if later |
| Q40, Q41, Q42 | 120 days | Delivery date |

### Absolute limit of 540 days

For R20, R21 and the Q codes, no dispute may be filed more than **540 days after the settlement
date**, even if the window counted from the delivery or service date has not ended.

### Late filing

A dispute filed after its window is **rejected as out of time** without review of the merits.
The only exception is a fraud dispute where the cardholder shows they could not have seen the
transaction earlier, for example because statements were sent to an address changed by a
fraudster. Such exceptions require an approver.

## Waiting periods

- R20: the cardholder must wait 15 days after the expected delivery date before filing.
- Q40 and Q41: the cardholder must wait 15 days after first contacting the merchant.

## Merchant deadlines

- **Merchant response**: 30 days from the notification date. Evidence received after day 30 is
  not considered.
- **Extension**: an analyst may grant one extension of 5 business days if the merchant shows a
  carrier or system outage prevented them from responding. Merchants in the high-risk tier are
  not eligible for extensions.
- **Representment**: 30 days from the date of the decision letter.

## Cardholder deadlines after filing

- Answer a request for missing information: 10 days. If there is no answer, the dispute is
  closed and any provisional credit is reversed.
- Rebut a merchant representment: 10 days.
- Report that a "not received" item has arrived: 5 days after it arrives.

## DisputeDesk deadlines

- **Provisional credit**: within 10 business days of the filing date.
- **Decision**: within 45 days of the filing date.
- **Escalated cases**: the decision deadline is extended by 15 days, to a maximum of 60 days.
- **Notice before reversing provisional credit**: 5 business days.
