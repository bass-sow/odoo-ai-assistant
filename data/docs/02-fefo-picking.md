# FEFO Picking

FEFO (First Expired, First Out) is the removal strategy for every product with an
expiration date. The lot that expires first leaves the warehouse first, regardless of
when it was received.

## How Odoo applies FEFO

The product category has the removal strategy "First Expiry First Out (FEFO)". When a
delivery order is confirmed, Odoo reserves the lots with the closest removal date. The
removal date is computed from the expiration date minus the product's removal time.

Pickers must take the lot shown on the PDA. Scanning a different lot creates a mismatch
and the picking cannot be validated without a supervisor.

## When FEFO can be skipped

FEFO can only be skipped in two cases:

1. The customer contract requires a minimum remaining shelf life (for example hotels
   asking for at least 10 days) and the first lot does not meet it. The lot stays for
   another customer.
2. The lot is blocked in quarantine by the quality team.

In both cases the supervisor changes the reservation in Odoo (Unreserve, then select
the lot manually) and writes the reason in the delivery note.

## Minimum shelf life on delivery

Unless the customer contract says otherwise, chilled products must leave with at least
5 days of shelf life and frozen products with at least 30 days. Lots below that limit
must not be delivered: report them as described in the expired and near-expiry stock
procedure.
