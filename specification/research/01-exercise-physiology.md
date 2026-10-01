# Exercise Physiology of Endurance Running

**Abstract.** The average pace a runner can sustain over a road race from 5K to the marathon is set by a small number of physiological determinants — maximal oxygen uptake (VO₂max), the metabolic thresholds (lactate threshold, anaerobic threshold, and the closely related critical speed), running economy, the fraction of VO₂max that can be held for the race duration, and the newer construct of durability — interacting with the muscular and cardiovascular systems that supply and use oxygen. This document defines each determinant, quantifies its contribution and how that contribution shifts across race distances, describes the specific training stimulus that drives its adaptation and the ceiling on that adaptation, lays out the intensity-distribution and dose-response evidence, and gives the quantitative models (velocity at VO₂max, critical speed, the %VO₂max–duration relationship, and Riegel-type endurance exponents) that translate a physiological state estimate into a predicted sustainable pace. It closes with environmental and course modifiers of achievable pace and the fatigue/recovery dynamics relevant to training design. Well-established science is distinguished throughout from contested or emerging methods, and inter-individual variability is flagged wherever a number is a rule of thumb rather than a law. Every quantitative claim is cited.

---

## 1. The determinants of sustainable race pace

### 1.1 The classical model

The dominant framework in exercise physiology holds that endurance running performance is governed by three primary variables — VO₂max, the lactate/metabolic threshold, and running economy — which together determine the metabolic power a runner can sustain and therefore the velocity they can hold [1][2][3]. Michael Joyner's influential 1991 modeling paper showed that plausible combinations of these three variables predict a marathon time near ~1:57–1:58 for a hypothetical "best" physiology, and the model has since framed how coaches and scientists reason about the sport [1]. The logic is a chain: VO₂max sets the ceiling of aerobic power; the fractional utilization (%VO₂max at threshold) sets how much of that ceiling can be sustained; and running economy converts the sustainable oxygen uptake into a velocity [1][2][3]. A modern refinement adds a fourth dimension — durability — to account for how these first three drift during prolonged exercise [4][5].

For the target athlete (a serious recreational/competitive amateur) the practical consequence is that improving race-day average pace means improving one or more of these determinants, and the training system's job is to estimate them from data and target them with the right stimulus.

### 1.2 Maximal oxygen uptake (VO₂max)

VO₂max is the highest rate of oxygen consumption attainable during whole-body exercise, expressed in mL·kg⁻¹·min⁻¹ [2]. It represents the integrated ceiling of the oxygen-transport cascade, and in healthy athletes it is limited primarily by maximal cardiac output — the product of maximal heart rate and stroke volume — rather than by the muscle's ability to extract oxygen or by pulmonary diffusion [2]. Bassett and Howley's authoritative review concludes that ~70–85% of the limitation to VO₂max sits in oxygen *delivery* (central cardiovascular capacity), with peripheral factors playing a smaller role at the whole-body level [2].

VO₂max discriminates strongly between recreational and elite runners but poorly *within* a homogeneous elite group, where economy and fractional utilization become the separators [1][3]. Its contribution is greatest at shorter, more intense race distances (3K–10K) where runners operate at or near VO₂max for large fractions of the race, and progressively less decisive at the half and full marathon, which are run well below VO₂max [1][3]. A useful derived quantity is the *velocity at VO₂max* (vVO₂max, or maximal aerobic speed), which combines VO₂max and economy into a single running speed and predicts middle-distance performance better than VO₂max alone [6].

### 1.3 The metabolic thresholds: lactate threshold, anaerobic threshold, and critical speed

These are related but distinct constructs, and conflating them is a common source of error [7].

