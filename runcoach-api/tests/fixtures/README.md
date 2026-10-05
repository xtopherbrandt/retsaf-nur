# FIT fixture provenance

One row per `*.fit` file in this directory, saying what each file really is. File names are
historical and are not evidence: read the row, never the name. The rows were confirmed by the user
on 2026-10-04 and come from the decoded files (F014 reference `spec/references/F014-fixture-provenance.md`,
section 1, in the Shipyard data dir).

`tests/test_fixture_provenance.py` keeps the table honest. It fails and names the file when a
fixture has no row, when a row names a missing file, when `kind` or `run proof` is outside its set,
and when `positions` or `devices` disagrees with the decoded file.

## Columns

- **file**: the fixture's name without `.fit`.
- **kind**: one of `real run`, `real walk`, `HRV capture`, `health snapshot`.
- **notes**: qualifiers, including where a name misleads.
- **mode**: the recording interval as the watch wrote it, and any pause.
- **devices**: the creator (from `file_id`), then after the semicolon each ANT+ sensor in
  `device_info` whose manufacturer is known, or `none`. The watch's internal entries (products
  3709, 3799, gnss, the sensor hub) are left out.
- **positions**: `yes` when any position value is present. A position value is any field named
  `*_lat`/`*_long` in any message, or lap fields 27-30 (the lap bounding box, which fitdecode
  leaves unnamed).
- **run proof**: one of `yes`, `walk only`, `no`. It says whether a spec, reference, docstring or
  test docstring may cite the file as evidence of behaviour on real running data. `yes` means a real
  run; `walk only` means it may be cited for walking only; `no` means it is a capture or a snapshot,
  which may be cited for what it is and never as a run. The learnings rule
  `.claude/rules/learnings/cite-only-real-activity-fixtures-as-proof.md` reads this column.

## Rows

| file | kind | notes | mode | devices | positions | run proof |
|---|---|---|---|---|---|---|
| sample_run | real run | | 1 Hz | FR945 LTE; HRM-Pro Plus | yes | yes |
| dev_fields_run | real run | | 1 Hz | FR955; Polar HR strap, Stryd | yes | yes |
| wrist_ppg_run | real run | HR from the chest strap (user, 2026-10-04); the name is historical | 1 Hz, 81 s pause | FR945 LTE; HRM-Pro Plus | yes | yes |
| strap_run_hrv | real run | strap HRV | 1 Hz, 229 s pause | FR945 LTE; HRM-Pro Plus | yes | yes |
| hilly_run_8k_fr945 | real run | was `Hilly_Smart_Recorded_run.fit`; Smart selected, watch wrote 1 Hz; positions stripped | 1 Hz, 82 s auto-pause | FR945 LTE; HRM-Pro Plus | no | yes |
| hilly_long_run_17k_fr945 | real run | was `Hilly_Smart_Recorded_Run_2.fit`; Smart selected, watch wrote 1 Hz; positions stripped | 1 Hz | FR945 LTE; Dynastream OEM axh01 HR | no | yes |
| strap_cool_down_walk | real walk | Run profile | 1 Hz | FR945 LTE; HRM-Pro Plus | yes | walk only |
| strap_hrv_sample_run | HRV capture | resting, recorded on the Run profile; 0 cadence throughout; its 108 m is GPS acquisition while still | 1 Hz | FR945 LTE; HRM-Pro Plus, Garmin product 21 | yes | no |
| strap_hrv_capture | HRV capture | HRV Snapshot profile | 6 s steps | FR945 LTE; HRM-Pro Plus, Garmin product 21 | no | no |
| wrist_ppg_hrv_snapshot | HRV capture | HRV Snapshot profile, wrist | 6 s steps | FR945 LTE; none | yes | no |
| strap_health_snapshot | health snapshot | | 1 Hz | FR945 LTE; HRM-Pro Plus | no | no |
| strap_health_snapshot_hrv | health snapshot | | 1 Hz | FR945 LTE; HRM-Pro Plus, Garmin product 21 | no | no |
| sample_health_snapshot | health snapshot | | 1 Hz | FR945 LTE; none | no | no |
