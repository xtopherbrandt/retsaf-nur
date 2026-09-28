# Wearable Data Collection & Characteristics (Garmin-First)

**Abstract.** This document specifies, for a developer building an autonomous running-coach system, exactly what a modern Garmin running setup *measures* at the sensor level versus what it *estimates* through proprietary algorithms; how that data is structured in the FIT file format; how accurate and reliable each raw signal is according to the validation literature; how trustworthy the vendor-derived metrics are as secondary signals; and the practical, currently-available routes for getting the data programmatically — including a frank verdict on whether real-time, in-session adaptation is feasible on the Garmin platform. The organizing principle throughout is the project's core design commitment: **prioritize raw, measured signals over vendor-derived black boxes.** Raw streams (heart rate, RR intervals, GPS-derived pace, cadence, barometric altitude, accelerometer-derived running dynamics) are what the coaching model should be built on; Garmin's derived metrics (VO2max estimate, Training Status, Body Battery, HRV Status, Training Readiness) are undocumented, firmware-dependent, and unvalidated, and are usable only as corroborating cross-checks. Because HR, pace, cadence, RR intervals, and running power are all exposed through standard ANT+/BLE profiles that are not Garmin-specific, the same abstraction generalizes to other vendors.

---

## 1. Raw measured signals versus estimated/derived metrics

The single most important distinction for this project is between a **physical quantity a sensor actually transduces** and a **number a proprietary model infers from those quantities**. Garmin's marketing surfaces present both side by side without flagging the difference; a rigorous system must keep them separate.

### 1.1 What is genuinely measured

A typical setup — a Forerunner-class watch, its optical wrist HR sensor, an optional chest or arm HRM (HRM-Pro, HRM-Pro Plus, HRM-Dual, or the arm-based HRM-Fit), and the watch's GNSS receiver — physically measures the following:

