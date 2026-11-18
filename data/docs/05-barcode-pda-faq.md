# Barcode App and PDA FAQ

Frequent questions from warehouse staff about the Odoo Barcode app on the PDAs.

## I cannot validate the picking

Usually one of these:

- A lot-tracked product has no lot on one of its lines. Scan the GS1 label or type the
  lot.
- You scanned a different lot from the reserved one (FEFO). Ask the supervisor.
- Your PDA user does not have permission to validate that operation type. Pickers can
  validate deliveries and internal transfers; only receiving staff can validate receipts.

## The label scans as the wrong product

The GTIN on the label is not linked to the product in Odoo, or it is linked to two
products. Report it to the warehouse admin, who fixes the barcode on the product or its
packaging. Meanwhile, select the product manually.

## How do I move stock between locations?

Barcode app > Operations > Internal Transfers > New. Scan the source location, the
product (and lot), the quantity, then the destination location, and validate.

## The PDA logged me out

Sessions expire after 8 hours. Log in again with your own user. Never use another
person's user: every move is traced to the user who validated it.

## Printing labels

Lot labels with GS1 DataMatrix are printed from the lot form in Odoo (Print > Lot
Label). The label printer in the receiving area is the default for receipts.