- **Lactate threshold (LT1 / aerobic threshold):** the lowest exercise intensity at which blood lactate begins to rise measurably above baseline, marking the transition from the moderate to the heavy intensity domain. It typically corresponds to a blood lactate of roughly 2 mmol·L⁻¹, though this is an approximation and the true inflection is individual [7].
- **Anaerobic threshold / lactate turnpoint (LT2 / MLSS-region):** the highest intensity at which lactate production and clearance remain in balance so that blood lactate stabilizes rather than rising inexorably. It is operationalized variously as the maximal lactate steady state (MLSS), the second lactate turnpoint, or a fixed 4 mmol·L⁻¹ concentration (the "OBLA" convention). These conventions do not all give the same intensity, and fixed-concentration methods can misestimate the true steady-state boundary in an individual [7].
- **Critical speed (CS):** the running analogue of critical power — the asymptote of the hyperbolic speed–time-to-exhaustion relationship, representing the highest speed sustainable in a metabolic steady state [8][9]. Physiologically, CS demarcates the boundary between the heavy and severe intensity domains: above CS, VO₂ drifts up to its maximum and exhaustion is inevitable; below CS a steady state is possible [9]. CS is conceptually close to MLSS/LT2 but is derived purely from performance (a set of maximal efforts over different durations) rather than from blood sampling [8][9].

The threshold — however defined — is the single best physiological predictor of distance-running performance, because race pace across all these distances is fundamentally a threshold-anchored effort: the marathon is run somewhat below LT2, the half near it, and the 10K just above it [1][3][7]. For a later specification, the key point is that these constructs are *estimable from field data* (pace, heart rate, and maximal-effort time trials) without a laboratory, but each estimation method carries its own bias that must be stated (§3, §4).

### 1.4 Running economy

Running economy (RE) is the steady-state oxygen (or energy) cost of running at a given submaximal velocity — how much fuel it takes to move at a set pace [10]. Two runners with identical VO₂max can differ by 20–30% in RE, and among elite runners RE often explains performance differences that VO₂max cannot [1][3][10]. Because RE is a cost, better economy means a lower oxygen uptake at race pace, which raises the sustainable velocity for any given fractional utilization [1][10].

RE is multifactorial. Contributing factors include neuromuscular and biomechanical efficiency, lower-limb tendon and muscle-tendon stiffness (elastic energy storage and return, especially at the Achilles), muscle fiber-type distribution, anthropometry (e.g., lower limb mass distribution), and metabolic/thermoregulatory factors [10]. It also varies with footwear — modern "super-shoe" plate-and-foam midsoles improve RE by roughly 4% on average, a genuine and well-replicated effect that a pace model should account for as an equipment modifier [10 provides the framework; the ~4% figure is the widely reported shoe effect]. Its contribution to performance is significant at *all* race distances but is proportionally most decisive at the longer distances where runners are economy-and-durability-limited rather than VO₂max-limited [1][3].

### 1.5 Fractional utilization (%VO₂max)

Fractional utilization is the percentage of VO₂max that can be sustained for the duration of the event [1][3]. It is not an independent tissue property so much as an emergent consequence of where the thresholds sit relative to VO₂max and of how long the event lasts. As a rough guide, well-trained runners sustain approximately: ~95–100% VO₂max for ~3K–5K (10–15 min), ~90–95% for 10K, ~85–90% for the half marathon, and ~75–85% for the marathon (~2–4+ h) [1][3]. The longer the race, the lower the sustainable fraction — this decline with duration is the quantitative bridge between physiology and pace (§4). The marathon fraction in particular is strongly individual and is exactly where durability (Section 1.6) intervenes.

### 1.6 Durability / fatigue resistance (emerging construct)

**Durability is an emerging, not yet fully established, construct, and should be flagged as such.** It is defined as the time course of deterioration in physiological-profiling characteristics — economy, the thresholds, and the efficiency of oxygen use — over prolonged exercise [4][5]. A classical single-shot lab profile (VO₂max, LT, RE measured fresh) can rank two marathoners identically yet miss that one degrades far less over the final third of the race; that resistance to deterioration is durability [4]. Andrew Jones has proposed physiological *resilience* as a "fourth dimension" of endurance performance, independent of the fresh-state determinants [5]. Recent methodological reviews (2025) note that durability lacks a settled measurement standard, and quantifying it — e.g., the drift in critical speed, economy, or the heart-rate/pace relationship after several hours of work — is an active research question rather than a solved one [11][12]. For the marathon in particular, durability is plausibly the determinant most responsible for the last-10K slowdown, and it is highly relevant to the target amateur athlete, whose late-race pace decay is often larger than an elite's [4][5]. Because the evidence base is young, any system using durability should treat it as a corroborating, transparently-derived signal rather than a validated primary metric.