- **Heart rate, two independent ways.** The chest strap performs a single-lead **electrocardiogram (ECG)**: it detects the electrical depolarization of the heart across skin electrodes and times the interval between successive R-waves. This yields both an instantaneous HR and, crucially, the **beat-to-beat (RR) interval** in milliseconds — the raw substrate for heart-rate variability [4]. The optical wrist sensor performs **photoplethysmography (PPG)**: green LEDs illuminate the skin and a photodiode measures the pulsatile change in reflected light caused by blood volume in the wrist capillaries. PPG measures a *peripheral pressure pulse*, not electrical activity, and infers HR from its periodicity. Both are measurements, but of different physical phenomena, with materially different error profiles (Section 3).
- **Cadence** (steps per minute), from the watch's and/or the HRM-Pro's internal **accelerometer** detecting the periodic impact of footstrikes. This is a direct kinematic measurement, not an estimate.
- **Position, speed and distance**, from the watch's **GNSS** receiver (GPS plus GLONASS/Galileo/BeiDou; newer Forerunners add **multi-band/dual-frequency L1+L5** reception). The receiver measures satellite ranging; position is computed from it, and speed is derived either from successive positions or from Doppler shift. Distance and *pace* are downstream of this.
- **Altitude**, from a **barometric altimeter** (a pressure sensor) on most mid-to-high Forerunners and fēnix devices. This measures air pressure and converts it to elevation — independent of, and usually better than, GPS vertical position (Section 3).
- **Running dynamics** — vertical oscillation, ground contact time, GCT balance, stride length, vertical ratio — derived from the **accelerometer in the HRM-Pro/HRM-Fit chest/arm unit** (or the watch's own accelerometer on some models). These are kinematic quantities computed from raw acceleration; the acceleration itself is measured, the dynamics are lightly modeled from it.
- **Temperature**, from an onboard thermistor (reflects device/skin temperature, not reliably ambient during a run).
- **Running power**, on watches that compute it natively (or via a Stryd foot pod / the HRM-Pro). This is **modeled**, not measured — see Section 3.5 — but the inputs (pace, vertical oscillation, elevation change, sometimes wind) are measured, so it sits at the boundary.

### 1.2 What is estimated/modeled by proprietary algorithms

Everything in the following list is a *computed inference*, produced by algorithms Garmin licenses from or co-developed with **Firstbeat Analytics** (a company Garmin acquired in 2020) and does not fully publish. None is a sensor reading:

- **VO2max estimate** — inferred from the HR-to-pace relationship during runs [7][8].
- **Training Status, Training Load / acute–chronic load, Training Effect** (aerobic and anaerobic) — inferred from EPOC modeling of each session.
- **Training Readiness** — a composite of HRV status, sleep, recovery time, acute load and stress.
- **HRV Status** — a 7-day rolling baseline of overnight RR-derived HRV, classified as Balanced/Unbalanced/Low [9][10]. Note that the *classification* is the black box; the underlying **numeric overnight rMSSD** it is built from is separately obtainable as a raw number (see §4.1).
- **Body Battery** — a 0–100 "energy" figure from HRV, stress and activity.
- **Performance Condition** — a real-time in-run VO2max deviation figure.
- **Race Predictor / recovery time / stress score** — all model outputs.

The project's stance follows directly: **build the state model on §1.1, treat §1.2 as at best corroborating.** Sections 3 and 4 justify this quantitatively.

---

## 2. The FIT data model

FIT ("Flexible and Interoperable Data Transfer") is Garmin's binary container format for activities, workouts and courses, defined by the official **FIT SDK** [3][5]. A running activity you export or sync arrives as a `.fit` file. A developer needs the following model.

### 2.1 File and message structure

A FIT file is: a **header** (protocol and profile version, data size, ".FIT" signature), a body of **records**, and a trailing **CRC**. The body is a stream of two interleaved record kinds [5]:

- **Definition messages** declare the schema of the data messages that follow: which *global message number* they are, which fields are present, and each field's size and **base type** (e.g. `uint8`, `sint32`, `uint16`, `enum`, `string`, `float32`). Multi-byte fields carry an architecture byte for endianness.
- **Data messages** carry the actual field values, in the layout the preceding definition message declared. Values are packed integers; the **global FIT profile** (shipped as `Profile.xlsx` in the SDK) supplies each field's **scale, offset, and units**, so the decoder recovers engineering units as `physical = raw / scale − offset` [3][5].

The set of message types is the **global profile**. The ones a running coach cares about:

- **record** (global message #20): the **time-series stream** — one message per sample, the heart of the file.
- **lap** (#19) and **session** (#18): roll-up summaries per lap and per activity (totals, averages, maxima).
- **activity** (#34): the top-level wrapper.
- **event** (#21): timer start/stop, and — important — some devices write **RR/HRV** or button events here.
- **hr** (#132) and **hrv** (#78): beat-to-beat interval data (see §2.3).
- **workout / workout_step** and **course**: used for *pushing* structured workouts (Section 5).
- **device_info, file_id, developer_data_id, field_description**: metadata and the developer-field mechanism (§2.4).

### 2.2 Raw record fields and units

The `record` message is where the raw streams live. The commonly present fields, with FIT units:

| Field | FIT units | Notes |
|---|---|---|
| `timestamp` | s (since 1989-12-31 UTC) | one per sample |
| `position_lat`, `position_long` | semicircles | convert: degrees = semicircles × (180/2³¹) |
| `distance` | m (scale 100 → cm precision) | cumulative |
| `enhanced_speed` / `speed` | m/s (scale 1000) | instantaneous; pace is 1/speed |
| `altitude` / `enhanced_altitude` | m (scale 5, offset 500) | barometric where available |
| `heart_rate` | bpm | 1 Hz typical |
| `cadence` + `fractional_cadence` | rpm (running: ×2 for spm) | |
| `power` | W | if power-capable |
| `temperature` | °C | thermistor |
| `vertical_oscillation`, `stance_time`, `stance_time_percent`, `step_length` | mm, ms, %, mm | running dynamics |
| `grade`, `gps_accuracy` | %, m | derived helpers |

Running dynamics and power are frequently carried as **developer fields** (§2.4) rather than native record fields, depending on device and firmware.

### 2.3 RR-interval / HRV data

This is the single most valuable raw stream for the project and the most awkward in FIT. Beat-to-beat intervals are stored in the dedicated **`hrv` message (#78)**, whose single field `time` is an *array* of successive RR intervals in **seconds** (scale 1000 → millisecond resolution), packed several to a message and using a sentinel/invalid value for unused array slots [4]. The developer must concatenate these arrays across all `hrv` messages to reconstruct the full RR series. Note two caveats: (a) many activity files record RR **only when a chest strap is connected** — wrist PPG generally does not populate reliable beat-to-beat data during activity; (b) Garmin's *HRV Status* feature is computed from **overnight** wrist RR sampling whose raw beat-to-beat series is **not** exposed in the activity FIT file. Crucially, though, the *numeric* overnight HRV (an average rMSSD) that HRV Status is built from **is** separately retrievable — via the Health API's HRV Summary — and a numeric rMSSD is also produced by the on-demand **Health Snapshot** activity, which *does* land in a FIT file. Both numeric paths, and how they relate to raw RR, are treated in §4.1; the programmatic mechanics are in Sections 4–5. Some newer devices/firmware place beat-to-beat data in `event` or developer fields rather than message #78, so a robust parser should check all three.

### 2.4 Developer data fields

FIT V2 added **developer data fields**: a `developer_data_id` message registers an application UUID, `field_description` messages declare custom fields (name, base type, units, and which native field they augment), and those fields then ride inside ordinary `record`/`lap`/`session` messages [3][5]. This is how Connect IQ apps and third-party sensors (e.g. Stryd) inject their own streams. A parser must read the `field_description` messages first to interpret them, because their meaning is self-described in-file rather than fixed by the global profile.

### 2.5 Sampling: "smart" versus "every second"

Garmin watches offer two recording modes [3]:

- **Every-second (1 Hz) recording:** every stream is sampled once per second. This is what the coaching system should require, because it preserves the temporal resolution needed for interval detection, HR-lag correction, and accurate load integration.
- **Smart recording:** the watch records a sample only when values change "significantly," producing irregularly-spaced records (sometimes many seconds apart). This drastically shrinks files but corrupts any metric that assumes uniform sampling — trapezoidal load integration, HR/pace decoupling, and cadence statistics all degrade. **The spec should instruct users to enable 1-second recording, detect non-uniform sampling by inspecting `timestamp` deltas, and resample or flag files that used smart recording.**

Native GPS is typically ~1 Hz; HR from a chest strap effectively per-beat (delivered into 1 Hz records); RR intervals are per-beat by definition; running dynamics ~1 Hz.

---

## 3. Accuracy, validity, and failure modes of the raw signals

A metric is only as trustworthy as the signal under it. Each raw stream has characteristic error modes the coaching model must anticipate.

### 3.1 Optical wrist HR vs chest-strap ECG

The literature is consistent and strong: **chest-strap ECG is the practical gold standard; wrist PPG is adequate at rest and low intensity but degrades sharply during hard, dynamic running.** In a prospective athlete study, the Polar H7 chest strap achieved Lin's concordance correlation coefficient rc = 0.98 against clinical ECG, whereas wrist-worn optical devices (including a Garmin wrist unit) sat around rc ≈ 0.89 overall and — critically — **none of the wrist devices maintained rc ≥ 0.70 at 8–9 mph**; accuracy fell as intensity rose [1]. The dominant PPG failure modes are **motion artefact** (wrist flexion, footstrike vibration) and **"cadence lock,"** where the optical signal mistakes the periodic footstrike frequency for the pulse and reports a HR that tracks cadence instead of the heart. PPG also exhibits **lag at intensity transitions** — during the sharp HR rise at the start of an interval and the drop during recovery, the optical estimate trails the true HR by seconds, which is exactly the wrong behavior for a system trying to quantify interval work. Skin tone, perfusion, cold, and strap tightness add further PPG error [1][2].

**Implication for the project:** HR-derived metrics (TRIMP-style load, HR/pace decoupling, threshold estimation) should be computed **from chest-strap HR whenever present**, and the system should down-weight or flag wrist-only sessions, especially interval workouts. The presence of an `hrv` stream is a good proxy for "a chest strap was worn."

### 3.2 Chest-strap RR accuracy for HRV

For HRV to be meaningful, RR-interval timing must be accurate to a few milliseconds and largely free of ectopic/artefactual beats. ECG chest straps (Polar H-series, Garmin HRM-Dual/Pro) provide RR timing that is close to research-grade for **resting/overnight** measurement, which is why the HRV literature and the project should anchor HRV analysis on chest-strap or overnight data rather than in-run wrist data. Even good RR series contain occasional artefacts (missed or doubled beats), so the system **must run artefact detection/correction** (e.g. filtering physiologically impossible RR jumps) before computing rMSSD, SDNN, or frequency-domain measures. During intense running, respiration and motion make in-activity HRV largely uninterpretable; the defensible design is **morning/overnight resting HRV trends**, not intra-workout HRV.

**The resting-state qualification matters, and it is easy to over-generalize.** The PPG failure modes in §3.1 are overwhelmingly *motion*-driven — footstrike vibration, wrist flexion, cadence lock, transition lag. At **rest or overnight**, when the wrist is still, those mechanisms are largely absent, and the gap between optical and ECG HRV narrows substantially relative to the in-run case. So the genuinely hard, uncontested rule is "**never compute HRV from *in-run* wrist PPG**"; the stronger claim that resting/overnight wrist PPG is unusable for HRV does not follow from the same evidence and should not be assumed. This distinction is what makes the numeric wrist-derived paths in §4.1 defensible as a resting-HRV source. The validation literature supports it *with a caveat*: nocturnal PPG-derived rMSSD agrees well with ECG (Pearson r and concordance correlation both > 0.90) but needs longer averaging windows and carries larger individual error, especially in older adults [21]; and resting wrist PPG is reliable overall against ECG, though weaker for short-window rMSSD than for SDNN [22]. These are the studies the numeric tiers' lower rank rests on (§4.1; the tiering is resolved in `research/00` §3.3, which admits these tiers at reduced fidelity, an ordinal rank that selection reads, never at a numeric confidence weight, HRV-04).

### 3.3 GPS/GNSS pace and distance

GNSS gives good **cumulative distance** but noisy **instantaneous pace.** Validation of positioning-enabled sport watches has found distance errors typically within a few percent under open sky, with systematic under- or over-estimation depending on device and conditions [6]. The problems are: (a) **instantaneous pace is inherently noisy**, because it differentiates a position signal with meter-level jitter — which is why watches apply undocumented **smoothing/filtering** that introduces **lag**, so displayed "current pace" reacts a second or more behind reality; (b) **degraded environments** — tree cover, urban canyons, tunnels — cause multipath and dropouts that corrupt both position and pace; (c) tight tracks and switchbacks get "cut." **Multi-band (L1+L5) GNSS** on recent Forerunner/fēnix devices measurably reduces multipath error in difficult environments, improving both distance and pace stability [6], though it costs battery.

**Implication:** treat GPS pace as reliable **averaged over a lap or interval**, not sample-by-sample. For the success metric — average race pace — GPS is fine at the aggregate level. For detecting *within*-interval pacing, prefer lap/segment averages, and consider footpod cadence/stride data as a corroborating speed source on tracks and treadmills.

### 3.4 Barometric vs GPS elevation

**Barometric altitude is substantially more accurate than GPS vertical position** for relative elevation change over a run, because GPS vertical error is typically 2–3× its horizontal error. The barometer's weakness is **drift with weather**: a passing pressure front shifts the baseline, so absolute elevation can be off even when *change* is good. Garmin corrects barometric drift against GNSS/DEM data. **For grade-adjusting pace and computing climb load, use the barometric `enhanced_altitude` stream, smoothed, and be aware that raw grade is noisy.**

### 3.5 Running power validity

Running power is **modeled, and there is no accepted physiological gold standard** to validate it against (unlike cycling, where a crank/hub power meter measures mechanical work directly). Studies of the leading footpod, **Stryd**, report good **reliability** (repeatable, low within-device variability) at submaximal speeds, but its **validity** against true metabolic/mechanical cost is condition-dependent — it changes with terrain and walking vs running [11]. Garmin's own wrist/HRM-derived running power and Stryd use different models and are **not interchangeable**, and Garmin's is not exposed as a simple standard field on all devices. **Verdict for the project:** running power can be a useful *relative* intensity and pacing signal within one athlete on one device, but it should **not** anchor cross-athlete or cross-device physiology, and any use must record which power model produced it.

---

## 4. Assessment of the vendor-derived metrics

Every metric in §1.2 traces to **Firstbeat Analytics**. Firstbeat has published white papers describing the *general approach*, which is more transparency than most vendors offer — but the papers describe a methodology, not the shipped, firmware-specific implementation, and Garmin does not publish the parameters, version history, or per-device tuning actually running on a given watch.

**VO2max estimate.** The Firstbeat method infers VO2max from the relationship between **heart rate and running speed** during normal runs, using the fact that submaximal HR-to-workload slope predicts maximal aerobic capacity, with corrections for HR reliability, and modeling to handle non-steady-state segments [7][8]. Independent evaluation suggests it is a *reasonable population-level estimate* but with meaningful individual error (commonly cited in the range of a few ml/kg/min), and it is sensitive to the very signal weaknesses in §3: bad optical HR, hills, heat, and wind all distort the HR-pace relationship and hence the estimate. It is best read as a **coarse trend line**, not a precise number.

**HRV Status** is a 7-day rolling baseline built from **overnight wrist-PPG beat-to-beat sampling**, classified relative to the individual's own recent range [9][10]. Two distinct things must be separated here. The **classification** — Balanced/Unbalanced/Low against an undocumented, firmware-dependent baseline — is a black box, useful only as a corroborator. But the **numeric overnight rMSSD** underneath it is a transparent, standard HRV statistic (root mean square of successive RR differences, in ms), and Garmin now exposes that number directly through the Health API's HRV Summary (§4.1). The concept (personalized rolling HRV baseline) is sound and aligns with the HRV-guided-training literature; the system should own the *baseline and interpretation* from the numeric rMSSD rather than consume Garmin's classification.

**Training Readiness, Body Battery, Training Status/Load, Performance Condition, Race Predictor** are composite black boxes stacking several of the above (themselves estimates) with sleep and stress models. Each additional layer compounds undocumented assumptions and firmware dependence, and there is little-to-no *independent* peer-reviewed validation of the shipped composites.

| Vendor metric | What's publicly documented | Independent validation | Trust as secondary signal |
|---|---|---|---|
| VO2max estimate | Firstbeat white papers: HR–speed method [7][8] | Some studies; moderate individual error | Low–moderate; use as coarse trend only |
| HRV Status *(the classification)* | Blog/manual: 7-day overnight baseline [9][10]; method not published | Concept validated in literature, this impl. not | Moderate as corroborator of own HRV trend |
| Overnight rMSSD *(the number under it)* | Numeric field via Health API HRV Summary [18]; standard rMSSD statistic | rMSSD well-established; resting/nocturnal PPG-vs-ECG agrees at a discount [21][22] | Moderate–usable as a resting-HRV input (§4.1) |
| Training Load / ACWR / Training Effect | EPOC-based (Firstbeat concept) | Sparse for shipped version | Low; compute own load from raw HR/pace |
| Training Readiness | Composite; components listed, weights not | None independent | Low; do not drive decisions from it |
| Body Battery | HRV+stress+activity; algorithm undisclosed | None independent | Very low; ignore for coaching logic |
| Performance Condition | Real-time VO2max deviation | Minimal | Low; noisy, informational only |

**Overall verdict:** the *composite* metrics are **firmware-dependent, undocumented in their shipped form, and change without notice** — exactly the properties the project deems disqualifying for a foundation. They may enter the system **only** as secondary cross-checks (e.g. "our computed VO2max trend diverges from Garmin's — flag for review"), never as inputs to plan decisions. The one exception the next subsection carves out is the **numeric overnight/resting rMSSD**, which is not a composite black box but a standard statistic, and which the readiness logic may legitimately consume.

### 4.1 Numeric resting/morning HRV is obtainable — two concrete paths beyond a chest strap

A recurring design worry is that requiring a daily chest-strap resting HRV capture adds a behavioral barrier in front of the single most valuable readiness signal, and that Garmin's only wrist-side HRV surface is the black-box HRV Status classification. That second premise is **outdated**: a *numeric* resting rMSSD is retrievable two ways, and both matter to the design.

**Path A — Health API HRV Summary (passive, overnight, numeric).** Garmin added HRV data to the **Health API** as a first-class summary type [18]. The HRV Summary carries, per night: `lastNightAvg` — the **average overnight HRV as an rMSSD value in milliseconds** (independent normalizers map this field straight to an rMSSD-in-ms biomarker; an illustrative value is ~46 ms) [18][20]; `lastNight5MinHigh` — the maximum rMSSD over any 5-minute window overnight; and `hrvValues` — a **map of individual HRV readings with their time offsets across the night**, i.e. an intra-night rMSSD time series, not merely an aggregate. The measurement is taken **overnight, at rest, from the wrist optical sensor** while the athlete sleeps — squarely the resting-state case where the §3.1 motion failure modes are absent (§3.2). Its cost is access: the Health API is **partnership-gated** (Connect Developer Program approval, OAuth) — the same barrier as every official-API route (§5.1) — and it delivers a Garmin-*computed* rMSSD, not raw RR, so the beat detection and artefact handling upstream are not ours to inspect.

**Path B — Health Snapshot (on-demand, 2-minute, in a FIT file, no Developer Program).** **Health Snapshot** is a built-in Garmin activity in which the athlete holds still for **two minutes (~119 s)** while the watch records a short cardiovascular panel [19]. Its export panel includes heart rate (min/max/avg), respiration (min/max/avg), stress (min/max/avg), and **HRV as `RmssdAvgValue` (average rMSSD) and `SdrrAvgValue` (SDNN)** [20]. Because it is saved as an **activity**, its result rides out in the athlete's **own FIT/original file**, reachable by ordinary FIT export or the Activity API — **without** Health-API partnership (§5.2). This makes it the cleanest **short-term** path to a numeric morning HRV number: an active, on-demand, held-still wrist reading that yields an rMSSD directly. It is still a small behavioral ask (launch the app, sit still for two minutes) but it needs **no chest strap** and **no Developer Program**. As with Path A, the guaranteed output is a Garmin-computed rMSSD *summary*; whether the underlying beat-to-beat series is present in the file depends on device/firmware and on whether a chest strap was paired (§2.3).

**Where these sit in the raw-over-derived hierarchy.** Neither Path A nor Path B is raw RR, so neither is fully "owned" the way a chest-strap capture is — but both are a *documented statistic* (rMSSD, ms), not a proprietary composite like VO2max or Body Battery, so they are a genuinely different and higher tier than HRV Status. This yields three resting-HRV input tiers the specification can degrade through, with the HRV Status classification quarantined beside them and never a tier (`research/00` HRV-01):

1. **Chest-strap morning resting RR** (FIT `hrv` message, raw beats) — the system computes rMSSD itself and applies its own artefact filter; fully owned/transparent, highest fidelity, highest behavioral cost (strap on at rest).
2. **Health Snapshot** (FIT activity, `RmssdAvgValue`) — numeric rMSSD, no strap, no Developer Program; the best short-term path; Garmin-computed pipeline.
3. **Health API HRV Summary** (`lastNightAvg` + `hrvValues`) — passive nightly numeric rMSSD, zero daily ask, but Developer-Program-gated and Garmin-computed.
4. **HRV Status classification** (Balanced/Unbalanced/Low) — black box, quarantined beside the three tiers; not a tier, and never a trend input.

The design consequence is that the chest-strap protocol should be the *preferred, highest-fidelity* tier rather than a hard prerequisite: when it is absent, the readiness logic can fall to Tier 2/3 (a numeric rMSSD admitted at reduced fidelity, an ordinal rank below the strap that selection reads) instead of collapsing to "HRV unavailable." Two caveats travel with Tiers 2–3: because the rMSSD is Garmin-computed, (a) it ranks below the strap, a rank grounded in the resting/nocturnal PPG-vs-ECG validation literature [21][22] and never a numeric per-tier confidence weight (`research/00` HRV-04 defers any such weight to Section 6's readiness fusion), and (b) cross-device changes (the athlete switching watches) and silent firmware algorithm changes are risks the trend logic must tolerate — which is why the trend must not mix source tiers within one baseline. **This finding has been reconciled into `research/00` §3.3 (resting-HRV source tiering), which amends the Part 3 data-quality-gating and HRV-gate register rows accordingly; spec §2.2.3, §2.4.5, and §3.7 implement the three input tiers, the pre-computed-rMSSD ingestion path, and the per-source baseline discipline.**

---

## 5. Programmatic data access — the practical routes

There is no single clean API that gives an outside developer everything. The routes, with current status:

### 5.1 Garmin Connect Developer Program (official APIs)

Access requires **applying to the Garmin Connect Developer Program and being approved**; you receive an evaluation environment first, then throttled production access [12]. All use **OAuth** user consent, and data flows only after the user syncs their device to Garmin Connect. Three relevant APIs:

- **Health API** — all-day wellness and physiological summaries: HR, steps, calories, sleep, stress, respiration, pulse-ox, body composition, and **epoch** summaries; it supports both **Ping/Pull and Push** delivery, and offers **Enhanced Beat-to-Beat (RR) interval** data as a licensed feature [13]. It also now exposes an **HRV Summary** type carrying numeric overnight rMSSD (`lastNightAvg`, `lastNight5MinHigh`, and a per-timestamp `hrvValues` map) [18], and a **Health Snapshot** summary type [20] — see §4.1. This is the *official* route to overnight RR/HRV numbers. It is oriented to wellness aggregates, not granular activity streams.
- **Activity API** — completed **activity** data and files, including the ability to pull the **FIT/original file** for each activity; push notifications tell your backend when a new activity is available [12][14]. This is the primary official route to the raw per-activity streams the coaching model needs — and, because Health Snapshot is recorded as an activity, the official-automation route to its numeric rMSSD as well (the no-partnership route is §5.2).
- **Training API** — the *outbound* channel: **publish structured workouts and training plans to the user's Garmin Connect calendar**, from which they sync to the watch and execute step-by-step; also supports courses [15]. This is how the coach *pushes prescriptions back* to the athlete's device.

All three are **partnership-gated** (approval, possible commercial terms, throttling) and **pull/push after-the-fact** — they are not real-time (Section 6).

### 5.2 Direct FIT file access

Independent of any API, a user can **export the original FIT** for any activity from Garmin Connect (web/app), or copy `.fit` files **directly off the watch over USB/MTP** (the device presents an `Activity/` folder). This route needs no partnership and gives the fullest raw file. Robust open-source parsers exist — Garmin's **official FIT SDK** (multiple languages) [3], plus community libraries such as Python's `fitparse`/`fitdecode` and Go's `muktihari/fit` [4]. **Recommended default for development and for privacy-conscious users:** ingest user-provided or exported FIT files with the official SDK's profile, falling back to the Activity API for automation once partnered. **This is also the no-Developer-Program route to Health Snapshot's numeric rMSSD** (§4.1, Path B): the Snapshot is an ordinary activity, so its FIT exports like any other.

### 5.3 Connect IQ SDK (on-device apps/data fields)

**Connect IQ** is Garmin's on-watch app platform (Monkey C language) [16]. It lets you ship **data fields, widgets, and apps** that run *on the watch during the activity* and can read live sensor data — current HR, cadence, pace, and paired ANT+/BLE sensors — and display or log it. Its relevance is precisely **real-time in-session access** (Section 6): a Connect IQ app is the only sanctioned way to compute and react to metrics live on the device. Constraints: limited memory/CPU, no guaranteed persistent network link (BLE to phone is available but power/reliability-limited), and background/data-field execution is sandboxed. It is powerful but a substantial separate engineering effort.

### 5.4 Unofficial/third-party routes

Libraries such as **`python-garminconnect`** (cyberjunky) and the auth layer **`garth`** [17] work by **reverse-engineering the Garmin Connect web/mobile API** and logging in with the user's own credentials to scrape activities, wellness data, and even upload workouts. They are convenient and require no partnership, **but they are unofficial, not authorized by Garmin, arguably against Garmin's Terms of Service, and break without warning whenever Garmin changes its private endpoints or auth flow** (note that `garth` is already marked deprecated). They also require handling the user's raw Garmin credentials. **The spec should treat these as a stopgap/convenience path only, isolate them behind the ingestion abstraction, never depend on them for a production commitment, and clearly disclose the TOS and breakage risk to users.**

### 5.5 Summary: getting a numeric morning HRV, ranked by build friction

Pulling §4.1 and the routes above together, for the specific goal of obtaining a numeric resting/morning rMSSD the readiness logic can trend:

- **Lowest friction, short-term:** have the athlete run **Health Snapshot** each morning and ingest its FIT via **direct export** (§5.2) — numeric rMSSD, no partnership, no chest strap.
- **Lowest daily burden, once partnered:** subscribe to the **Health API HRV Summary** (§5.1) for the passive overnight `lastNightAvg` rMSSD — no athlete action at all, but requires Developer-Program approval.
- **Highest fidelity:** a **chest-strap** morning capture whose raw RR (`hrv` message) the system reduces to rMSSD itself, via FIT export (§5.2) or the Health API's Enhanced Beat-to-Beat feed (§5.1).

These are not mutually exclusive — the ingestion layer should accept whichever is present and tag its tier (§4.1; `research/00` HRV-04 admits the numeric tiers at reduced fidelity, an ordinal rank that selection reads, never at a numeric confidence weight), subject to the per-source baseline discipline (`research/00` §3.3; spec §3.7).

---

## 6. Real-time / in-session data access feasibility

This is the project's key open architectural question, and the honest answer is nuanced.

**The default Garmin reality is *not* real-time.** The Connect Developer APIs (Activity/Health) deliver data **only after the activity is finished and synced** — the FIT file is uploaded when the run ends (or at intervals if LiveTrack is on, but LiveTrack is a coarse location/summary feed, not a full raw stream). So a cloud coaching backend built on the official APIs **cannot adapt a workout mid-run**; it can only adapt *between* sessions.

The feasible real-time paths, and their costs:

1. **Connect IQ app on the watch (feasible, the real answer).** An on-device Connect IQ app/data field *does* see live HR, pace, cadence, and paired sensors in real time and can implement in-session logic — e.g. "your HR drift exceeds target, ease the next rep" — displaying prompts on the watch itself. This is the **only robust way to do genuine in-session adaptation on Garmin.** Cost: a separate Monkey C application, device-side compute limits, and the coaching logic must be portable to the watch. Optionally it can stream to a phone over BLE for heavier computation.
2. **Direct sensor broadcast to a companion device (feasible, but bypasses the watch's models).** HRM straps and footpods broadcast HR/RR/cadence/power over **ANT+ and BLE**. A phone or edge app can subscribe to those broadcasts directly and run its own real-time engine — but then you are consuming the *sensors*, not the Garmin watch's data, and you lose the watch's GPS/dynamics unless you also read them.
3. **Phone-tethered streaming from the watch (limited).** There is no general, supported "stream every FIT record live to my server" facility; LiveTrack and Connect IQ-to-phone messaging are the closest, and neither is a full raw feed.

**Verdict.** *Between-session* adaptation (the core of periodized coaching) is fully feasible today with the official Activity/Health APIs or FIT export. *In-session* adaptation is feasible **only** by putting logic on the device via **Connect IQ**, or by subscribing to **ANT+/BLE sensor broadcasts** on a companion device — both of which are meaningful additional engineering and are best treated as an optional Phase-2+ capability, not a foundation. **Recommended architecture:** design the coaching engine for post-session adaptation from raw FIT/Activity-API data as the primary loop, and define a clean interface so an optional Connect IQ / live-sensor real-time module can be added later without redesign.

---

## 7. Extensibility note: cross-vendor universal signals

The abstraction layer should be built around the raw signals that are **standardized across vendors**, because these are exactly the ones exposed by open **ANT+ and Bluetooth Low Energy (BLE) GATT profiles**, not by Garmin proprietary formats:

- **Heart rate and RR intervals** — ANT+ *Heart Rate* profile and BLE *Heart Rate Service* (0x180D), whose measurement characteristic includes the optional RR-interval field. Any compliant strap (Polar, Wahoo, Garmin, Coros-paired) speaks this.
- **Speed/pace and cadence** — ANT+ and BLE *Running Speed and Cadence Service* (RSC, 0x1814); footpods across vendors implement it.
- **Cycling-style and running power** — ANT+/BLE *Power* profiles (Stryd and others).
- **Position/altitude** — not a sensor-profile standard, but GPS/GNSS position and barometric altitude are universal physical quantities present in every vendor's activity file (FIT, TCX, GPX).

**Design consequence:** model the ingestion layer around a **vendor-neutral canonical schema** — timestamped streams of HR, RR, pace/speed, cadence, altitude, power, and running-dynamics — into which a **Garmin FIT adapter** maps first, and to which Polar/Suunto/Coros/Apple adapters can be added later. Keep every **vendor-derived** metric out of the canonical schema entirely; if retained at all, they belong in a clearly-labeled, quarantined "vendor annotations" sidecar that never feeds a decision — no setting or default lets the coaching logic read it (`research/00` ARCH-06, PRIN-08). This directly enforces the project's raw-over-derived principle while leaving the door open to any wearable that emits the standard raw signals. The same abstraction covers the resting-HRV numeric tiers: another vendor's resting/overnight PPG rMSSD enters at the numeric tier the same way Garmin's does (§4.1; `research/00` §3.3).

---

## References

[1] Pasadyn SR, et al. Accuracy of commercially available heart rate monitors in athletes: a prospective study. *Cardiovascular Diagnosis and Therapy*. 2019. https://cdt.amegroups.org/article/view/26754/25185

[2] Wrist-Worn and Arm-Worn Wearables for Monitoring Heart Rate During Physical Activities: Device Validation Study. *JMIR Cardio*. 2025. https://pmc.ncbi.nlm.nih.gov/articles/PMC11951816/

[3] Garmin. FIT SDK — Overview. Garmin Developers. https://developer.garmin.com/fit/overview/

[4] Garmin Forums / FIT SDK. FIT File HRV Data Array Interpretation. https://forums.garmin.com/developer/fit-sdk/f/discussion/255690/ ; muktihari/fit (FIT Protocol V2 decoder). https://github.com/muktihari/fit

[5] Garmin. FIT Protocol. Garmin Developers. https://developer.garmin.com/fit/protocol/

[6] Accuracy of Distance Recordings in Eight Positioning-Enabled Sport Watches: Instrument Validation Study. 2020. https://pubmed.ncbi.nlm.nih.gov/32396865/ ; Software Correction of Speed Measurement Determined by GNSS Modules for Runners. 2023. https://pmc.ncbi.nlm.nih.gov/articles/PMC10007219/

[7] Firstbeat. VO2 Estimation Method Based on Heart Rate Measurement (white paper). https://www.firstbeat.com/wp-content/uploads/2015/10/white_paper_vo2_estimation.pdf

[8] Firstbeat. Automated Fitness Level (VO2max) Estimation with Heart Rate and Speed Data (white paper). 2017. https://assets.firstbeat.com/firstbeat/uploads/2017/06/white_paper_VO2max_30.6.2017.pdf

[9] Garmin. Understanding the HRV Status on Your Garmin Smartwatch. Garmin Blog. https://www.garmin.com/en-US/blog/fitness/understanding-the-hrv-status-on-your-garmin-smartwatch/

[10] Garmin. Forerunner 265 Owner's Manual — Heart Rate Variability Status. https://www8.garmin.com/manuals/webhelp/GUID-F41EAFB3-6CC9-42DE-9C6C-9E358DBB0671/EN-US/GUID-9282196F-D969-404D-B678-F48A13D8D0CB.html

[11] Validity of the Stryd Power Meter in Measuring Running Parameters at Submaximal Speeds. 2020. https://pubmed.ncbi.nlm.nih.gov/32698464/ ; Reliability of Stryd and Garmin RP Wearable Devices. 2024. https://pmc.ncbi.nlm.nih.gov/articles/PMC11175203/

[12] Garmin. Garmin Connect Developer Program — Overview & Activity API. https://developer.garmin.com/gc-developer-program/overview/ ; https://developer.garmin.com/gc-developer-program/activity-api/

[13] Garmin. Health API. Garmin Connect Developer Program. https://developer.garmin.com/gc-developer-program/health-api/

[14] Garmin. Program FAQ. Garmin Connect Developer Program. https://developer.garmin.com/gc-developer-program/program-faq/

[15] Garmin. Training API. Garmin Connect Developer Program. https://developer.garmin.com/gc-developer-program/training-api/

[16] Garmin. Connect IQ SDK — Overview and Core Topics: Sensors / ANT and ANT+. https://developer.garmin.com/connect-iq/overview/ ; https://developer.garmin.com/connect-iq/core-topics/sensors/

[17] cyberjunky. python-garminconnect (unofficial Connect API wrapper). https://github.com/cyberjunky/python-garminconnect ; matin. garth (Garmin SSO auth, deprecated). https://github.com/matin/garth

[18] Garmin. HRV Summaries are now available for Health API. Garmin Developer Portal (blog). https://developerportal.garmin.com/blog/hrv-summaries-are-now-available-health-api ; field normalization to overnight rMSSD (ms): Sahha — Garmin integration. https://sahha.ai/integrations/garmin/

[19] Garmin. Health Snapshot (Owner's Manual, Venu 2 series). https://www8.garmin.com/manuals/webhelp/GUID-D93137A9-B374-4A24-8A4D-A66C9AC91265/EN-US/GUID-7E8CB930-46DE-4622-AAD0-5E21DBB1A8E1.html ; overview: Wareable — What is Garmin Health Snapshot. https://www.wareable.com/garmin/what-is-garmin-health-snapshot-how-to-use

[20] Garmin Health API export formats — HRV Summary and Health Snapshot Summary fields (`LastNightAvg`, `LastNight5MinHigh`, `HrvValues`; `RmssdAvgValue`, `SdrrAvgValue`, HR/respiration/stress). MyDataHelps / CareEvolution. https://support.mydatahelps.org/garmin-heart-rate-variability-summary-export-format ; https://support.mydatahelps.org/garmin-health-snapshot-summary-export-format

[21] Liang T, Yilmaz G, Soon C-S. Deriving Accurate Nocturnal Heart Rate, rMSSD and Frequency HRV from the Oura Ring. *Sensors*. 2024;24(23):7475. https://www.mdpi.com/1424-8220/24/23/7475 — nocturnal PPG-derived rMSSD vs ECG: Pearson r and CCC > 0.90 with a ≥80% validity-proportion threshold and ≥30-min aggregation; larger individual error at short windows and in older adults.

[22] Zuern C, et al. Validation of photoplethysmography-derived heart rate variability against ECG under resting conditions. *Scientific Reports*. 2026;16:22597. https://www.nature.com/articles/s41598-026-52700-7 — resting wrist PPG HRV is reliable overall against ECG (SDNN ρ ≈ 0.98) but shows weaker agreement for short-term rMSSD/entropy; authors note real-world (non-resting) validation is still needed.
