# Receiving Goods

Procedure for receiving supplier deliveries at the dock. Applies to all incoming
transfers (WH/IN) for chilled, frozen and dry goods.

## Before unloading

Check that the truck matches an expected receipt in Odoo: Inventory > Operations >
Receipts, filter "Ready". The delivery note (albarán) must reference our purchase order
(P0xxxx). If there is no purchase order, do not unload: call the purchasing team.

For chilled goods, measure the temperature of the truck before opening more than one
pallet. Chilled products must arrive between 0 °C and 4 °C, frozen products at -18 °C or
colder. Write the reading in the receipt's internal note.

## Scanning with GS1 labels

Open the receipt in the Barcode app on the PDA and scan the GS1-128 or GS1 DataMatrix
label of each pallet or case. Odoo reads the application identifiers automatically:

- (01) GTIN: identifies the product.
- (10) Lot or batch number: filled in as the lot of the move line.
- (17) Expiration date in YYMMDD format: filled in as the lot's expiration date.
- (37) Quantity of units in the case.

If a label cannot be read, type the lot number manually and take a photo of the label.
Never invent a lot number: a product tracked by lot cannot be validated without its
real lot.

## Quantity and quality differences

If the quantity received differs from the purchase order, register what was really
received and validate. Odoo will ask whether to create a backorder: choose
"Create Backorder" if the supplier will send the rest, "No Backorder" if not.

Damaged cases, broken cold chain or products with less than 50% of their shelf life
left are moved to WH/Quarantine and reported to the quality manager the same day.

## Validation

Validate the receipt only when every lot-tracked line has a lot and an expiration date.
Put chilled goods away within 30 minutes of unloading.
