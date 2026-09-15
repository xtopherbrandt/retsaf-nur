# Section 2 — Canonical Data Schema and Ingestion

*Build-ready specification, Phase 2. This section defines the layer directly downstream of the inputs: the vendor-neutral schema every raw stream is normalized into, the concrete Garmin FIT adapter that populates it first, the quarantine that keeps vendor-derived black-box numbers out of the decision path, the data-quality gates that decide which signals are trustworthy enough to compute on, the ingestion of the goal-race course geometry that Section 1 deferred here, and the abstraction that lets other wearables be added later without redesign. It conforms to `research/00` as the decision authority — in particular the raw-over-derived principle (`research/00` §1.5), the vendor-neutral-canonical-schema keystone (`research/00` Part 2, finding 6), the post-session-loop keystone (`research/00` Part 2, finding 1), the data-quality-gating register row and the resting-HRV source tiering (`research/00` Part 3, §3.3) — and cites `research/02` for the device-level evidence behind every field, mapping, accuracy claim, and access route it states. Per the project conventions, rules and formulas are stated here in full; the evidence and derivations behind them live in the cited docs and are not re-argued.*

---

## 2.1 The design commitment this section enforces

Two architectural rulings from `research/00` decide the entire shape of this section, and every rule below is downstream of them.

**Raw over derived.** The state model and every metric the system computes are built **only** from raw measured signals — heart rate, RR intervals, pace/GPS, cadence, barometric altitude, running dynamics, and power where present (`research/00` §1.5). Vendor-derived black-box numbers (Garmin/Firstbeat VO₂max, Training Status, Training Readiness, Body Battery, Performance Condition, the HRV Status classification) are inferences from proprietary, undocumented, firmware-dependent, and largely un-validated algorithms (`research/02` §1.2, §4); they are ingested into a **quarantined namespace** (§2.3.6), never feed a decision, and are retained only for optional corroboration and for showing the athlete a familiar number. The most important job of the ingestion layer is therefore a *separation*: it must keep the physical quantities a sensor actually transduces distinct from the numbers a model inferred from them (`research/02` §1), and it must let only the former reach the coaching engine. One deliberately-drawn boundary case: a device's **numeric** resting rMSSD (a standard statistic in known units, §2.4.5) is admitted as an HRV input at reduced confidence, unlike the HRV Status *classification* built on top of it, which stays quarantined (`research/00` §1.5, §3.3).

**One vendor-neutral canonical schema, adapters map into it.** Ingestion normalizes every data source into a single timestamped raw-stream schema (§2.1.2–§2.1.4). A Garmin FIT adapter maps into that schema first and is specified here in full (§2.2); adapters for other vendors are added later without touching anything downstream (`research/00` Part 2, finding 6; `research/02` §7). The schema is built around the raw signals that are **standardized across vendors** — the same signals exposed by open ANT+ and BLE GATT profiles rather than by any proprietary format — because those are exactly the signals every wearable can supply (`research/02` §7). Nothing downstream of ingestion ever sees a vendor's native format; it sees only the canonical schema.

A third ruling scopes what the ingestion layer must deliver in time. Genuine in-session adaptation is not available on the primary Garmin data path — the official APIs deliver data only after an activity finishes and syncs (`research/00` Part 2, finding 1; `research/02` §6) — so the ingestion layer is designed for **completed sessions arriving after the fact**, and the real-time on-device path is an optional module (§2.7.4) that feeds the same canonical schema when present. This is not a limitation this section works around; it is the operating assumption the schema is built for.

---

## 2.2 The canonical raw-stream schema

The canonical schema is the vendor-neutral representation of one recorded training session. It is organized as four nested objects: a **session** header, a per-sample **record stream**, a beat-to-beat **RR stream**, and a **context** object carrying environmental and provenance metadata. Raw measured fields come first and are the substrate for everything the system computes; nothing derived by a vendor appears anywhere in this schema (derived numbers live only in the sidecar of §2.3.6).

### 2.2.1 The session object