**Table 1 — Determinants by race distance (approximate relative importance; rules of thumb, high inter-individual variability).**

| Determinant | 5K | 10K | Half | Marathon |
|---|---|---|---|---|
| VO₂max | High | High | Moderate | Lower |
| Threshold (LT2/CS) | High | Very high | Very high | Very high |
| Running economy | Moderate | High | High | Very high |
| Fractional utilization (sustainable %) | ~95–100% | ~90–95% | ~85–90% | ~75–85% |
| Durability | Low | Low–moderate | Moderate | High |

Sources for the pattern: [1][3][4].

---

## 2. How each determinant adapts to training

Adaptations divide into **central** (cardiovascular oxygen delivery) and **peripheral** (muscular oxygen use), and different training stimuli bias one or the other [2][13][14].

### 2.1 VO₂max — central-dominant, moderate ceiling

The primary driver of VO₂max improvement is training that spends time at a high fraction of cardiac output — classically high-intensity intervals near VO₂max (e.g., 3–5 min efforts at ~95–100% vVO₂max), but sustained high-volume aerobic training also raises stroke volume through plasma-volume expansion and eccentric cardiac remodeling [2][13][14]. The central adaptations are increased maximal stroke volume and cardiac output, blood/plasma volume, and total hemoglobin mass [2]. Time course: measurable increases appear within a few weeks; Hickson's classic study documented a roughly linear rise in VO₂max over the first ~3 weeks of intense training with continued gains flattening thereafter [13]. Trainability/ceiling: VO₂max is substantially heritable — twin and family studies indicate a large genetic component to both baseline VO₂max and its *trainability* — and untrained-to-trained gains of ~15–20% are typical, with much smaller headroom in already-trained runners, in whom VO₂max often plateaus while economy and threshold keep improving [1][2][3]. For the trained target athlete, VO₂max is therefore a relatively low-ceiling target compared with economy.

### 2.2 Threshold / critical speed — peripheral-dominant, high and durable trainability

Threshold improvements are driven chiefly by *peripheral* muscular adaptations that increase the capacity to produce ATP oxidatively and to clear/buffer metabolites [13][14]. The stimulus is a large volume of aerobic running plus targeted work at and just below threshold ("tempo"/threshold runs, cruise intervals) [14][15]. The underlying adaptations — increased mitochondrial density and volume, up-regulated oxidative enzyme activity (e.g., citrate synthase, succinate dehydrogenase), increased capillary density around each fiber, elevated myoglobin, and a shift toward more oxidative fiber phenotypes — are the well-established Holloszy/Gollnick muscle adaptations to endurance training [14]. These shift the lactate–velocity curve rightward, raising the speed at any given lactate concentration [7][14]. Time course: mitochondrial and capillary adaptations develop over weeks to months and continue improving for years in response to accumulated volume, which is why threshold pace keeps rising in athletes whose VO₂max has plateaued [3][14][15]. Trainability/ceiling: high — fractional utilization and threshold are the most trainable of the classical determinants and the primary long-term levers for the serious amateur [1][3][15].

### 2.3 Running economy — multifactorial, slow but high-headroom

RE improves through several partly independent routes [10][15]: (a) high running volume over months and years (the strongest correlate of good economy, likely via biomechanical refinement and peripheral adaptation); (b) heavy and explosive resistance training and plyometrics, which improve economy by increasing musculotendinous stiffness and neuromuscular efficiency without adding mass — this is one of the better-supported findings in the RE literature [10]; (c) high-intensity interval training; and (d) equipment and environmental factors (footwear, altitude exposure) [10]. Time course: slow — RE changes accrue over months to years of consistent volume, which is why economy is a hallmark of training age. Paula Radcliffe's documented ~15% improvement in running economy across her career, at essentially unchanged VO₂max, is the canonical illustration that economy is trainable over the long run and can drive large performance gains independent of VO₂max [16]. Trainability/ceiling: substantial headroom, especially for amateurs, because economy responds to years of accumulated volume plus strength work that most recreational runners have not maximized [10][15][16]. The relevant peripheral/musculoskeletal adaptations are tendon and muscle stiffness changes, fiber-type and elastic-tissue properties, and neuromuscular coordination [10].

