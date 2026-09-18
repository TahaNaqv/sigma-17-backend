# Reply — the OS extract, monthly valuations, and the decisions taken

Following your note of 15 September. One thing to check at your end, one offer we would like to
accept, and three items closed.

---

## 1. The file we received carries four valuation dates, not sixteen

This is worth settling first, because the quarterly reported triangle turns entirely on it.

You said the OS sheet holds an `As at` for each of the sixteen quarter-end closings. The file we
were given does not. Checked on the exact artefact:

* `Claim OS Full.xlsx` — one sheet (`OS`), 32,044 rows;
* its `As at` column holds **four distinct values: 31-12-2021, 31-12-2022, 31-12-2023 and
  31-12-2024**.

It is the same file the system received: 2,223,077 bytes against the 2.2 MB recorded on the run,
and 32,044 rows against that run's own preflight count of 32,044.

You can confirm it in a few seconds — open the file and put a filter on the `As at` column; the
drop-down will list four dates.

We think the likely explanation is that your source system does hold all sixteen closings and the
export that produced this file selected year-ends only. If the export can be re-run to include
every quarter end, that alone switches the quarterly reported triangle on.

## 2. Yes please to monthly outstanding — it is the better answer

> "we can bring all the monthly OS in the original uploaded data"

That supersedes everything we asked for. Month-end valuations support the reported triangle at
**monthly, quarterly and yearly** in one go: the system reads whatever valuation dates the extract
carries and enables each grain accordingly, so nothing needs configuring and nothing needs
deploying. Please send it in the same file and column layout you already use — only the `As at`
values change.

## 3. `LIC (OS) Summary` — you are right, and we withdraw the suggestion

> "this sheet has a separate purpose ... it produces all the claims prior to UW 2018 to club in
> UW 2018, while OS triangles discard using the experience start date"

Understood, and the distinction matters more than our suggestion did: that sheet is on an
underwriting-year basis with everything before UW 2018 clubbed into it, whereas the triangles
discard by experience start date. Building a triangle from it would mix the two bases. We have
withdrawn the idea; please disregard "Route B" in our previous note.

## 4. Health Insurance — done

> "we have not uploaded the claims paid/OS data, so let it leave and just run the overall process
> without it"

Implemented. A run can now be told to leave a reserving class out entirely, and the class is
dropped from premium and both claims files together, so it no longer earns premium into the LRC
while carrying no claims liability. It is matched on the class name as you see it in the output,
and is case- and spacing-insensitive, so it cannot be defeated by a spelling. Nothing changes on
runs that do not use it.

## 5. ULAE % — noted

Understood that it is intentionally empty; we will not flag it again.

Separately, and only if it is not also intentional: in the same `ULAE-RA` sheet the `RA %` column
is filled for four rows — Fire 0.504, Marine 0.551, Misc 0.946, Motor 0.456, all **GROSS** — and
blank for the other six, which are **every RI row** plus Health Insurance.

The consequence is worth stating because it is not obvious from the sheet: Module 2 joins that
table on reserving class **and** GROSS/RI and treats a blank as zero, so a blank RI row is not
inherited from its gross counterpart — it books **a risk adjustment of zero on the reinsurance
side**, for every class. Health Insurance no longer matters now that the class is excluded.

If that is deliberate, say so and we will stop raising it. We are asking only because it is a
position rather than a default, and nothing in the file distinguishes "intentionally nil" from
"not filled in yet".
