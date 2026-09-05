# Excel traps that produce wrong verdicts

These are the failure modes where a check looks like it ran cleanly and the answer is simply
wrong. Almost all of them produce a false `PASS` rather than a false `FAIL`, which is the
direction that hurts, so treat each as something to rule out before writing a verdict.

## Comparing numbers

**Never test equality on floats.** `0.1 + 0.2` is not `0.3` in Excel any more than anywhere
else, and a chain of currency arithmetic accumulates. Pick a tolerance from the units and say
it in `process`: 0.01 for currency, something proportional for large aggregates. A
difference of 1e-9 between two totals is agreement; reporting it as a `FAIL` wastes someone's
afternoon.

**Displayed values lie.** A cell showing `100.00` may hold 99.9962. If the check is about the
underlying value, read the value, not the rendering. If the check is genuinely about what a
reader sees — a report that must foot on the page — say which of the two you tested, because
the answers differ and both are legitimate questions.

**Rounding is not the same as tolerance.** A total that agrees only after rounding to whole
units is a real finding if the test asked for a tie to the cent. Report what you found rather
than picking the tolerance that makes it pass.

**Error values propagate through some functions and not others.** A single `#N/A` in a range
makes `SUM` return `#N/A` but leaves `SUBTOTAL` and `AGGREGATE` variants unaffected depending
on options. Before comparing two totals, check both ranges for `#REF!`, `#N/A`, `#DIV/0!`,
`#VALUE!`, `#NAME?` and `#NUM!`, and say in `process` whether you found any. Errors in the
data are usually the more important finding than whatever the test was nominally about.

**Blank is not zero.** `COUNT` ignores blanks, `COUNTA` counts anything non-empty including
empty strings returned by formulas like `=IF(A1="","",…)`, and `AVERAGE` skips blanks but not
zeros. A test about "every row has a value" needs to decide whether a formula-produced empty
string counts, and you should say which reading you took.

## Comparing text

**Numbers stored as text** compare and sum as nothing. They arrive from imports, from leading
apostrophes, and from columns formatted as text before entry. If a numeric range sums to
something implausible, check whether some entries are text before concluding the data is
wrong.

**Whitespace and non-breaking spaces.** Trailing spaces and `CHAR(160)` from web or PDF
imports break exact matches invisibly. When a text comparison fails, check for this before
reporting a mismatch — and when it passes, it passed genuinely, so no action needed.

**Case.** Excel's `=` is case-insensitive but `EXACT` is not. Decide which the test meant and
say so.

## Structure and what you are actually reading

**Merged cells** hold their value in the top-left cell and leave the rest genuinely empty. A
range scan across merged headers will report blanks that aren't blanks.

**Hidden rows and active filters change what "the data" means.** `SUM` includes hidden rows;
`SUBTOTAL` and `AGGREGATE` can exclude them. If a filter is active on a range you're testing,
the owner's mental model of that range may be the filtered view while yours is the whole
thing. Say which you tested. Do not change the filter to find out.

**Inflated used ranges.** A sheet can report a used range extending to row 1048576 because
something was once pasted there. Don't treat the used range as the data range — find where the
data actually stops.

**Whole-column references** in formulas (`SUM(D:D)`) will pick up anything added below the
intended block, including stray notes. Worth flagging when a test is about totals.

**Pivot tables show cached data.** What a pivot displays may predate the current state of its
source. A test comparing a pivot to its source is testing the cache unless it has been
refreshed, and refreshing is out of scope here — so this is usually `INCONCLUSIVE`, and worth
saying why.

**External links may show stale cached values** for the same reason. If a test depends on one,
say that the value read is the cached one.

**Protected sheets** may return restricted or no data. That is an `ERROR` if the read fails
outright, and worth naming in `process` either way, because it tells the owner why
coverage is incomplete.

## Time and reproducibility

**Volatile functions make results unreproducible.** `TODAY`, `NOW`, `RAND`, `RANDBETWEEN`,
`OFFSET` and `INDIRECT` can produce a different answer on the next run through no change to
the workbook. If a test's outcome depends on one, say so in `process` — a `PASS` that
won't reproduce tomorrow should not look identical to one that will.

**Dates are serial numbers, and some dates are text.** A column that mixes real dates with
text dates will sort and compare wrongly while looking fine. Check the type before testing a
date range. Regional day/month order compounds this on imported data.

## Coverage

**Sample honestly, and sample well.** When a range is too large to read entirely, the top rows
are the least representative part of it — they are usually the ones that were checked by hand.
Read the head, the tail, the rows around any structural break, and a spread through the
middle. Then state the sample size and where it came from.

Never phrase partial coverage as complete. "Checked 400 of 41,880 rows, spread across the
range" is a useful finding. "Checked the detail rows" implies something you didn't do, and the
person reading the column has no way to tell.

**Boundary rows are where the defects are.** The first data row, the last data row, and the
row immediately after a subtotal are where off-by-one errors in ranges show up. If you are
sampling anyway, spend the sample there.
