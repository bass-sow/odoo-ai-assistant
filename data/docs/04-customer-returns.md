# Customer Returns

## Accepting a return

Returns are accepted only with an authorisation from customer service, linked to the
original delivery (WH/OUT). The driver brings the goods with the return form.

In Odoo, open the original delivery and click "Return". Select the lines and quantities
that come back. The return creates a receipt into WH/Quarantine, never directly into
sellable stock.

## Chilled and frozen returns

Chilled and frozen products returned by customers cannot be put back on sale because the
cold chain cannot be guaranteed. They are scrapped after the quality check, even if the
lot is not expired. The scrap reason is "Customer return - cold chain".

## Dry goods returns

Dry goods in intact packaging with more than 60 days of shelf life can go back to
stock after the quality check. Move them from WH/Quarantine to their storage location
with an internal transfer. Damaged packaging means scrap.

## Credit notes

The warehouse does not issue credit notes. Once the return receipt is validated,
customer service creates the credit note from the sales order.