One session object per recorded activity. It carries identity, timing, the source provenance the quality gates and adapters need, and roll-up summaries — but the summaries are conveniences for display and indexing only; **every metric the coaching engine computes is derived from the record and RR streams, never from these roll-ups** (a rule that matters because a vendor's session-level averages can silently fold in its own corrections).

| Field | Type | Units / format | Notes |
|---|---|---|---|
| `session_id` | identifier | opaque string, stable | Unique per activity; the key the decision log and state model reference. |
| `athlete_id` | identifier | opaque string | Links to the athlete input (§1.3). |
| `start_time` | timestamp | UTC, ISO-8601 | Session start; all record timestamps are offsets from a common epoch, stored absolute. |
| `sport` | enum | running / other | Non-running activities are ingested but flagged; the running models apply only to running. |
| `activity_tag` | enum / string | e.g. `race`, `workout`, `easy`, `long_run`, `resting_hrv_check`, `health_snapshot` | Athlete- or system-assigned role. `resting_hrv_check` is a chest-strap morning-HRV capture and `health_snapshot` a Garmin Health Snapshot reading (§2.4.5); both are routed to the resting-HRV path rather than treated as training sessions. |
| `source_vendor` | enum | e.g. `garmin` | Which adapter produced this object. Recorded so quality gates and later audits know the provenance. |
| `source_device` | string | model + firmware where available | Device identity; firmware is recorded because vendor-derived metrics (§2.3.6) and the numeric resting-HRV pipeline (§2.4.5) are firmware-dependent and change without notice (`research/02` §4), so any corroboration or source-tier baseline must know the firmware it came from. |
| `recording_interval` | descriptor | detected sampling mode | `1hz` / `smart` / `irregular`, set by the recording-mode detector (§2.4.1). Gates whether uniform-sampling metrics may run. |
| `hr_source` | enum | `chest_strap` / `wrist_ppg` / `unknown` | Which HR sensor produced this session's HR/RR, inferred per §2.4.2. Gates HRV and at/above-threshold HR metrics. |
| `quality_flags` | list | see §2.4 | Every gate result raised on this session, carried with it so downstream consumers can down-weight rather than the ingester silently dropping data. |
| `summary` | object | totals/averages/maxima | Distance, duration, average/max HR and pace, elevation gain — for display and indexing only, never a metric input. |

### 2.2.2 The record stream

The record stream is the heart of the schema: a time-ordered series of samples, one per recording instant, each a fixed set of raw fields. This is the time series every load, decoupling, threshold, and grade-adjusted-pace computation reads. The system **requires 1 Hz (every-second) sampling** for this stream (§2.4.1); irregularly-sampled sessions are flagged and either resampled or excluded from uniform-sampling metrics, because trapezoidal load integration, HR/pace decoupling, and cadence statistics all degrade under non-uniform spacing (`research/02` §2.5; `research/00` Part 3 data-quality-gating row).

Each record sample carries the following fields. A field absent from a given source is left null and flagged unavailable rather than imputed; a metric that needs a null field is reported unavailable rather than computed on a guessed value.

| Field | Type | Canonical units | Notes |
|---|---|---|---|
| `t` | number | seconds since `start_time` | Sample time. Uniform 1 s spacing when `recording_interval = 1hz`. |
| `lat`, `lon` | number | decimal degrees (WGS-84) | Position. Converted from the source's native encoding by the adapter (§2.3.3 for FIT semicircles). |
| `distance` | number | metres, cumulative | Cumulative distance along the track. |
| `speed` | number | metres per second, instantaneous | Pace is `1 / speed`. Treated as reliable only when averaged over a lap/interval, not sample-by-sample (§2.4.4). |
| `heart_rate` | number | beats per minute | 1 Hz. Trustworthiness depends on `hr_source` (§2.4.2). |
| `cadence` | number | steps per minute | A direct kinematic (accelerometer) measurement; the adapter folds any fractional/×2 convention into spm (§2.3.3). |
| `altitude` | number | metres, barometric where available | Barometric altitude preferred over GPS vertical; smoothed before grade is taken (§2.4.4). |
| `power` | number | watts | Present only on power-capable setups. **Modeled, not measured**, and model-specific — carries `power_model` provenance and never anchors cross-athlete or cross-device physiology (§2.4.4; `research/02` §3.5). |
| `vertical_oscillation` | number | millimetres | Running dynamics; lightly modeled from accelerometer data (`research/02` §1.1). |
| `ground_contact_time` | number | milliseconds | Running dynamics. |
| `gct_balance` | number | percent | Left/right ground-contact balance. |
| `step_length` | number | millimetres | Running dynamics. |
| `temperature` | number | °C | Device thermistor — reflects device/skin temperature, **not reliable ambient air temperature** during a run (`research/02` §1.1); not used as the environmental heat input (that comes from the race/session context, §2.2.4). |
| `sample_quality` | list | per-sample flags | Optional per-sample quality markers (e.g. GPS dropout, PPG artefact window) so downstream metrics can mask individual samples. |

### 2.2.3 The RR-interval stream

Beat-to-beat (RR) intervals are the single most valuable raw stream for the system's readiness logic and the most awkward to obtain correctly, so they are a first-class stream of their own rather than a record field. An RR interval is the time in milliseconds between successive R-waves — the raw substrate for heart-rate variability (`research/02` §1.1, §2.3). This stream also carries the **numeric resting-HRV path** the source tiering of `research/00` §3.3 introduces: for the higher tiers a raw RR series is present and the system computes rMSSD itself, while for the numeric wrist tiers a device-computed resting rMSSD arrives as a scalar with no beats, so the stream carries fields for both forms.

| Field | Type | Canonical units | Notes |
|---|---|---|---|
| `rr` | array of number | milliseconds | The full reconstructed series of successive beat-to-beat intervals for the session (§2.3.4 gives FIT reconstruction). Present for a chest-strap source; null for a numeric-only wrist tier. |
| `rr_source` | enum | `chest_strap_ecg` / `overnight_ppg` / `health_snapshot_ppg` / `other` | Provenance of the RR/HRV data. `chest_strap_ecg` (or a validated overnight resting RR source) carries raw beats and is the highest-fidelity tier; `overnight_ppg` and `health_snapshot_ppg` carry a device-computed resting rMSSD, admitted at reduced confidence (§2.4.5). |
| `rmssd_precomputed` | number | milliseconds | A **device-computed resting rMSSD** supplied without raw beats — Health Snapshot's `RmssdAvgValue` or the Health API HRV Summary's `lastNightAvg` (§2.4.5). Populated only for the numeric wrist tiers (`rr` null); it bypasses the artefact filter of §2.4.3 (there are no beats to filter). **Device-only audit record**: the §3.7 trend never reads it — it reads `resting_rmssd_ms` below (amended 2026-09-09, F005). |
| `resting_rmssd_ms` | number | milliseconds | The **resolved resting rMSSD the §3.7 trend reads, on every tier** (introduced by the 2026-09-06 resting-HRV capture amendment; spec-recorded 2026-09-09, F005): on the chest-strap tier the rMSSD the system computed from the artefact-filtered beats (§2.4.3, §3.7.2); on the numeric tiers the device value copied from `rmssd_precomputed`. Null for any session that is not a resting-HRV reading, null when a quality gate refused the capture, and null on reading rows stored before that amendment (no backfill) — the trend excludes such rows explicitly rather than assuming the value is safe to take `ln` of. |
| `hrv_source_tier` | enum | `chest_strap_raw` / `health_snapshot` / `health_api_overnight` | Which tier of the resting-HRV hierarchy (`research/00` §3.3) produced this reading. The HRV-trend logic reads this to apply the tier's confidence weight and to keep the baseline/SWC band from mixing tiers (§2.4.5; spec §3.7). |
| `rr_artefacts` | array | index + correction applied | The artefact-detection/correction record (§2.4.3): every RR value flagged as physiologically impossible and how it was handled, retained for auditability. Empty for a numeric-only wrist tier. |
| `rr_valid_fraction` | number | 0–1 | Fraction of the raw series surviving artefact filtering; a quality weight on any HRV figure derived from it. Not applicable to a numeric-only wrist tier (whose confidence is set by its tier weight instead). |

Two rules govern this stream, both from the data-quality register and the source tiering (`research/00` Part 3, §3.3; `research/02` §2.3, §3.1–3.2, §4.1):

- **In-activity RR comes only from a chest strap, and in-activity HRV is never computed.** Wrist PPG does not produce reliable beat-to-beat data *during running*; the presence of an RR stream in a training activity is itself the signature that a chest strap was worn (§2.4.2). During intense running, respiration and motion make beat-to-beat HRV largely uninterpretable (`research/02` §3.2), so the RR stream from a run is retained only to corroborate that a chest strap was present and to support artefact statistics — **the HRV trend the readiness logic consumes is never built from intra-workout RR, and never from in-run wrist PPG.**
- **Resting HRV is sourced by tier, not by a single mandatory device.** The trend is built from a *resting-state* reading — `resting_rmssd_ms`, resolved on every tier (§2.2.3) — preferring a chest-strap raw RR capture the system reduces itself, and falling back, at reduced confidence, to a device-computed numeric resting rMSSD when a strap is not used (§2.4.5); on those tiers the device value is kept in `rmssd_precomputed` as the audit record and resolved into `resting_rmssd_ms` unchanged. This is the standing-at-rest case where the motion failure modes above are absent (`research/02` §4.1), which is what makes the numeric wrist tiers admissible; it does not relax the in-run prohibition in the first rule.

### 2.2.4 The context object

The context object carries the environmental and situational metadata a session was recorded under, plus the provenance that lets later sections trust or discount it. It is distinct from the goal-race `expected_conditions` of §1.2.2 (a forecast for a future race); this is the *actual* environment of a completed session, where known.

| Field | Type | Units | Notes |
|---|---|---|---|
| `env_temperature_c` | number | °C, ambient | Ambient air temperature, from an external/weather source keyed to the session's time and location — **not** the device thermistor (`record.temperature`), which is unreliable as ambient (`research/02` §1.1). Null when no external source is available. |
| `env_humidity_pct` | number | 0–100 | Ambient relative humidity, same sourcing. |
| `env_wind_ms`, `env_wind_dir` | number, bearing/enum | m/s, direction | Ambient wind where available; feeds the same environmental-cost models used for the pace target (§1.4.4) when normalizing a session's pace. |
| `subjective` | object | `research/03` instruments | Any self-report attached to this session (session-RPE, wellness, soreness/pain map). First-class input (`research/00` Part 2, finding 7); the schema carries it, Section 6 interprets it. |
| `ingested_at` | timestamp | UTC | When the ingestion layer processed this session; distinguishes bulk backfill from incremental arrival (§2.6.1). |
| `provenance` | object | route + adapter version | Which access route (§2.7) and adapter version produced this object, for auditability and reprocessing. |

---

## 2.3 The Garmin FIT adapter

The FIT adapter is the first concrete mapping from a vendor format into the canonical schema, and the reference every later adapter is written against. FIT ("Flexible and Interoperable Data Transfer") is Garmin's binary container format, defined by the official FIT SDK (`research/02` §2). The adapter's contract is: consume a `.fit` file, emit exactly one canonical session object (§2.2), and place nothing vendor-derived into it (vendor-derived numbers go to the sidecar of §2.3.6).

### 2.3.1 Decoding the FIT container

A FIT file is a header (protocol/profile version, data size, `.FIT` signature), a body of interleaved records, and a trailing CRC (`research/02` §2.1). The body alternates two record kinds:

- **Definition messages** declare the schema of the data messages that follow — their global message number, which fields are present, and each field's size and base type, with an architecture byte for endianness.
- **Data messages** carry packed integer values in the layout the preceding definition declared. The global FIT profile (shipped as `Profile.xlsx` in the SDK) supplies each field's scale, offset, and units, so the decoder recovers engineering units as **`physical = raw / scale − offset`** (`research/02` §2.1).

The adapter is built on the **official Garmin FIT SDK profile** for decoding, so field semantics track the authoritative definitions rather than a hand-maintained table (`research/02` §5.2). Robust open parsers exist in multiple languages (the official SDK; community libraries such as Python `fitparse`/`fitdecode`) and any may implement the decode, but the field scale/offset/units must come from the SDK profile.

### 2.3.2 Message-to-object mapping

The running-relevant FIT global messages map to the canonical schema as follows (`research/02` §2.1–§2.2):

| FIT message (global #) | Canonical target | Notes |
|---|---|---|
| `file_id`, `device_info` | `session.source_device`, provenance | Device model + firmware (message identifiers resolved via the SDK profile, §2.3.1). |
| `session` (#18) | `session` header + `summary` | Roll-up only; not a metric source (§2.2.1). |
| `lap` (#19) | lap boundaries within the record stream | Used for lap/interval averaging of pace (§2.4.4). |
| `record` (#20) | the record stream (§2.2.2) | One FIT `record` message → one canonical sample. Field mapping in §2.3.3. |
| `event` (#21) | timer events; sometimes RR/beat data | Checked as one of the possible RR carriers (§2.3.4). |
| `hrv` (#78) | the RR stream (§2.2.3) | Primary RR carrier; reconstruction in §2.3.4. |
| `hr` (#132) | RR/beat-timing corroboration | Secondary beat-timing source where present. |
| `developer_data_id`, `field_description` | developer-field interpretation | Read first so developer fields can be decoded (§2.3.5). |

### 2.3.3 Record-field mapping and unit conversions

The FIT `record` message (#20) is where the raw streams live. The adapter maps and converts (`research/02` §2.2):

- `position_lat` / `position_long` (semicircles) → `lat` / `lon` (degrees): **`degrees = semicircles × (180 / 2³¹)`**.
- `enhanced_speed` / `speed` (m/s, FIT scale 1000) → `speed` (m/s). `enhanced_speed` preferred where present (wider range).
- `enhanced_altitude` / `altitude` (m, FIT scale 5, offset 500) → `altitude`. **`enhanced_altitude` (barometric) is preferred** over GPS-derived vertical wherever present (`research/02` §3.4).
- `distance` (FIT scale 100 → cm precision) → `distance` (m, cumulative).
- `heart_rate` (bpm) → `heart_rate`.
- `cadence` + `fractional_cadence` (rpm) → `cadence` (spm): running cadence is `(cadence + fractional_cadence) × 2` (`research/02` §2.2).
- `vertical_oscillation`, `stance_time`, `stance_time_percent`, `step_length` → `vertical_oscillation`, `ground_contact_time`, `gct_balance`, `step_length` (note the running-dynamics fields are frequently carried as **developer fields**, not native record fields — see §2.3.5).
- `power` (W) → `power`, tagged with its `power_model` (§2.4.4).
- `temperature` (°C) → `record.temperature`, retained but **not** used as the ambient/heat input (§2.2.4).

### 2.3.4 Reconstructing the RR series

Beat-to-beat data is stored in the dedicated **`hrv` message (#78)**, whose single field `time` is an *array* of successive RR intervals in seconds (FIT scale 1000 → millisecond resolution), packed several to a message with a sentinel/invalid value in unused array slots (`research/02` §2.3). Reconstruction:

1. Read every `hrv` (#78) message in file order and **concatenate its `time` arrays** into one continuous series, discarding sentinel/invalid slots.
2. Convert each interval to milliseconds (`× 1000` from FIT seconds) and emit as `rr` (§2.2.3).
3. Because some newer devices/firmware place beat-to-beat data in `event` (#21) or in developer fields rather than in message #78, a robust parser **checks all three carriers** and merges what it finds, recording which carrier supplied the series in `rr_source` provenance (`research/02` §2.3).
4. Set `rr_source = chest_strap_ecg` and `hrv_source_tier = chest_strap_raw` when a raw RR stream is present in an activity file (RR presence during activity is the chest-strap signature, §2.4.2); pass the series to artefact filtering (§2.4.3) before any HRV use.

Note the boundary this closes, restated for the source tiering of `research/00` §3.3. Garmin's overnight **HRV Status *classification*** (Balanced/Unbalanced/Low) is a vendor-derived composite and is **quarantined** (§2.3.6) — never a trend input. But the **numeric** overnight rMSSD underneath it (`lastNightAvg`, ms) is a standard statistic and *is* obtainable — via the Health API HRV Summary (§2.7.1) — and a numeric resting rMSSD is likewise produced by the on-demand **Health Snapshot** activity, which lands in an ordinary FIT file (`research/02` §4.1). These numeric readings do not populate the raw `rr` array; the adapter writes them to `rmssd_precomputed` with the corresponding `rr_source`/`hrv_source_tier` (§2.2.3), and the morning-capture protocol of §2.4.5 governs how the readiness logic consumes them.

### 2.3.5 Developer-field handling

FIT V2 added **developer data fields**: a `developer_data_id` message registers an application UUID, `field_description` messages self-describe each custom field (name, base type, units, and which native field it augments), and those fields then ride inside ordinary `record`/`lap`/`session` messages (`research/02` §2.4). Because their meaning is declared in-file rather than fixed by the global profile, the adapter must **read the `field_description` messages first**, build the field map, and only then decode the developer fields that reference them. This is the mechanism through which Connect IQ apps and third-party sensors (e.g. Stryd) inject streams — most importantly, running dynamics and running power are frequently delivered this way rather than as native record fields (`research/02` §2.2, §2.4). A developer field the adapter cannot resolve to a canonical field is preserved verbatim in provenance, never guessed into a canonical slot.

### 2.3.6 Vendor-derived metrics: quarantine, not schema

Everything Garmin *infers* — VO₂max estimate, Training Status, Training Load/acute-chronic load, Training Effect, Training Readiness, the **HRV Status classification**, Body Battery, Performance Condition, Race Predictor, recovery time, stress score — is a computed inference from proprietary Firstbeat-lineage algorithms, undocumented in shipped form and firmware-dependent (`research/02` §1.2, §4). None enters the canonical schema. When such a value is present in the FIT file or is available from an API, the adapter writes it to a **quarantined vendor-derived sidecar** keyed to the session:

- The sidecar is a clearly-labeled namespace **the coaching logic ignores by default** (`research/00` §1.5, Part 2 finding 6; `research/02` §7).
- Its only sanctioned uses are (a) optional **corroboration** — e.g. flag when the system's own computed VO₂max trend diverges materially from Garmin's, surfaced for review, never acted on automatically — and (b) showing the athlete a **familiar number** alongside the system's own.
- Every sidecar value carries a "vendor-derived, not a decision input" marker so no later code path can mistake it for a raw signal. The surfacing of any sidecar-vs-own divergence to the athlete is owned by Section 9 (explainability), which this section only supplies the data for.

Note the boundary with §2.4.5: the HRV Status *classification* is quarantined here, but a device's *numeric* resting rMSSD is **not** a quarantined composite — it is a standard statistic admitted (at reduced confidence) as a genuine HRV input via `rmssd_precomputed` (`research/00` §1.5, §3.3). The two must not be conflated: the label is a black box, the number is not.

---

## 2.4 Data-quality gating

Raw signals vary in trustworthiness by sensor, intensity, and recording mode, and a metric is only as good as the signal under it. The gates below implement the data-quality-gating register row (`research/00` Part 3) and the accuracy findings of `research/02` §3. The governing posture is **flag-and-down-weight, not silently drop**: the ingester records a quality verdict on every session and stream, and passes it downstream so each metric can decide how much to trust or how to degrade — the ingester discards data only when it is unusable for every purpose. Every gate result lands in `session.quality_flags` (or per-sample `sample_quality`).

### 2.4.1 Recording-mode detection (the 1 Hz requirement)

Garmin watches record either **every-second (1 Hz)** — every stream sampled once per second — or **smart**, which records only when values change "significantly," producing irregularly-spaced samples (`research/02` §2.5). The system **requires 1 Hz** because smart recording corrupts every metric that assumes uniform sampling: trapezoidal load integration, HR/pace decoupling, and cadence statistics all degrade (`research/00` Part 3; `research/02` §2.5). The gate:

1. Inspect the `timestamp` deltas across the record stream. Uniform ≈1 s spacing → `recording_interval = 1hz`. Predominantly larger, irregular gaps → `smart`/`irregular`.
2. On non-1 Hz, raise a `smart_recording` flag and **resample to a uniform 1 s grid** so uniform-sampling metrics can still run on a marked, lower-confidence basis. Default rule: linearly interpolate across gaps of **≤ 5 s**; for any gap **> 5 s**, mark the affected span `interpolation_gap` and **exclude it** from uniform-sampling metrics (load integration, decoupling) rather than fabricating samples across it. The 5 s threshold is a spec-introduced implementation default, tunable, not an open scientific question.
3. Surface the requirement to the athlete as setup guidance: enable 1-second recording (`research/02` §2.5).

### 2.4.2 HR-source inference and PPG down-weighting

Chest-strap ECG is the practical gold standard; wrist PPG is adequate at rest and low intensity but degrades sharply during hard, dynamic running — motion artefact, "cadence lock" (the optical signal tracking footstrike frequency instead of pulse), and lag at intensity transitions, with no wrist device holding acceptable concordance at fast running speeds (`research/02` §3.1). Note this is a *motion*-driven failure: at rest the same sensor is materially more trustworthy, which is the basis for the resting-HRV numeric tiers (§2.4.5; `research/02` §4.1). The gate:

1. **Infer `hr_source`.** Presence of an RR stream (§2.2.3) in an activity is the chest-strap signature → `chest_strap`. Absence, with HR present → `wrist_ppg`. Record the inference; it is provenance every HR metric reads.
2. **Gate the sensitive metrics.** Metrics that require timing/upper-range fidelity — in-activity HRV (never computed at all, §2.2.3), and any **at-or-above-threshold HR** metric (threshold estimation, interval HR, HR-based load on hard sessions) — are computed **only** from chest-strap HR (`research/00` Part 3; `research/02` §3.1). On a wrist-only session these are down-weighted and flagged.
3. **Cadence-lock check.** On a wrist-only session, where reported HR stays within **±3 bpm of step rate (cadence in spm)** for a sustained window of **≥ 30 s**, flag the affected samples (`cadence_lock`) so decoupling and load logic can mask them. The ±3 bpm / 30 s bounds are spec-introduced implementation defaults, tunable.

Easy-run aggregate HR from wrist PPG remains usable (down-weighted); the gate narrows *which* metrics may consume a given HR source, it does not throw the session away.

### 2.4.3 RR artefact filtering

Even good chest-strap RR series contain occasional artefacts — missed or doubled beats — and HRV is meaningless until they are removed (`research/02` §3.2). Before any HRV computation on a **raw RR series** the system runs artefact detection/correction. Default rule: flag any RR interval falling outside the physiologically plausible band **300–2000 ms** (≈ 30–200 bpm), or differing from the local median of its surrounding intervals by **more than 20%** (a standard Kubios/Plews-style relative-jump criterion; the readiness literature the register points to for HRV math is Plews/Altini/Kubios, `research/00` Part 4). Flagged intervals are corrected or excised, each action recorded in `rr_artefacts` with the surviving-fraction weight `rr_valid_fraction` (§2.2.3). The 20% and 300–2000 ms bounds are the shipped defaults, tunable per athlete. An HRV figure from a low-valid-fraction series is carried at reduced confidence rather than presented as clean. Only after this filtering are rMSSD/SDNN or any frequency-domain measure computed (and, per §2.2.3, only on resting/overnight series, never intra-workout). This gate applies to the raw-RR tier only; a numeric reading on Tiers 2–3 (§2.4.5) has no beats to filter — its device value is stored in `rmssd_precomputed` and resolved into `resting_rmssd_ms` as-is — and its reduced confidence is a property of the tier, applied at Section 6's readiness fusion rather than by this gate or by the trend (spec §3.7.4).

### 2.4.4 GPS/GNSS pace and barometric-altitude quality

**Pace.** GNSS gives good *cumulative distance* (typically within a few percent under open sky) but noisy *instantaneous* pace, because instantaneous speed differentiates a position signal with metre-level jitter; watches apply undocumented smoothing that introduces lag; and tree cover, urban canyons, and tunnels cause multipath and dropouts (`research/02` §3.3). The gate's consequence for every downstream consumer: **treat pace as reliable only when averaged over a lap or interval, not sample-by-sample** (`research/02` §3.3). For the success metric — average race pace — aggregate GPS is fine. Multi-band (L1+L5) reception, where the device reports it, reduces multipath error and raises pace-stability confidence. Segments with GPS dropout or degraded accuracy are flagged (`gps_degraded`) at the sample level so within-interval pacing logic can mask them and fall back to lap averages.

**Altitude.** Barometric altitude is substantially more accurate than GPS vertical for *relative* elevation change (GPS vertical error is typically 2–3× its horizontal error), but the barometer drifts with weather as pressure fronts shift the baseline (`research/02` §3.4). The adapter therefore prefers the barometric `enhanced_altitude` stream (§2.3.3), and the gate **smooths it before grade is taken** (raw grade is noisy) and flags spans of suspected barometric drift. Grade-adjusted pace — the universal downstream pace representation (`research/00` Part 2, finding 5) — consumes this smoothed altitude, so getting altitude quality right here is load-bearing for every grade-corrected metric later.

**Power.** Running power is *modeled, with no accepted physiological gold standard to validate it against*, and Garmin-native and Stryd models are not interchangeable (`research/02` §3.5). The gate requires every power sample to carry a `power_model` provenance tag naming which model produced it (Garmin-native, Stryd, or other), and marks power as a *relative* intensity signal usable within one athlete on one device only — never an anchor for cross-athlete or cross-device physiology.

### 2.4.5 Morning / resting HRV capture protocol (tiered by source)

The readiness logic downstream (Section 6, via the HRV-guided gate of `research/00` Part 3) needs a **resting-state HRV trend**. Rather than mandate one acquisition method, the system implements the four-tier resting-HRV source hierarchy resolved in `research/00` §3.3: the chest strap is the *preferred, highest-confidence* source, and the logic degrades gracefully through numeric wrist-derived rMSSD sources at reduced confidence before it treats HRV as unavailable. This replaces the earlier single-protocol capture, which mandated a daily chest-strap reading and thereby put a behavioral barrier in front of the most valuable readiness signal.

**The tiers, highest fidelity first** (`research/00` §3.3; `research/02` §4.1):

1. **Chest-strap resting RR — `hrv_source_tier = chest_strap_raw`.** The athlete records a short **2–5 minute resting measurement with the chest strap connected**, ideally on waking before rising, in a consistent posture and at a consistent time (consistent timing is what makes the trend comparable; `research/02` §3.2). It is ingested as a session tagged `activity_tag = resting_hrv_check`, its raw RR flows through reconstruction (§2.3.4) and artefact filtering (§2.4.3), and `rr_source = chest_strap_ecg`. The system computes rMSSD itself — fully owned, highest confidence.
2. **Health Snapshot — `hrv_source_tier = health_snapshot`.** The athlete runs Garmin **Health Snapshot**, a ~2-minute held-still reading whose `RmssdAvgValue` (a numeric resting rMSSD in ms) rides out in an ordinary FIT file — **no chest strap, no Developer Program** (`research/02` §4.1). It is ingested tagged `activity_tag = health_snapshot`; the adapter writes the value to `rmssd_precomputed` (raw `rr` null) with `rr_source = health_snapshot_ppg`. This is the **preferred short-term default** where a chest strap is not in use.
3. **Health API HRV Summary — `hrv_source_tier = health_api_overnight`.** Where the deployment is enrolled in the Connect Developer Program, the passive overnight `lastNightAvg` (numeric resting rMSSD in ms) is ingested with no daily athlete action, written to `rmssd_precomputed` with `rr_source = overnight_ppg` (§2.7.1; `research/02` §4.1).
4. **HRV Status classification** (Balanced/Unbalanced/Low) is **not** an HRV input — it is a quarantined vendor composite (§2.3.6), retained only for corroboration.

**Confidence and the anti-mixing rule.** The numeric tiers (2–3) are genuine HRV inputs because a numeric rMSSD in known units is a standard statistic, not a black-box composite (`research/00` §1.5) — but they carry a **confidence discount** below the chest-strap tier, reflecting the validation evidence: nocturnal PPG rMSSD agrees well with ECG (r, CCC > 0.90) yet needs longer averaging and shows larger individual error, especially in older adults (Liang et al. 2024, *Sensors*), and resting wrist PPG is reliable overall but weaker for short-window rMSSD than SDNN (Zuern et al. 2026, *Scientific Reports*). The 7-day rolling mean and the SWC band the trend already uses — ±0.5·SD(ln rMSSD), the sample SD of the athlete's own ln rMSSD baseline; the register's "CV-scaled" band as clarified 2026-09-09 (`research/00` §5.4; spec §3.7.3) — supply exactly the longer-averaging smoothing those caveats call for. Critically, **the baseline and SWC band must not mix tiers**: because each source carries its own systematic bias, the trend is built on a consistent source, and when the primary source tier changes (the athlete adopts or drops the strap, or a device/firmware change shifts the overnight pipeline — hence `source_device` firmware is recorded, §2.2.1) the system **re-establishes the baseline** rather than reading the source switch as a physiological HRV shift (spec §3.7).

**What is retained unchanged.** The uncontested prohibition stands: **HRV is never computed from in-run wrist PPG, and in-activity HRV is not computed at all** (§2.2.3). The tiering concerns only *resting-state* acquisition.

**Degradation.** Degradation is decided at **tier level**, not per day (amended 2026-09-10, F005 review; the rule is §3.7.4's as amended 2026-09-09): the trend is built on the highest-fidelity tier that is present densely enough to sustain a baseline ("densely enough" as clarified 2026-09-10 in §3.7.3, T093: at least `min_baseline_readings` distinct local days in the baseline window **and** at least `min_window_readings` in the judged week — days, not captures, since 2026-09-12 (T095) — and, since 2026-09-15 (T117, a behaviour change closing [[IDEA-064]]), also **read recently relative to the other candidates**: its latest baseline-window day within `recency_tolerance_days` (28) of the most recent baseline-window day of any candidate, so an abandoned trial still sitting in the window no longer takes a week it happens to cover; `research/00` §5.4), at that tier's confidence, and when that tier cannot sustain one the logic falls to a numeric resting rMSSD tier (Health Snapshot, then Health API overnight) that can — re-establishing the baseline there only when that is a sustained source change under §3.7.3's rule (the tier now sustaining the baseline differs from the one that sustained the previous 60 days, and the eras do not interleave); a week-driven or empty-week fallback to a tier that also sustained the previous window is not a re-establishment (qualified 2026-09-12, T095, carrying T094's clarification of §3.7.3 into this paragraph, which T087 had re-scoped before that clarification existed). A day whose only capture is off the baseline tier is **not** filled from that capture inside a live baseline — that would mix tiers within one band, which the anti-mixing rule above forbids — and such a reading is corroboration only. Only when **no** resting-HRV reading of any tier can sustain a trend does the HRV input go unavailable — whereupon the readiness logic widens its guardrails and leans harder on the subjective and resting-HR axes, rather than substituting an intra-workout or in-run-PPG value (`research/00` §1.4, Part 2 finding 8). A future validated overnight chest-strap RR feed enters at Tier 1 behind the same `resting_hrv_check` interface without downstream change.

*This protocol discharges the item previously logged for back-port: it is now reconciled into `research/00` §3.3 (resting-HRV source tiering), which amends the register rows accordingly.*

---

## 2.5 Course-geometry ingestion

Section 1 (§1.2.1) defined the goal-race `course_profile` as an already-resolved elevation-versus-distance series and deferred its *ingestion* here. This subsection closes that forward reference. The course profile is what the grade modifier of §1.4.4 integrates over, and it is produced by the same barometric-altitude-quality reasoning as §2.4.4 — a noisy raw elevation track must become a clean, uniformly-distance-indexed series before the Minetti cost-of-gradient curve is applied point-by-point (`research/00` Part 2, finding 5; `research/02` §3.4).

**Accepted sources.** A course profile may arrive as a standard route/track file (GPX, TCX, or a FIT `course`), as an explicit distance/elevation table, or be derived from a prior recorded session over the same course (its `distance` + smoothed barometric `altitude` streams). Position/altitude are universal physical quantities present in every vendor's activity/route file (`research/02` §7), so this path is vendor-neutral like the rest of the schema.

**Resampling and interpolation — the shipped default.** The ingester resamples the source to a **uniform distance grid and requires sample points no sparser than every 50 m**; where the source is sparser, it **linearly interpolates** the intermediate points and marks each synthesized point `interpolated` so the grade integration downstream can down-weight or widen confidence over interpolated spans. Elevation is **smoothed before gradient is computed** (raw grade is noisy; `research/02` §3.4), using the same altitude-smoothing the session gate applies (§2.4.4), so that a training run's grade and the race course's grade are treated consistently. The 50 m / linear-interpolation choice is a spec-introduced default under uncertainty — no research doc specifies a course-resampling resolution — flagged here and logged for register back-port; it is lower-stakes than the environmental-composition and HRV-capture items and does not block use of the section.

**Fallback.** When no course profile is available, the system falls back to a flat profile (zero gradient) and flags the grade modifier as defaulted, exactly as §1.2.1 specified, widening the pace-target confidence band (§1.4.6). Course geometry, once ingested, is the resolved series Section 1's grade modifier consumes; the two now meet cleanly at this interface.

---

## 2.6 The multi-vendor ingestion abstraction

The canonical schema (§2.2) and the adapter contract (§2.3) together are the extensibility mechanism. Adding a vendor means writing one adapter that consumes that vendor's format and emits canonical session objects; **nothing downstream of ingestion changes** (`research/00` Part 2, finding 6; `research/02` §7). The abstraction holds because the schema is built around signals standardized across vendors by open ANT+/BLE GATT profiles, not around Garmin specifics:

- **Heart rate and RR** — ANT+ Heart Rate profile / BLE Heart Rate Service (0x180D), whose measurement characteristic includes the optional RR-interval field. Any compliant strap (Polar, Wahoo, Garmin, Coros-paired) speaks this.
- **Speed/pace and cadence** — ANT+/BLE Running Speed and Cadence Service (0x1814); footpods across vendors implement it.
- **Power** — ANT+/BLE Power profiles (Stryd and others), always carried with its model provenance (§2.4.4).
- **Position/altitude** — not a sensor-profile standard, but universal physical quantities present in every vendor's activity file (FIT/TCX/GPX).

Every adapter obeys the same three invariants: it maps only raw measured signals into the canonical schema; it routes any vendor-derived numbers to the quarantined sidecar (§2.3.6); and it stamps each object with `source_vendor`, `source_device`, and adapter-version provenance (§2.2.4) so quality gates and audits can reason about origin. The resting-HRV tiers generalize the same way: a numeric resting rMSSD from another vendor's resting/overnight PPG enters at the numeric tier via `rmssd_precomputed` with its own `hrv_source_tier`, subject to the same anti-mixing baseline rule (§2.4.5). Polar, Suunto, Coros, and Apple adapters can be added on this contract later; the Garmin FIT adapter is simply the first and the reference implementation.

### 2.6.1 Bulk versus incremental ingestion

The ingestion layer runs in two modes against the same adapter and schema:

- **Bulk backfill.** At program start the athlete's history (§1.3) is ingested in one pass — potentially years of sessions — to establish the starting physiological state (Section 4). Bulk mode tolerates gaps and mixed recording modes across the history, flagging rather than rejecting, because the state estimator weights sessions by quality and recency.
- **Incremental arrival.** Thereafter each new completed session is ingested as it syncs, one object at a time, and drives the recent-workout and daily loops (Section 6). `context.ingested_at` distinguishes the two so reprocessing and audit can tell backfilled from live-arriving data.

Both modes are **post-session** by design: on the primary Garmin path, data arrives only after an activity finishes and syncs (`research/00` Part 2, finding 1; `research/02` §6), so incremental ingestion is event-driven off sync/push notifications (§2.7), not a live stream.

---

## 2.7 Programmatic access routes

There is no single clean API that yields everything, so the ingestion layer supports several routes behind the adapter, each with a different trust and automation profile (`research/02` §5). All feed the same FIT adapter (§2.3) or canonical schema.

### 2.7.1 Garmin Connect Developer Program (official APIs)

Access requires applying to and being approved for the Garmin Connect Developer Program (an evaluation environment first, then throttled production access), with OAuth user consent, and data flowing only after the user syncs their device to Garmin Connect (`research/02` §5.1). Three APIs matter:

- **Activity API** — the primary official route to raw per-activity data: it delivers completed activities and lets the backend pull the **original FIT file** per activity, with push notifications when a new activity is available. This is what feeds incremental ingestion (§2.6.1) in production, and — because Health Snapshot is recorded as an activity — the official-automation route to its numeric resting rMSSD (§2.4.5, Tier 2), the no-partnership route to which is direct FIT export (§2.7.2).
- **Health API** — all-day wellness and physiological summaries, the **Enhanced Beat-to-Beat (RR) interval** feed (the *official* route to overnight raw RR), and the **HRV Summary** type carrying the numeric overnight rMSSD (`lastNightAvg`) that feeds §2.4.5 Tier 3 (`research/02` §4.1). It is oriented to wellness aggregates rather than granular activity streams.
- **Training API** — the **outbound** channel: it publishes structured workouts and training plans (and courses) to the athlete's Garmin Connect calendar, from which they sync to the watch and execute step-by-step (`research/02` §5.1). This is how the coach pushes prescriptions back to the device. Section 2 only *identifies* this channel as the ingestion layer's counterpart; the structured-workout representation pushed through it is owned by Section 5.

All three are partnership-gated and operate pull/push **after the fact** — not real-time (§2.7.4).

### 2.7.2 Direct FIT file access

Independent of any API, a user can export the original FIT for any activity from Garmin Connect, or copy `.fit` files directly off the watch over USB/MTP (the device presents an `Activity/` folder). This route needs no partnership and yields the fullest raw file (`research/02` §5.2). It is the **recommended default for development and for privacy-conscious users**, ingested with the official FIT SDK profile (§2.3.1), with the Activity API taking over for automation once partnered. It is also the **no-Developer-Program route to Health Snapshot's numeric resting rMSSD** (§2.4.5, Tier 2), since the Snapshot exports as an ordinary activity.

### 2.7.3 Unofficial/third-party routes

Libraries such as `python-garminconnect` (and the auth layer `garth`) reverse-engineer the Connect web/mobile API and log in with the user's own credentials to scrape activities and wellness data (`research/02` §5.4). They are convenient and need no partnership, **but they are unofficial, arguably against Garmin's Terms of Service, break without warning when Garmin changes private endpoints, require handling the user's raw credentials, and `garth` is already marked deprecated.** The spec treats them as a **stopgap/convenience path only**: isolated behind the ingestion abstraction like any other adapter source, never a production dependency, with the TOS and breakage risk disclosed to the user.

### 2.7.4 The optional real-time module

Genuine in-session data is not available on the routes above — the official APIs deliver only after an activity finishes and syncs, so a cloud backend can adapt *between* sessions but not mid-run (`research/00` Part 2, finding 1; `research/02` §6). Real-time is feasible only two ways, both meaningful extra engineering: an **on-device Connect IQ app** that sees live HR/pace/cadence/paired-sensor data and runs in-session logic on the watch, or **direct ANT+/BLE sensor-broadcast** subscription on a companion device (`research/02` §5.3, §6). Per the register (`research/00` Part 3, real-time row) this is an **optional stretch module, not a foundation**: the ingestion layer exposes a clean interface so such a module, when present, emits into the *same* canonical record/RR streams (§2.2) as any post-session source, and everything downstream is unaffected by its absence.

---

## 2.8 What this section hands to the rest of the system

Section 2 delivers the layer every later section reads from: a vendor-neutral canonical schema (§2.2) whose raw streams — record, RR, context — are the only substrate the coaching engine computes on; a fully specified Garmin FIT adapter (§2.3) as the first concrete mapping and the reference for all others; a quarantined vendor-derived sidecar (§2.3.6) that satisfies raw-over-derived by keeping black-box numbers ingestible but inert; data-quality gates (§2.4) that stamp every session and stream with the trust verdict downstream metrics need, including the tiered resting-HRV capture (§2.4.5) that supplies the readiness logic's HRV source — chest-strap raw RR preferred, degrading to a numeric resting rMSSD at reduced confidence; course-geometry ingestion (§2.5) that closes Section 1's `course_profile` forward reference; and a multi-vendor abstraction with defined access routes and bulk/incremental modes (§2.6–§2.7). Three explicit hand-offs carry forward: **Section 3** consumes these raw streams to compute the system's own derived metrics (load, fitness/fatigue/form, HRV trend, grade-adjusted pace, durability), reading `hrv_source_tier` to apply the per-tier confidence weight and the anti-mixing baseline rule; **Section 5** owns the structured-workout representation pushed through the Training API channel this section only identifies; and **Section 9** owns surfacing any sidecar-versus-own divergence (§2.3.6) to the athlete. The division of labor with Section 1 is preserved: Section 1 fixed the input contract, Section 2 normalizes and quality-gates the data behind it, and Section 4 turns that normalized history into the physiological state estimate.

---

*Open items logged for this section (see `spec_development_plan.md`): (1) the resting-HRV capture protocol (§2.4.5) — the earlier hard chest-strap mandate — has been **reconciled** into the `research/00` Part 3 register via `research/00` §3.3 (resting-HRV source tiering); this section now implements that tiering and the item is closed. (2) the course-geometry resampling default (50 m grid, linear interpolation of sparser sources, §2.5) is a spec-introduced default under uncertainty — lower-stakes, still logged for register back-port. Neither blocks use of this section.*
