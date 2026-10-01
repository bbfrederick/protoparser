# Historical driver submission

`driver_submitted.exar1` is an unchanged copy of the XA60 file originally
submitted as `loadtest/loadtest_DRIVER.exar1` on 2026-09-03. Its scanner return
is already shipped as `examples/XA60/driver_loadtest.exar1`, byte-identical to
`loadtest/loadtest_DRIVER_reexport.exar1`.

SHA-256:

- Submission: `37ef6553885a916d9c17846b77be5b29068e59f6cdc4227807282de1787059ac`
- Return: `aadcfd49f8381c4f52fdc2e0be1cba74c7de4b25913816351487489a26defb8b`

The submission predates the current UI-only writing rules. It deliberately
retains orphan content rows and inconsistent slice spacing; the scanner
returned that spacing unchanged. Comparison tests also retain the observed
scale/SNR, private `sIR`, and navigator filename changes as substantive
differences. This pair tests historical evidence, not acceptance of today's
writer or the new scanner trial.
