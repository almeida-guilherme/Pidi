# Example: approving invoices

`invoice-approval.mp4` (77 seconds, silent) is a screen recording of an invented finance portal,
[`sample_app/index.html`](sample_app/index.html). Every company, person, invoice and amount in it is made up.

In it, an accounts-payable clerk signs in and filters the invoices pending approval. Each invoice is compared with its
purchase order: it's approved when the amounts match and flagged for review when they don't (INV-20437 is $110.50
over its order). Then the clerk exports the approved invoices to CSV.

## What Pidi wrote

[`invoice-approval-PDD.pdf`](invoice-approval-PDD.pdf) (also as [Word](invoice-approval-PDD.docx)): 15 pages with
14 steps and their screenshots, a flowchart with the three decisions, inputs, outputs, exceptions, an automation
viability assessment and, highlighted in yellow, what the recording can't tell (client, contacts, volumes).

It was made on OpenAI's default models in two passes, which cost $0.14 in all:

1. **The recording** ($0.10, about 2 minutes). The first draft had 10 steps. The recording is silent, so nothing
   said *why* the clerk opens the purchase order, and the draft skipped that check and the third invoice.
2. **One redo** ($0.02, 25 seconds), from three review notes, as an author would leave them on the project's review
   page:
   - step 5, *a step is missing after it*: "the clerk clicks 'View purchase order' and compares the amounts";
   - step 6, *wording*: "approve when the Difference is $0.00, flag for review when the amounts differ";
   - step 8, *a step is missing after it*: "the third invoice, INV-20440, is approved too".

   Pidi checked each note against the recording and fixed all three. The viability assessment, written again
   from the corrected steps, went from "partial" to "good candidate": the decision now follows a clear rule.

Narrating while recording ("I open the purchase order to check the amounts match") usually gets this right in the
first pass.

## Try it



```bash
.venv/bin/python main.py examples/invoice-approval.mp4
```

The recording is made by a script, so it can be changed and made again (needs Google Chrome or Chromium, and ffmpeg):

```bash
.venv/bin/python -m examples.make_sample
```
