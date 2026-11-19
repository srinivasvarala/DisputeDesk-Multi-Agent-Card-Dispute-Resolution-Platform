# Decision Letters

> **Fictional policy.** Written for the DisputeDesk demo project. These are not real card-network,
> issuer or regulatory rules, and must not be used to decide real disputes.

## Letters that must be sent

- **Cardholder decision letter**: sent when a dispute is accepted, rejected or closed.
- **Merchant decision letter**: sent to the merchant when a contested dispute is decided.
- **Information request**: sent to the cardholder when required evidence is missing, with the
  10-day deadline.

## Required content

Every decision letter must include:

1. The case ID and the transaction date and amount.
2. The reason code and its name.
3. The decision: accepted, rejected or closed.
4. The rulebook sections relied on, cited by document and section heading.
5. A list of the evidence considered, naming who supplied each item.
6. The refund amount and the date it will post, or the date provisional credit will be reversed.
7. What the recipient can do next and the deadline for doing it (for merchants, the 30-day
   representment window).

## Data minimisation

- Show only the **last four digits** of the card number.
- Never include the cardholder's email address, phone number or full home address in a letter
  to a merchant. Use the case ID instead.
- Never include internal risk scores, merchant risk tier or repeat-dispute flags in a letter to
  either party.

## Grounding

A letter may only state facts that appear in the case evidence and rules that appear in this
rulebook. It must not:

- promise an outcome, refund or timeline that the rulebook does not provide for,
- quote evidence that is not in the case file, or
- describe a rule without citing the section it comes from.

A draft letter that fails any of these checks is returned to the analyst for revision.

## Tone

Letters are neutral and plain. They describe the decision and the reason without blaming either
party. Avoid legal language, and do not describe a cardholder as dishonest even when a dispute is
rejected.

## Language for rejected disputes

A rejection letter must explain the specific rule that was not met and, where the rulebook
allows it, how the cardholder can still get help: for example, filing a claim with the carrier
after an R20 rejection, or refiling after returning the item for a Q40 rejection.
