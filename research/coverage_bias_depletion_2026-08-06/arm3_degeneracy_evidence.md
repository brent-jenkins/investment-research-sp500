# Arm 3 degeneracy — evidence

Arm 3 ("realistic depletion": remove usable candidates whose S&P 500 membership
has ended as of today) removed **0 of 452/474/500 usable candidates** in blocks
13, 14, and 15 respectively -- byte-identical to Arm 1.

This is not a script bug -- confirmed directly. Example: `AAL` (American
Airlines). Real `universe_membership` window: `2015-03-23` to `2024-07-08` --
a genuine S&P 500 constituent for the entire duration of block 13
(2022-11-30 -> 2024-02-20). Yet `price_data` holds **zero rows** for `AAL` in
this database. Same pattern confirmed for `BK` (member through 2026-05-07,
zero price rows), `CAG` (member through 2026-06-29, zero price rows), `ANSS`,
`BBWI`, `BIO` -- all genuine, real members throughout blocks 13-15's windows,
all with literally no ingested price history.

Mechanism: this database has NOT had the Task A ingestion fix applied
(Task A's fix lives in a separate, not-yet-merged worktree/branch). Under the
current, unpatched `get_all_watchlist_entries()`, a ticker is only ingested
while it is a member as of *today* (real-world today) or is a currently-held
position. AAL/BK/CAG/etc. left the index (or, for BK/CAG, are about to) close
to today's date -- by the time this database's ingestion pipeline last had a
chance to backfill them, they were either already gone from the live
candidate pool or about to be, so they were never ingested at all despite
being real members throughout blocks 13-15.

Consequence for Arm 3: the tickers that would qualify for "realistic
depletion" (real members whose membership has since ended) are the SAME
population the "usable" filter has already excluded from the candidate pool
-- for the same root-cause reason. Arm 3, as specified, operates only within
`usable`, so there is nothing left in that population to remove for these
three, most-recent blocks. This is itself direct, additional evidence for
Task A: the ingestion gap already reaches into blocks 13-15's own baseline
before this experiment ever runs a depletion arm.
