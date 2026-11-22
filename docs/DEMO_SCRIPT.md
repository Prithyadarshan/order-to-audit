# Demo script (8 minutes). Run `python -m app.seed --reset` first, then `uvicorn app.main:app`.

Use a phone-sized browser window for steps 4 and 6, a desktop window for the rest.

1. (0:00) Production, Purchases: Cauvery Flour Mills, flour 60 kg at 45. Show Stock: flour 160 kg.
2. (0:40) Production, Start run: Murukku 200 g, change oil to 10.5 L, Start. Show flour 120 kg, oil 19.5 L.
3. (1:30) On the run page, Record actual output: good 188, damaged 6, cartons 9. Show the flagged yield (-6%) and oil (+5%), pouches 306, finished goods 238.
4. (2:30) Sign in as salesman_a on the phone view: customer Retailer A, 150 packets, Send order. (Optional: switch the browser offline, send, go online and watch it sync.)
5. (3:30) Admin: open the order, see stock and the CGST+SGST preview, Approve, Raise GST invoice, Download PDF, Record dispatch. Finished goods 88.
6. (4:45) salesman_b: Distributor B, 120 packets. Admin opens it: shortage of 32, IGST preview, approval refused. Click "Reduce to available stock" or reject.
7. (5:45) Admin on the invoice: payment 4,000 by UPI. Outstanding 3,875.
8. (6:15) Production run page: add labour 1,200 and gas/power 600. Cost per packet Rs 31.14, margin.
9. (6:45) Accountant: Reports, open Sales register, GST summary, Stock ledger, Production, Outstanding; export one to Excel and one to PDF.
10. (7:30) Owner: dashboard with variance alerts, cost per packet, outstanding dues.