### 2.4 Durability — emerging, likely volume-driven

Because durability is a young construct, its trainability is not yet well quantified, but the leading hypothesis is that it is built by *accumulated training volume and long-duration sessions* that repeatedly expose the muscle to prolonged substrate use and fatigue, improving fat oxidation, glycogen sparing, mitochondrial resistance to prolonged work, and thermoregulatory/fluid adaptations [4][5]. Long runs and progressively longer race-pace efforts are the plausible stimulus. This is consistent with, but not yet proven at the level of the classical determinants; a system should treat durability training as high-volume-driven and monitor late-session pace/HR decoupling as the read-out [4][5].

---

## 3. Intensity distribution and the dose–response evidence

### 3.1 Zone models and their physiological anchors

Two anchoring intensities organize almost all zone systems: **LT1 (aerobic threshold)** and **LT2 (anaerobic threshold / MLSS / critical speed)** [7][17]. These two boundaries divide effort into three physiological intensity domains — moderate (below LT1), heavy (LT1–LT2), and severe (above LT2/CS up to VO₂max) — which is the basis of the widely used **3-zone model** [7][17]. Finer **5-zone** and **7-zone** schemes subdivide these domains for prescription convenience but rest on the same two physiological anchors plus VO₂max/vVO₂max at the top [17].

**Table 2 — Three-zone model and its physiological anchors.**

| Zone | Domain | Lower/upper anchor | Blood lactate (approx.) | Typical purpose |
|---|---|---|---|---|
| Zone 1 | Moderate | Below LT1/aerobic threshold | < ~2 mmol·L⁻¹ | Aerobic base, high-volume easy running |
| Zone 2 | Heavy | LT1 → LT2 | ~2–4 mmol·L⁻¹ | Threshold/tempo development |
| Zone 3 | Severe | Above LT2/CS, up to VO₂max | > ~4 mmol·L⁻¹ | VO₂max and vVO₂max work |

Anchors and lactate approximations: [7][17]. The lactate numbers are conventions with real inter-individual scatter, not fixed truths [7].

### 3.2 Estimating zone boundaries from field data

For a device-driven system that lacks blood lactate, the anchors are estimated from surrogates [7][17][18]:
- **Heart rate:** LT1/LT2 correspond to identifiable heart-rate ranges; deflection points in the HR–pace relationship and fixed %HRmax/%HR-reserve bands are common but imperfect surrogates, and HR lags, drifts, and is confounded by heat and fatigue (§4, §5).
- **Pace / critical speed:** maximal-effort time trials over two or more durations yield critical speed directly (§4), which anchors the LT2/severe boundary without blood [8][9].
- **RPE / talk test:** the ability to speak in full sentences approximates LT1; session-RPE (Foster's method) provides a validated, cheap load surrogate [18].

### 3.3 Dose–response: intensity distribution

The best-supported finding in the training-distribution literature is that successful endurance athletes accumulate the large majority of their training volume at low intensity (below LT1) while reserving a smaller, potent fraction for high intensity — the "polarized" pattern popularized by Seiler, commonly summarized as roughly **~80% of sessions easy / ~20% hard** [19]. Seiler's work and subsequent systematic reviews report that polarized and pyramidal distributions tend to outperform threshold-heavy or purely high-intensity distributions for VO₂max and performance in trained endurance athletes, though effect sizes are modest and the optimal distribution shifts across the season (more threshold/pyramidal in the specific-preparation phase) [19][20]. This is an area of genuine ongoing debate: "polarized vs. pyramidal" is not settled, and the 80/20 figure is a robust heuristic, not a precise optimum [19][20]. For volume specifically, more low-intensity aerobic running is consistently associated with better distance performance up to the point of injury/recovery limits, which for the target amateur — not the elite — is usually the binding constraint [15][19].

---

## 4. Translating physiology into a predicted sustainable pace

A specification needs to turn a physiological state estimate into a pace target. Several complementary, well-established models do this.

### 4.1 Velocity at VO₂max (vVO₂max / maximal aerobic speed)

Because RE links oxygen cost to speed, VO₂max and economy can be combined into a single running velocity — vVO₂max — that already folds two determinants together and predicts middle-distance performance well [6]. Time to exhaustion at vVO₂max is typically ~4–8 min, and vVO₂max is a practical training and prediction anchor for 3K–10K [6].

### 4.2 The critical speed / critical power model

The two-parameter CS model describes the hyperbolic relationship between running speed and time to exhaustion:

**t = W′ / (P − CP)**, or in running terms distance = CS·t + D′,

where **CS (critical speed)** is the sustainable asymptote and **D′ (or W′)** is a fixed distance (energy) capacity usable above CS [8][9]. CS is estimated from two or more maximal efforts of different durations (e.g., 3-min and 12-min, or a set of time trials) [8][9]. CS approximates the highest steady-state pace and therefore anchors sustainable pace for events lasting up to ~30–60 min; races longer than that are run *below* CS, and the gap grows with duration [8][9]. **Known limitations, which the spec must acknowledge:** the model assumes CS is a fixed boundary, but CS itself declines during prolonged exercise (this is precisely the durability phenomenon, Section 1.6); D′ is not a cleanly emptying "anaerobic battery"; and estimates are sensitive to the choice and quality of the test efforts [9][11]. CS is best treated as an accurate anchor for short-to-middle events and a *drifting* one for the marathon.

### 4.3 The %VO₂max–duration relationship and endurance exponents

For events too long for the CS model, the empirical relationship between sustainable %VO₂max and duration (Section 1.5) governs pace: as duration rises, the sustainable fraction falls [1][3]. The most usable engineering form of this is the **Riegel endurance model**, which relates times over two distances by a power law:

**T₂ = T₁ · (D₂ / D₁)^b**,

where the **fatigue/endurance exponent b ≈ 1.06** for trained distance runners across roughly 3K–marathon [21]. An exponent of 1.0 would mean pace is perfectly maintained across distances; b > 1 encodes the real-world slowdown as distance increases [21]. Riegel derived b ≈ 1.06 from large samples of race records, and it remains the standard cross-distance predictor (it underlies most online race-time predictors and Jack Daniels' VDOT tables) [21][22]. **Caveats to specify:** b is a population average; an individual's true exponent depends on their own endurance profile (a marathon-type athlete has a lower effective exponent than a 5K specialist), and accuracy degrades when extrapolating far — e.g., predicting a marathon from a 5K systematically under-predicts marathon time for runners without marathon-specific endurance [21]. The individual exponent is itself an estimable, trainable quantity (durability and long-run volume flatten it), which is a natural derived metric for the system.

### 4.4 Putting it together

Operationally, a predicted average race pace can be built as: estimate vVO₂max and CS from time trials and threshold data → use CS/vVO₂max to anchor short-race pace → apply the individual endurance exponent (or a %VO₂max-vs-duration curve) to project to longer distances → adjust for economy (including footwear) and durability drift for the marathon → apply environmental and course modifiers (§5). This chain is exactly the physiology-to-pace bridge the later specification requires [1][6][8][21].

---

## 5. Environmental and course modifiers of achievable pace

Race pace predicted from physiology is a *still-air, flat, temperate* estimate; real courses modify it, sometimes substantially.

### 5.1 Heat and humidity

Endurance performance is optimal in cool conditions and degrades as heat load rises, because thermoregulatory demand diverts cardiac output to the skin, raises cardiovascular strain and core temperature, and accelerates fatigue [23]. Ely et al.'s analysis of large marathon fields found that finishing times slow progressively as wet-bulb globe temperature (WBGT) rises above roughly optimal (~5–10 °C air temperature for fast runners), and — importantly for the target amateur — **slower runners are hurt more than faster runners** because they are on course longer and accumulate more heat load [23]. Reported decrements scale with both WBGT and runner ability: modest for elites in mild heat but reaching well into double-digit percentages for mid-pack runners in hot, humid conditions [23]. Humidity compounds heat by impairing evaporative cooling, so the effective stress is best captured by WBGT rather than dry-bulb temperature alone [23]. **Rule of thumb (approximate, individual):** expect meaningful slowing once air temperature exceeds ~15 °C, growing roughly with the temperature excess and the race duration; treat the marathon as far more heat-sensitive than the 5K [23].

### 5.2 Altitude

VO₂max, and therefore performance in aerobic events, declines with altitude because of the falling partial pressure of inspired oxygen [24][25]. In endurance athletes VO₂max falls **approximately linearly, by roughly 6–7% per 1000 m of ascent above about 600–700 m** [25]. Acclimatization (increased ventilation, plasma-volume changes, and over weeks increased red-cell mass) partially restores performance but does not fully offset the aerobic penalty for races held at altitude [24]. The "live high–train low" paradigm (Levine and Stray-Gundersen) exploits altitude *residence* to raise hemoglobin mass while preserving sea-level training quality, improving sea-level performance in responders — but this is a training intervention, not a race-day pace modifier [24]. For a race at altitude, the ~6–7%/1000 m aerobic decrement (attenuated by acclimatization) is the operative rule of thumb [25].

### 5.3 Wind

Overcoming air resistance costs energy that rises with the square of the *relative* air velocity. Pugh's classic work established that at middle-distance running speeds in still air, overcoming air resistance accounts for roughly ~8% of total energy cost, rising steeply at sprint speeds [26]. Because the cost is quadratic in relative velocity, a headwind costs more than a tailwind of equal speed gives back, so out-and-back or looped courses net a penalty in wind [26]. Drafting behind another runner reduces this cost substantially [26].

### 5.4 Gradient (elevation gain/loss)

Uphill running raises the metabolic cost of transport and downhill running lowers it up to a point, then raises it again on steep descents due to eccentric braking. Minetti et al. measured the energy cost of running across a wide range of gradients and quantified this asymmetric U-shaped relationship, with a minimum cost at a slight downhill (~−10% to −20% grade) and steeply rising cost on uphills [27]. The practical, well-known consequence is that **the time lost on an uphill is not fully recovered on the equivalent downhill**, so net-flat rolling courses run slower than truly flat ones, and this asymmetry is exactly what a "grade-adjusted pace" model encodes [27]. The Minetti cost-of-gradient curve is the standard physiological basis for such models [27].

---

## 6. Fatigue, recovery, and adaptation dynamics

### 6.1 Supercompensation and the fitness–fatigue model

The foundational training principle is that a session imposes a stimulus that transiently *lowers* performance capacity (fatigue), followed by recovery and then a rebound above baseline (supercompensation) if the next stimulus is timed correctly; too soon and fatigue accumulates, too late and the gain is lost [28]. The more sophisticated and widely used formalization is Banister's **fitness–fatigue (impulse–response) model**, in which each training impulse produces two opposing after-effects — a positive "fitness" component and a negative "fatigue" component — each modeled as an exponential decay with its own time constant, and performance at any moment is their difference [28]. The physiological basis is that the fatigue effect is larger but decays faster (days) while the fitness effect is smaller but longer-lasting (weeks), which is why a taper — reducing load so fatigue dissipates while fitness is largely retained — produces a performance peak [28]. This model is the direct conceptual ancestor of the "acute vs. chronic training load" performance-management charts used in modern software, and it is the natural quantitative scaffold for the system's load/fatigue/readiness metrics [28].

### 6.2 Overreaching vs. overtraining

Deliberately overloading followed by recovery produces **functional overreaching** — a short-term performance dip that supercompensates into a gain within days to ~2 weeks [29]. Pushing further yields **non-functional overreaching** (stagnation/decline lasting weeks with no performance benefit) and, at the extreme, **overtraining syndrome**, a maladaptive state with performance decrement, persistent fatigue, and neuroendocrine/mood disturbance that can take months to resolve [29]. The joint European/American consensus statement emphasizes that these lie on a continuum and that the only reliable retrospective distinction is the *time needed to recover* and whether performance ultimately improves [29]. For the target amateur, whose recovery capacity is often the binding constraint, the system should bias toward functional overreaching and treat sustained unexplained performance/HRV decline as an early overreaching signal [29].

### 6.3 Recovery time courses by session type

Recovery duration scales with the type and severity of the stimulus (approximate rules of thumb, highly individual) [17][19][29]:
- **Easy aerobic runs (Zone 1):** low residual fatigue; recovery within ~a day, so they can be performed frequently — the basis of high-volume easy running in the polarized model.
- **Threshold sessions (Zone 2):** moderate glycogen and neuromuscular cost; typically ~24–48 h to full recovery.
- **VO₂max/severe intervals (Zone 3):** high neuromuscular and metabolic cost; typically ~48–72 h, hence spacing hard sessions ~2–3 days apart.
- **Long runs / marathon-specific durability sessions:** high glycogen depletion and muscle damage (especially with downhill/eccentric loading); ~2–3+ days, with muscle-damage markers and durability read-outs lagging longest.

These time courses justify the standard hard-easy alternation and the ~7–10 day microcycle, and they define the recovery constants the system's fitness–fatigue metrics must encode [17][19][28][29].

---

## 7. Summary for the success metric

Average race pace is the product of how much aerobic power a runner can sustain (VO₂max × fractional utilization, anchored by threshold/critical speed) and how cheaply they convert that power into speed (running economy), degraded over the race by loss of durability and by environmental and course conditions. For the serious amateur, the highest-yield, highest-ceiling targets are **threshold/critical speed, running economy, and durability** — all built primarily by accumulated aerobic volume plus targeted threshold, VO₂max, strength, and long-run stimuli in a predominantly low-intensity distribution — while VO₂max offers a smaller, lower-ceiling headroom. Each determinant is estimable from field data (pace, heart rate, RPE, and maximal-effort time trials), each maps to a specific trainable stimulus with a known approximate time course, and the CS/vVO₂max/endurance-exponent models plus environmental modifiers together convert a physiological state estimate into a predicted race-day average pace — the chain the downstream specification will implement.

---

## References

[1] Joyner MJ. Modeling: optimal marathon performance on the basis of physiological factors. *Journal of Applied Physiology*, 1991;70(2):683–687. https://doi.org/10.1152/jappl.1991.70.2.683

[2] Bassett DR Jr, Howley ET. Limiting factors for maximum oxygen uptake and determinants of endurance performance. *Medicine & Science in Sports & Exercise*, 2000;32(1):70–84. https://doi.org/10.1097/00005768-200001000-00012

[3] Coyle EF. Integration of the physiological factors determining endurance performance ability. *Exercise and Sport Sciences Reviews*, 1995;23:25–63. https://doi.org/10.1249/00003677-199500230-00004

[4] Maunder E, Seiler S, Mildenhall MJ, Kilding AE, Plews DJ. The importance of 'durability' in the physiological profiling of endurance athletes. *Sports Medicine*, 2021;51:1619–1628. https://doi.org/10.1007/s40279-021-01459-0

[5] Jones AM. The fourth dimension: physiological resilience as an independent determinant of endurance exercise performance. *The Journal of Physiology*, 2024;602(17):4113–4128. https://doi.org/10.1113/JP284205

[6] Billat LV, Koralsztein JP. Significance of the velocity at VO₂max and time to exhaustion at this velocity. *Sports Medicine*, 1996;22(2):90–108. https://doi.org/10.2165/00007256-199622020-00004

[7] Faude O, Kindermann W, Meyer T. Lactate threshold concepts: how valid are they? *Sports Medicine*, 2009;39(6):469–490. https://doi.org/10.2165/00007256-200939060-00003

[8] Jones AM, Vanhatalo A. The 'critical power' concept: applications to sports performance with a focus on intermittent high-intensity exercise. *Sports Medicine*, 2017;47(Suppl 1):65–78. https://doi.org/10.1007/s40279-017-0688-0

[9] Poole DC, Burnley M, Vanhatalo A, Rossiter HB, Jones AM. Critical power: an important fatigue threshold in exercise physiology. *Medicine & Science in Sports & Exercise*, 2016;48(11):2320–2334. https://doi.org/10.1249/MSS.0000000000000939

[10] Saunders PU, Pyne DB, Telford RD, Hawley JA. Factors affecting running economy in trained distance runners. *Sports Medicine*, 2004;34(7):465–485. https://doi.org/10.2165/00007256-200434070-00005

[11] Hunter B, et al. Durability as an index of endurance exercise performance: methodological considerations. *Experimental Physiology*, 2025. https://doi.org/10.1113/EP092120

[12] Maunder E, et al. Durability, fatigability, repeatability, and resilience in endurance sports: definitions, distinctions, and implications. *Journal of Applied Physiology*, 2025. https://doi.org/10.1152/japplphysiol.00343.2025

[13] Hickson RC, Bomze HA, Holloszy JO. Linear increase in aerobic power induced by a strenuous program of endurance exercise. *Journal of Applied Physiology*, 1977;42(3):372–376. https://doi.org/10.1152/jappl.1977.42.3.372

[14] Holloszy JO, Coyle EF. Adaptations of skeletal muscle to endurance exercise and their metabolic consequences. *Journal of Applied Physiology*, 1984;56(4):831–838. https://doi.org/10.1152/jappl.1984.56.4.831

[15] Midgley AW, McNaughton LR, Jones AM. Training to enhance the physiological determinants of long-distance running performance. *Sports Medicine*, 2007;37(10):857–880. https://doi.org/10.2165/00007256-200737100-00003

[16] Jones AM. The physiology of the world record holder for the women's marathon. *International Journal of Sports Science & Coaching*, 2006;1(2):101–116. https://doi.org/10.1260/174795406777641258

[17] Seiler S. What is best practice for training intensity and duration distribution in endurance athletes? *International Journal of Sports Physiology and Performance*, 2010;5(3):276–291. https://doi.org/10.1123/ijspp.5.3.276

[18] Foster C, Florhaug JA, Franklin J, et al. A new approach to monitoring exercise training. *Journal of Strength and Conditioning Research*, 2001;15(1):109–115. https://doi.org/10.1519/00124278-200102000-00019

[19] Seiler S, Tønnessen E. Intervals, thresholds, and long slow distance: the role of intensity and duration in endurance training. *Sportscience*, 2009;13:32–53. https://sportsci.org/2009/ss.htm

[20] Rosenblat MA, Perrotta AS, Thomas SG. Effect of high-intensity interval training vs. polarized distribution on endurance performance: a systematic review. 2024. https://pmc.ncbi.nlm.nih.gov/articles/PMC11679080/

[21] Riegel PS. Athletic records and human endurance. *American Scientist*, 1981;69(3):285–290. https://www.jstor.org/stable/27850427

[22] Daniels J. *Daniels' Running Formula*, 4th ed. Human Kinetics, 2021. ISBN 9781718203662.

[23] Ely MR, Cheuvront SN, Roberts WO, Montain SJ. Impact of weather on marathon-running performance. *Medicine & Science in Sports & Exercise*, 2007;39(3):487–493. https://doi.org/10.1249/mss.0b013e31802d3aba

[24] Levine BD, Stray-Gundersen J. "Living high–training low": effect of moderate-altitude acclimatization with low-altitude training on performance. *Journal of Applied Physiology*, 1997;83(1):102–112. https://doi.org/10.1152/jappl.1997.83.1.102

[25] Wehrlin JP, Hallén J. Linear decrease in VO₂max and performance with increasing altitude in endurance athletes. *European Journal of Applied Physiology*, 2006;96(4):404–412. https://doi.org/10.1007/s00421-005-0081-9

[26] Pugh LGCE. The influence of wind resistance in running and walking and the mechanical efficiency of work against horizontal or vertical forces. *The Journal of Physiology*, 1971;213(2):255–276. https://doi.org/10.1113/jphysiol.1971.sp009381

[27] Minetti AE, Moia C, Roi GS, Susta D, Ferretti G. Energy cost of walking and running at extreme uphill and downhill slopes. *Journal of Applied Physiology*, 2002;93(3):1039–1046. https://doi.org/10.1152/japplphysiol.01177.2001

[28] Banister EW, Calvert TW, Savage MV, Bach T. A systems model of training for athletic performance. *Australian Journal of Sports Medicine*, 1975;7:57–61. (See also Busso T. Variable dose–response relationship between exercise training and performance. *MSSE*, 2003;35(7):1188–1195. https://doi.org/10.1249/01.MSS.0000074465.13621.37)

[29] Meeusen R, Duclos M, Foster C, et al. Prevention, diagnosis, and treatment of the overtraining syndrome: joint consensus statement of the ECSS and the ACSM. *Medicine & Science in Sports & Exercise*, 2013;45(1):186–205. https://doi.org/10.1249/MSS.0b013e318279a10a
