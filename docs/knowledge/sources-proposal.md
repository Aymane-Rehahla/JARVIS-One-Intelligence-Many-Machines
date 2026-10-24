# JARVIS knowledge sources: proposal for owner approval

Prepared 2026-10-23. Every link below was fetched on 2026-10-23. Unless noted otherwise, the fetch was a scripted HTTP GET that followed redirects. "HTTP 200, full text" means the response was the guide itself, with no login, paywall or landing-page-only content. CDC pages refuse scripted requests, so those were checked with a web-fetch tool that returned the page content.

How to read the numbers:
- Each "Numbers stated" bullet repeats a value as the source gives it.
- Values in [square brackets] are my conversions (°F to °C, in. w.c. to Pa, ft/min to m/s, wet-bulb to %RH). They are not quotes.
- Where a source's figure is unclear or looks like a misprint, the bullet says so.

Simulation reference values (the machine manual overrides any guide):
- Incubator: chicken eggs, 21 days. Setpoint 37.5 °C at 55 %RH, raised to 70 %RH for the last 3 days (lockdown). Eggs turn every 3 h (8 per day) with a 0°–45° tilt, and turning stops at lockdown. High-limit 39.5 °C.
- HVAC: barn setpoint 24 °C, 60 %RH.
- Animal protection alarms: CO2 3,000 ppm, NH3 25 ppm, O2 low 19.5 %, CO 35 ppm, heat stress at 30 °C barn temperature.

---

## 1. Incubators (incubator-1 and incubator-2, one shared guide)

| # | Title | Publisher, year | URL | Verified | Covers | Reuse terms |
|---|---|---|---|---|---|---|
| I1 | Hatchery Management Guide for Game Bird and Small Poultry Flock Owners | Mississippi State University Extension Service; web page, undated (site footer 2025) | https://extension.msstate.edu/agriculture/livestock/poultry/hatchery-management-guide-for-game-bird-and-small-poultry-flock-owners | Yes: HTTP 200, full text (HTML) | Temperature, humidity (wet bulb), incubator ventilation and dampers, turning, sanitation, temperature alarms, signs of poor ventilation | Free to read; site says "All rights reserved"; cite, don't copy |
| I2 | Trouble Shooting Failures with Egg Incubation | Mississippi State University Extension Service; web page, undated | https://extension.msstate.edu/agriculture/livestock/poultry/trouble-shooting-failures-egg-incubation | Yes: HTTP 200, full text (HTML) | Symptom, probable cause and fix tables for poor hatches (clears, early dead, pipped not hatched, early or late hatch, sticky chicks, and more) | Free to read; "All rights reserved"; cite, don't copy |
| I3 | Artificial Incubation (AFS-8100) | Oklahoma State University Extension; published Feb. 2017 | https://extension.okstate.edu/fact-sheets/artificial-incubation | Yes: HTTP 200, full text (HTML). The old PDF link at pods.okstate.edu timed out, so use this HTML version. | Incubation periods, temperature, RH before and after pipping, egg position, turning angle and frequency, ventilation, day-by-day embryo development | Free to read; no licence stated; cite, don't copy |
| I4 | Tips for a Successful Hatch (EPS-003) | Texas A&M AgriLife Extension; 7/13 (July 2013) | https://poultry.tamu.edu/wp-content/uploads/sites/14/2023/07/EPS-003-Tips-for-a-Successful-Hatch.pdf | Yes: HTTP 200, full text (PDF) | Egg storage, temperature band, RH, candling day, lockdown steps (turner off, RH up, lid closed), hatch timing | Free to read; no licence stated; cite, don't copy |
| I5 | A Systematic Approach to Solving Hatchability and Chick Quality Problems (VM136) | University of Florida IFAS Extension (EDIS); originally June 2002, reviewed Dec 2018, page stamped "Reviewed 02/2025" | https://ask.ifas.ufl.edu/publication/VM101 (PDF: https://ask.ifas.ufl.edu/publication/VM101/pdf). The old edis.ifas.ufl.edu link redirects here. | Yes: HTTP 200, full text (HTML and PDF) | Hatch-residue breakout method, benchmark percentages for infertile and early, mid and late dead, causes of each failure class | CC BY-NC-ND 4.0 (EDIS copyright page; images excluded) |

Optional extra (not counted in the five): **Care and Incubation of Hatching Eggs**, Tom W. Smith, Mississippi State University Poultry Science; undated PDF, file dated 2009. https://www.poultry.msstate.edu/pdf/extension/incubation.pdf. Verified: HTTP 200, full text (PDF). It adds candling at 7, 14 and 18 days by air-cell size, the 12 % egg-weight-loss target, a wet-bulb to %RH table and power-outage advice. Reuse terms: free to read; cite, don't copy.

### Numbers stated

**I1 (MSU Hatchery Management Guide)**
- Large commercial machines run at 99 °F [37.2 °C]. Most small machines run at 100 °F [37.8 °C].
- Temperature alarm: low at 97–98 °F [36.1–36.7 °C], high at 102 °F [38.9 °C].
- Chicken (forced-air): 21 days; 100 °F dry bulb; 85–87 °F wet bulb [≈55–60 %RH at 100 °F]; no turning after day 18; open vents an extra ¼ on day 10; open vents further (if needed) on day 18. For still-air incubators, add 2–3 °F.
- 3–4 h at 90 °F [32.2 °C] kills few embryos. 30 min at 105 °F [40.6 °C] kills many. High temperature is more harmful than low.
- RH "should always be 57 to 60 percent". 100 °F dry bulb with 87.3 °F wet bulb = 60 %RH.
- Recommended practice: hold 100 °F dry and 85–87 °F wet (as near 86 °F as possible) from day 1 until hatching is complete.
- Raising the wet bulb to 90–92 °F by closing vents may reduce hatch. At 92–94 °F wet bulb with restricted ventilation, embryos die of suffocation on days 19–21.
- Wet-bulb wicks: clean weekly and replace after 4–8 washings.
- Eggshells have 3,000–6,000 pores.
- Turning: commercial machines turn every hour, 24 h a day. Automatic turners should turn at least once every 2 h. Manual turning should be an odd number of times per day (3, 5 or 7). No turning within 3–4 days of hatching.
- Multi-age setters: dampers no more than ½ closed when the machine is nearly full, unless intake air is quite cool.
- Thorough cleaning gives 95–99 % microbial removal.

**I2 (MSU Troubleshooting)**
- Store eggs at 50–60 °F [10–15.6 °C] and 60 %RH. Incubate within 7 days of lay.
- Turn at least 3 times daily. Do not turn within 3 days of hatching. Set eggs small end down.
- Remove chicks as soon as they are fluffy, within 24 h of hatching.
- Breeder floor space at least 3 ft² per bird (breeder flock, not incubator).
- Diagnoses: early hatch (bloody navels) comes from high temperature; late or uneven hatch from low temperature or hot and cold spots; sticky embryos from high average humidity or low temperature; pipped-but-dead from insufficient moisture or poor ventilation.

**I3 (Oklahoma State AFS-8100)**
- Chicken incubation period: 21 days.
- Forced-draft incubators: 99–100 °F [37.2–37.8 °C] throughout. Variations of more than 1 degree reduce hatch.
- Still-air or sectional incubators (measured at the top of the eggs): 101 °F in week 1, 102 °F in week 2, 103 °F until hatch [38.3 / 38.9 / 39.4 °C].
- RH 60 % until pipping, then 70 %. Turkeys need 2–3 % more.
- RH table: 100 °F dry with 87 °F wet ≈ 60 %RH, and 90 °F wet ≈ 68 %RH. One column header in the table is misprinted as "88" where 99 is meant.
- Turn at least 4 times per 24 h, through a 90° plane. Stop 1–3 days before hatching.
- "Embryonic zero" is about 70 °F [21 °C].
- Embryo milestones: heart beats on day 2; embryo orients for hatch on day 14; yolk sac enters the body on day 19; beak enters the air cell on day 20; hatch on day 21.

**I4 (Texas A&M EPS-003)**
- Storage: 60–69 °F [15.6–20.6 °C] at 75–80 %RH, large end up. Set eggs 3–10 after lay; the unit is missing in the source, presumably days.
- Incubator room: 70–80 °F [21–27 °C], draft-free. Start the incubator several days before setting.
- Incubation temperature 99.5–100.5 °F [37.5–38.1 °C]. Check at least twice daily.
- Humidity 55–60 %RH. The water's surface area, not its depth, drives humidity.
- Candle at 10–12 days.
- From 3 days before hatch: turner off, no turning after day 18, eggs on their sides, RH raised to 65–70 %, lid kept closed after day 18.
- Hatching can take nearly 24 h. Leave chicks in the incubator 24 h or until dry.

**I5 (UF/IFAS VM136)**
- Benchmark breakout over a 40-week breeder cycle, as % of eggs set:
  - hatch 85
  - second-quality chicks 1.0
  - dead chicks 0.25
  - pipped eggs 1.25
  - infertile 4.50
  - embryo mortality by day: days 1–4: 2.50; days 5–10: 1.25; days 11–17: 1.25; days 18–21: 2.50
  - contamination 0.50
- 85 chicks per 100 eggs set is achievable. Hatches of 81–82 % or less are common, and some operations get 70–75.
- Candle at 9–12 days to separate infertile eggs from early dead.
- Embryo landmarks: eyes visible at 7–8 days; feathers start at 13–14 days; all structures formed by day 15.
- Causes: deaths on days 1–4 point to egg handling and storage. Deaths on days 5–18 point to machine management, turning, or temperature or RH that is too high or too low. Dead-in-shell points to poor hatcher ventilation and malposition.

**Optional extra (MSU Care and Incubation of Hatching Eggs)**
- Forced-air: 100 °F, with less than ½° swing and no more than 1° total variation. Still-air: 102 °F.
- RH from setting until 3 days before hatch: 58–60 % (84–86 °F wet bulb). At hatch: 65 % or more.
- Chicken table: 100 °F; 85–87 °F wet bulb; no turning after day 18; 90 °F wet bulb for the last 3 days; open vents more on day 18.
- Turn at least 4–6 times daily. No turning in the last 3 days.
- Egg weight should drop 12 % during incubation. Check air-cell size by candling at 7, 14 and 18 days.
- Wet-bulb table at 100 °F dry: 55 % → 85.3 °F; 60 % → 87.3 °F; 65 % → 89.0 °F; 70 % → 90.7 °F.
- Egg storage 55 °F at 75 %RH, no more than 7 days. Hatchability is near zero after 3 weeks of storage.

### Differences from the simulation

- **Setpoint 37.5 °C (99.5 °F):** within all forced-air ranges: TAMU 99.5–100.5 °F, OSU 99–100 °F, MSU 99 °F (commercial) or 100 °F (small machines). It is 0.3 °C below MSU's preferred 100 °F for small machines. Still-air guidance is 2–3 °F higher, but the simulated machine is presumably forced-air.
- **High-limit 39.5 °C (103.1 °F):** MSU I1 sets its high alarm lower, at 102 °F (38.9 °C), and its low alarm at 97–98 °F (36.1–36.7 °C). The simulation has no low alarm listed. MSU's lethality anchor is 105 °F (40.6 °C) for 30 min.
- **Setting RH 55 %:** at the bottom of the published bands. TAMU gives 55–60 %; MSU I1 57–60 %; MSU extra 58–60 %; OSU 60 %. MSU's 85–87 °F wet bulb is about 55–60 %.
- **Lockdown RH 70 %:** matches OSU (70 % after pipping), the TAMU upper bound (65–70 %) and MSU's 90 °F wet bulb (about 70 %). MSU I1 is the exception: it advises keeping the wet bulb near 86 °F (about 57 %) all the way through hatch. It warns that raising humidity by closing vents causes suffocation.
- **Turning every 3 h (8/day):** more often than the extension minimums (OSU at least 4/day, MSU at least 3/day, MSU extra 4–6/day). Less often than MSU I1's "at least once every 2 h" for automatic turners and hourly in commercial setters.
- **Tilt 0°–45° (a 45° arc):** OSU says to turn eggs "through a 90-degree plane". The simulated arc is half that. Check this against the machine manual.
- **Stop turning at lockdown, last 3 days:** consistent with all sources (no turning after day 18). OSU allows 1–3 days and MSU I1 3–4 days.
- **Candling:** not in the simulation spec. Sources give days 10–12 (TAMU), 9–12 (UF), and 7, 14 and 18 (MSU extra).
- **Incubator ventilation:** not in the simulation spec. MSU opens vents an extra ¼ on day 10 and further on day 18, and says more air is needed late in incubation.

---

## 2. HVAC (poultry-house ventilation)

| # | Title | Publisher, year | URL | Verified | Covers | Reuse terms |
|---|---|---|---|---|---|---|
| H1 | Spring and Fall Broiler Production Presents Challenges (Publication 2780) | Mississippi State University Extension Service; 2019 (POD-05-19) | https://extension.msstate.edu/sites/default/files/publications/p2780_web.pdf | Yes: HTTP 200, full text (PDF) | Minimum vs transitional vs tunnel ventilation and when to switch, wind-chill, NH3 and RH targets, moisture balance | Copy and distribute unaltered for non-profit education, with credit to MSU Extension (stated) |
| H2 | Minimum Ventilation Can Challenge Broiler Growers (Publication 2804) | Mississippi State University Extension Service; reviewed 2024 (POD-09-24) | https://extension.msstate.edu/sites/default/files/publications/P2804_web.pdf | Yes: HTTP 200, full text (PDF) | Air-quality targets (O2, CO2, NH3), minimum-ventilation rate table, fan timer calculation, moisture sources | Same MSU terms (stated) |
| H3 | Prepare Broiler Houses for Summer Weather (Publication 2778) | Mississippi State University Extension Service; revised 2024 (POD-07-24) | https://extension.msstate.edu/sites/default/files/publications/P2778_web.pdf | Yes: HTTP 200, full text (PDF, 6 pp) | Tunnel air speed, fan belts, bearings and shutters, evaporative pad care, static pressure limits, night cooling, standby generator | Same MSU terms (stated) |
| H4 | Basic Tunnel Fan Selection Criteria (Poultry Housing Tips Vol. 38 No. 5) | University of Georgia, Dept. of Poultry Science (M. Czarick); 2026 | https://www.poultryventilation.com/wp-content/uploads/vol38n5.pdf (web version: https://www.poultryventilation.com/resources/basic-tunnel-fan-selection-criteria/) | Yes: HTTP 200, full text (PDF and HTML) | Static pressure vs tunnel air speed, fan capacity at pressure, shutters and cones, fan efficiency, belt-wear failures | Free to read; no licence stated; cite, don't copy |
| H5 | Importance of Adequate Ventilation on Air Quality During Cold Weather (Broiler Tip) | University of Georgia Cooperative Extension (B. Fairchild); Jan 2003, still hosted in UGA's archive | https://poultry.caes.uga.edu/content/dam/caes-subsite/poultry/documents/archived-poultry-tips/importance-of-ventilation-during-cold-weather-jan-03.pdf | Yes: HTTP 200, full text (PDF) | O2, CO2, CO and NH3 in poultry houses: normal levels, effect thresholds for birds, the role of minimum ventilation | Free to read; no licence stated; cite, don't copy |

Optional extra: **Broiler House Ventilation During Cold Weather (Publication 2749)**, MSU Extension, revised 2024. https://extension.msstate.edu/sites/default/files/publications/P2749_web.pdf. Verified: HTTP 200, full text (PDF). It adds house-tightness tests, inlet opening widths and the minimum-ventilation static-pressure range. Same MSU reuse terms.

Coverage gap: none of these covers air *filters* as such; poultry houses usually have none. H3 covers filters on the evaporative-pad water line and pad cleaning. If the simulated HVAC has air filters, the machine manual is the only source.

### Numbers stated

**H1 (MSU P2780)**
- Spring and fall swings: nights 35–40 °F [1.7–4.4 °C], afternoons 70–75 °F [21–24 °C].
- Be careful running tunnel mode for young birds when nights drop below 80 °F [26.7 °C]. Wind-chill matters less near 100 °F [37.8 °C] and above.
- Transitional mode can remove as much heat as tunnel mode while running about half the tunnel fans.
- Ammonia ideally 20–25 ppm or less.
- RH best at 55–65 %. At 50 % or below, dust becomes excessive; at 70 % or above, litter cakes.
- Moisture content of air: 40 °F at 50 %RH holds about 3 oz of water per 1,000 ft³; 90 °F at 60 %RH holds about 20 oz.
- The controller lowers the setpoint gradually as the flock ages, by about ½–1 °F per day.

**H2 (MSU P2804)**
- Fresh-air O2 is about 20 %; house O2 should stay at or near that.
- CO2 above 3,000 ppm for extended periods is linked to ascites and to lower weight gain, feed and water intake, and activity.
- Ventilate to keep NH3 at 20 ppm or less. Long exposure to 20 ppm or more harms birds. Eye damage can occur after 12 h at 45–100 ppm. Growers often cannot smell NH3 below 50 ppm.
- Minimum ventilation rate, cfm per bird (the table header reads "cfm/hr/bird", but the worked example uses cfm/bird): week 1: 0.10; week 2: 0.25; week 3: 0.35; week 4: 0.50; week 5: 0.65; week 6: 0.70; week 7: 0.80; week 8: 0.90.
- Fan sizes: a 36-in fan moves about 10,000 cfm and a 48-in fan about 20,000 cfm. Worked example: 50 % duty cycle on a 5-min timer = 150 s on.
- Birds retain about 20 % of the water they drink. Example: 20,000 birds at about 6 lb drink 2,200 gal/day.
- Each gallon of propane burned produces 6.8 lb (0.8 gal) of water.
- Chicks excrete about 0.06 oz of water per hour in week 1 and 0.11 oz in week 2.
- Warming air by 20 °F doubles its moisture-holding capacity.

**H3 (MSU P2778)**
- Tunnel air speed: 400 ft/min [2.0 m/s] is no longer enough. Aim for at least 600–700 ft/min [3.0–3.6 m/s]; some houses reach 800–1,000 ft/min [4.1–5.1 m/s].
- Size the fan count on capacity at 0.10 in. w.c. [25 Pa].
- Static pressure with all tunnel fans running should never exceed 0.12 in. w.c. [30 Pa]. Measure 20–30 ft past the pads and 20–30 ft past the last baffle. Readings of 0.04 in. near the pads and 0.10–0.12 in. at the last baffle mean the baffles are too low.
- Dirty shutters can cut airflow by up to 30 %.
- Replace fan belts once a year. Grease bearings twice a year.
- Air-speed variation: under 20 % in smooth solid-wall houses vs 30–50 % in curtain-sided houses. 600 ft/min everywhere beats 750 at the center with 350 at the sidewall.
- Birds must shed about 12 Btu/h per lb of body weight [≈7.7 W/kg]: about 5 Btu through the body surface and 7 Btu through respiration.
- Night RH runs 80–95 %.
- Drain and refill cool-cell reservoirs weekly.
- Standby generator: exercise weekly for at least 30 min. Keep the fuel tank at least ½ full (⅔ is better). Full service once a year.

**H4 (UGA Poultry Housing Tips 38(5), 2026)**
- Tunnel speed vs typical static pressure (all fans running):

  | Air speed | Static pressure |
  |---|---|
  | 500 ft/min | 0.09–0.12 in. |
  | 600 ft/min | 0.12–0.15 in. |
  | 700 ft/min | 0.15–0.18 in. |
  | 800 ft/min | 0.18–0.21 in. [≈22–52 Pa across the range] |

- For 700 ft/min, rate fans at no less than 0.15 in., ideally 0.20 in.
- Shutter on the intake side: +5–10 % airflow. Butterfly shutters: +5–10 % over interior shutters. Dust on conventional shutters: −10–20 %. Discharge cone: +5–10 %.
- Dirty shutters plus dirty screens can each add 0.05 in. or more. Together they cut airflow 15 % or more (for example, 700 down to 600 ft/min).
- At 0.20 in., a 10 % drop in fan speed can cut capacity by up to 30 %. Replace the belt if fan speed is 5–10 % below the BESS Lab rating.
- Efficiency at 0.10 in.: 19 cfm/W minimum, 20.8 good, 22 or more ideal. Airflow ratio (0.20 in. ÷ 0.05 in.): at least 0.76, preferably 0.79 or more. Fans should be 54 in. or larger.

**H5 (UGA Broiler Tip, Jan 2003)**
- Normal air: O2 20.9 %; CO 0.0 %; NH3 0.0 %. CO2 is printed as "0.314 %", which looks like a misprint for about 0.03 %.
- Chick death needs O2 "well below 18 %". In one study, 18 % vs 23 % O2 lowered day-14 body weight with no lasting effect.
- CO2 up to 10,000 ppm has been seen during preheating. 12,000 ppm is linked to reduced body weight.
- CO: below 200 ppm causes drowsiness, with quick recovery. 600–1,000 ppm causes toe picking, gasping and diarrhea. Above 1,500 ppm, death can occur within 1.5 h.
- NH3: keep below 25 ppm. Even 30 ppm harms young birds over long exposure.

**Optional extra (MSU P2749)**
- Tightness test at 1 cfm per ft² of floor: should reach 0.13–0.15 in. (curtain-sided) or 0.20–0.22 in. (solid sidewall).
- Minimum-ventilation static pressure 0.08–0.12 in. for a 40-ft-wide house, about 0.15 in. for wider houses. Each 0.01 in. adds about 2 ft of air throw.
- Inlet openings: ceiling vents 1–2 in., sidewall vents 1.5–2.5 in.
- RH 50–70 %. Raise minimum ventilation as RH approaches 65 %; lower it as RH nears 50 %.
- NH3: houses without litter amendments often reach 50 ppm or more, against an integrator guideline of 20–25 ppm.
- Each gallon of propane burned consumes 850 ft³/h of fresh air and produces 92,000 Btu, 108 ft³ of CO2 and 6.8 lb of water.
- Run stir fans until birds are 14–18 days old.

### Differences from the simulation

- **Barn setpoint 24 °C (75 °F):** no source gives one fixed house setpoint. They describe a setpoint that falls as birds age. H2 says the house should feel too hot to a person for the first couple of weeks, and H1 describes ramp-downs of ½–1 °F per day. 24 °C suits older birds but would be too cool for chicks. If the simulated barn has a fixed setpoint, say in the guide that this simplifies real practice.
- **Barn RH 60 %:** inside every band: 55–65 % (H1), 50–70 % (P2749 extra). Outside 50–70 %, the sources predict dust (low RH) or caked litter and more NH3 (high RH).
- **NH3 alarm 25 ppm:** equals UGA's "keep below 25 ppm" (H5) and the top of MSU's 20–25 ppm (H1). MSU P2804 (H2) aims for 20 ppm or less, so a 20 ppm warning level would match it better.
- **CO2 alarm 3,000 ppm:** matches H2's 3,000 ppm welfare threshold exactly.
- **CO alarm 35 ppm:** far below the bird-effect levels in H5 (drowsiness under 200 ppm). It is set for people; see section 3, NIOSH.
- **O2 low 19.5 %:** stricter than the bird data in H5 (about 18 %). It matches the human confined-space limit; see section 3.
- **Not modelled yet, but useful for troubleshooting:** static-pressure bands (H3, H4, P2749), tunnel air speed (H3, H4), symptoms of belt and shutter wear (H3, H4), and minimum-ventilation rate per bird (H2).

---

## 3. Animal protection (heat stress, fire, flood/storm/tornado, water loss, gas hazards)

This node covers five sub-topics, so it gets five core sources plus three short supporting references. Each supporting reference fills one gap: livestock water quantities, the cattle heat index, and the O2 19.5 % definition.

| # | Title | Publisher, year | URL | Verified | Covers | Reuse terms |
|---|---|---|---|---|---|---|
| A1 | Heat Stress Management in Broilers (VM65) | University of Florida IFAS Extension (EDIS); originally Dec 1990, reviewed Dec 2018, page stamped "Reviewed 02/2025" | https://ask.ifas.ufl.edu/publication/VM019 (PDF: https://ask.ifas.ufl.edu/publication/VM019/pdf) | Yes: HTTP 200, full text (HTML and PDF) | Signs of heat stress (panting, wings spread, resting near walls and drinkers), panting threshold, humidity effect, response (ventilation, cool water, fasting at peak heat, diet) | CC BY-NC-ND 4.0 (EDIS copyright page) |
| A2 | 10 Ways to Reduce the Risk of Barn Fire (Factsheet 24-021) | Ontario Ministry of Agriculture, Food and Rural Affairs (OMAFRA); May 2024 | https://www.ontario.ca/files/2024-06/omafra-10-ways-to-reduce-the-risk-of-barn-fire-24-021-en-28-05-2024v1.pdf | Yes: HTTP 200, full text (PDF, 7 pp) | Barn fire prevention: housekeeping, temporary and permanent electrical equipment, heaters, hot work, combustibles storage; fire safety plan and contacts | © King's Printer for Ontario; non-commercial reproduction allowed if unchanged and credited (ontario.ca copyright page); otherwise cite, don't copy |
| A3 | Hurricane Preparation and Recovery in North Carolina: Poultry Producers Guide (excerpt of Gen. Tech. Rep. SRS-260o) | USDA Southeast Climate Hub / USDA Forest Service Southern Research Station; authors from MSU and NC State Extension; 2021 | https://www.climatehubs.usda.gov/sites/default/files/Poultry%20Producers%20Guide%20North%20Carolina_0.pdf | Yes: HTTP 200, full text (PDF; contents run to p. 42) | Flood and wind preparedness, backup generators and fuel, water storage, ventilation loss, recovery after storm and flood, mortality disposal, emergency plan template | US federal government work (generally public domain); cover photos licensed from third parties; cite |
| A4 | Hazardous gases on agricultural operations (fact sheet) | Ontario Ministry of Agriculture, Food and Agribusiness (OMAFA); published Nov 2022, page updated Sept 15, 2026 | https://www.ontario.ca/page/hazardous-gases-agricultural-operations | Yes: HTTP 200, full text (HTML; PDF linked) | H2S, CH4, NH3, CO, CO2 and NO2 on farms; effects, exposure limits, monitoring, manure-storage and silo precautions, rescue rules | Same Ontario Crown-copyright terms as A2 |
| A5 | NIOSH Pocket Guide to Chemical Hazards: Ammonia, Carbon dioxide, Carbon monoxide | CDC / NIOSH; pages last reviewed Oct 30, 2019 | https://www.cdc.gov/niosh/npg/npgd0028.html ; https://www.cdc.gov/niosh/npg/npgd0103.html ; https://www.cdc.gov/niosh/npg/npgd0105.html | Yes: content retrieved via web-fetch tool. Scripted curl gets 403 because CDC blocks bots. | Exposure limits for people (REL, PEL, IDLH) for the three gases the barn alarms on | US federal government work, public domain; cite |

Supporting references:

| # | Title | Publisher, year | URL | Verified | Covers | Reuse terms |
|---|---|---|---|---|---|---|
| A6 | Disaster Preparation for Livestock Operations (Timely Information) | Alabama Cooperative Extension System (Auburn) with LSU AgCenter; Sept 2017 | https://ssl.acesag.auburn.edu/agriculture/livestock-poultry/beef/documents/TimelyInformation-DisasterPreparationforLivestockOperations.pdf | Yes: HTTP 200, full text (PDF) | Livestock water needs when supply fails, hurricane and flood checklist, generator upkeep, barn vs pasture during storms | Free to read; no licence stated; cite, don't copy |
| A7 | Cattle Heat Stress Alert (fact sheet) | USDA Southeast Regional Climate Hub; undated | https://www.climatehubs.usda.gov/sites/default/files/SERCH%20Cattle%20Heat%20Stress%20Alert%20Factsheet.pdf | Yes: HTTP 200, full text (PDF) | THI heat-stress thresholds by cattle type, signs, responses | US federal government work; cite |
| A8 | A Guide to Safety in Confined Spaces (DHHS (NIOSH) Publication No. 87-113) | CDC / NIOSH; July 1987, still hosted on CDC Stacks | https://stacks.cdc.gov/view/cdc/5830/cdc_5830_DS1.pdf | Yes: content retrieved via web-fetch tool (scanned PDF with a text layer) | Definition of an oxygen-deficient atmosphere (19.5 %), gas displacement, entry and rescue rules | US federal government work, public domain; cite |

Optional extra for the power-outage angle: **Prepare for Natural Disaster on Commercial Farms**, University of Maryland Extension (J. Moyle), updated July 13, 2021. https://extension.umd.edu/resource/prepare-natural-disaster-commercial-farms. Verified: HTTP 200, full text (HTML). Reuse terms: free to read; cite, don't copy.

Gap: I found no farm-animal **tornado** guide from the preferred publishers. NC State's tornado page is about people only. A3 and A6 cover wind damage, debris and power loss, which are the main tornado effects on a barn.

### Numbers stated

**A1 (UF/IFAS VM65)**
- Panting is "normally expected" when ambient temperature is near or above 30 °C.
- Panting removes about 540 calories per gram of water lost through the lungs.
- High temperature combined with high RH causes fast heat-exhaustion deaths, especially in heavy birds. The document gives no humidity number.

**A2 (OMAFRA 24-021)**
- Keep bedding, hay and other combustibles at least 1 m (3 ft) from electrical systems.
- Keep a 10-lb ABC extinguisher within reach of temporary electrical equipment and hot work.
- Test fire alarms twice a year. Inspect exit signs monthly.
- Store bedding and feed in a building separate from livestock if it will be kept more than 1 week.
- Ventilate livestock buildings at all times, even when empty, to avoid humid, corrosive or explosive air. Keep methane well below the Lower Explosive Limit.

**A3 (USDA poultry hurricane guide, 2021)**
- "Without ventilation, your birds will likely die in about 15 minutes."
- Install generators with fuel storage for at least 2 weeks of full operation. Check before each storm that 2 weeks of fuel is on hand.
- Keep bulk water storage for at least 24 h of water needs in case wells or the municipal supply fail or are contaminated.
- Restart loads in stages after an outage; don't switch on four houses at once. More than 4 houses usually means more than one generator. Consider separate generators for each half of every house.
- Build above the 100-year flood zone, rated for wind of at least 140 mph [225 km/h], preferably 180 mph [290 km/h].
- 1 in. of rain raises the water table about 10 in. in fine-textured soils, 6 in. in flatwoods sandy soils and 4 in. in coarse sands. It takes 4–6 days to recover.
- Floodwaters can peak up to a week after a hurricane.
- Rendering of dead birds works only if they reach the plant in under 48 h.
- Keep drinking water and food for at least 2 weeks for staff stranded on the farm.
- USDA FSA notice of loss is due within 30 days.

**A4 (OMAFA hazardous gases)**
- H2S effects on people:

  | H2S | Effect |
  |---|---|
  | 4 ppm | Easily smelled |
  | 10 ppm | Eye irritation |
  | 27 ppm | Unpleasant odour |
  | 100 ppm | Loss of smell after 2–15 min |
  | 200–300 ppm | Inflammation after 1 h |
  | 500–700 ppm | Unconsciousness or death in 30–60 min |
  | 800–1,000 ppm | Rapid death |
  | >1,000 ppm | Paralysis on first breath |

- 8-h threshold limit values (TWA, from ASABE EP470.1 citing ACGIH): H2S 10 ppm; NH3 25 ppm; CH4 1,000 ppm; CO 25 ppm; CO2 5,000 ppm; NO2 3 ppm.
- NH3 irritates the eyes at 20–50 ppm.
- Manure storage: keep at least 0.3 m (1 ft) of freeboard below the slats. Use pit ventilation only if it can pull at least 0.10 m/s (20 ft/min) down through the slats.
- Silos: keep people away for 3 weeks after filling. Run the blower for 30 min before entry (24-ft silo, 15–30 ft headspace).

**A5 (NIOSH Pocket Guide)**

| Gas | REL (TWA) | Short-term limit | OSHA PEL (TWA) | IDLH |
|---|---|---|---|---|
| Ammonia | 25 ppm | STEL 35 ppm | 50 ppm | 300 ppm |
| Carbon dioxide | 5,000 ppm | STEL 30,000 ppm | 5,000 ppm | 40,000 ppm |
| Carbon monoxide | 35 ppm | Ceiling 200 ppm | 50 ppm | 1,200 ppm |

**A6 (Auburn/ACES 2017)**
- Adult cattle drink 15–25 gal/day [57–95 L]. Needs rise up to 50 % in hot weather and in lactating animals. Water matters more than feed after a disaster.
- Keep about 2 weeks of feed on hand.
- Run the generator under load for a couple of hours at least every 2 months.
- Keep food and water for the family for 1–2 weeks.
- Animals that cannot be evacuated probably survive better in large open pastures than in a barn.

**A7 (USDA cattle heat stress)**
- THI thresholds: beef 75; finishing beef 72; dairy 70; dairy heifers 0–1 yr 77; dairy heifers 1–2 yr 72. Pregnancy rates fall above THI 70.
- Lactating cows by stress level:

  | Stress level | Respiration rate | Rectal temperature |
  |---|---|---|
  | Threshold | >60 breaths/min | >101.3 °F |
  | Mild to moderate | >75 breaths/min | >102.2 °F |
  | Moderate to severe | >85 breaths/min | >104 °F |
  | Severe | 120–140 breaths/min | >106 °F |

- Sprinkling: about ⅓ gal of water on the cow's back every 5 min, with fans.

**A8 (NIOSH 87-113)**
- An oxygen-deficient atmosphere has less than 19.5 % O2; do not enter it without SCBA.
- Entry checklist: O2 at least 19.5 % and not more than 21 %. Above 21 % is oxygen-enriched.
- The guide's chart of lower O2 levels (16 %, 14 %, 6 %) is partly illegible in the scan, so those values are not quoted here.

**Optional extra (UMD 2021)**
- Keep enough generator fuel for at least 24 h, "if not longer".
- Family emergency food and water: 72 h minimum, one month preferable.

### Differences from the simulation

- **Heat-stress alarm 30 °C:** matches A1, where broiler panting is expected at or above 30 °C. No poultry source here combines temperature and humidity into one index. The cattle THI thresholds (A7, 70–77) would only apply if livestock other than poultry are simulated.
- **NH3 alarm 25 ppm:** equals the NIOSH REL TWA (A5) and the ACGIH TLV quoted by Ontario (A4). OSHA's PEL is higher (50 ppm) and the NIOSH STEL is 35 ppm. Ontario notes eye irritation from 20 ppm, and the bird guidance in the HVAC section prefers 20 ppm or less.
- **CO2 alarm 3,000 ppm:** the human limits are much higher (REL and PEL 5,000 ppm TWA; IDLH 40,000 ppm). The 3,000 ppm figure is a bird-welfare level (H2), so the alarm protects the flock rather than workers.
- **CO alarm 35 ppm:** equals the NIOSH REL TWA. It is higher than the ACGIH TLV of 25 ppm quoted in A4 and lower than the OSHA PEL of 50 ppm.
- **O2 low 19.5 %:** equals the NIOSH oxygen-deficiency definition (A8).
- **Response timing:** not in the simulation spec. A3 says birds can die in about 15 min without ventilation, which suggests a hard deadline for the response to an HVAC or power failure.
- **Generator fuel reserve:** sources disagree. A3 asks for 2 weeks, the UMD extra for at least 24 h, and H3 asks for a tank at least ½ full. If the simulation models generator fuel, the guide should give the machine manual's figure and cite A3 as best practice.
- **Water supply:** A3 asks for 24 h of stored water. For poultry consumption, H2 gives about 2,200 gal/day for 20,000 broilers at about 6 lb; A6 covers cattle. Check these against what the simulation uses.

---

## Links checked but not proposed

| Link | Result | Note |
|---|---|---|
| https://extension.psu.edu/hot-weather-management-of-poultry | HTTP 405 (blocked) | Penn State Extension refuses this network, both scripted and in a real browser; likely regional or bot blocking. Good candidate if the owner can open it; its numbers could not be checked. |
| https://extension.psu.edu/fire-prevention-in-barns | HTTP 405 (blocked) | Same host block; tested by script only. |
| https://extension.psu.edu/animals-and-livestock/poultry/reproduction-and-genetics | HTTP 405 (blocked) | Same host block; tested by script and by WebFetch. |
| https://extension.missouri.edu/publications/g2099 | HTTP 403 (blocked) | Missouri livestock heat-stress guide; could not open. |
| https://www.osha.gov/laws-regs/regulations/standardnumber/1910/1910.146 and the matching eCFR page | Blocked (403 or access-request page) | Used NIOSH 87-113 (A8) for the 19.5 % O2 definition instead. |
| https://www.pubs.ext.vt.edu/2902/2902-1090/2902-1090.html | HTTP 200, but the page says the publication is no longer available | Virginia Tech "Incubating Eggs" has been withdrawn. |
| https://extension.umd.edu/agnr.umd.edu/sites/extension.umd.edu/files/publications/HatchingEggsAtHome_FS-1114_ada.pdf | HTTP 404 | UMD FS-1114 PDF link is dead. |
| https://extension.umd.edu/iaa.umd.edu/sites/extension.umd.edu/files/publications/EBR-2%20Natural%20Disaster%20Preparedness%20for%20Poultry%20Producers.pdf | HTTP 404 | Dead link. |
| https://utia.tennessee.edu/publications/wp-content/uploads/sites/269/2025/07/D260.pdf | HTTP 404 | UT/MSU broiler heat-stress publication; dead link. |
| https://pods.okstate.edu/fact-sheets/ANSI-8100web.pdf | Connection timed out | Used the HTML version of the same fact sheet (I3). |
| https://www.aphis.usda.gov/sites/default/files/usda-livestock-preparedness-fact-sheet.pdf | HTTP 403 to scripted request | Not retried; A3 and A6 cover the same ground. |
| https://extension.illinois.edu/sites/default/files/incubation_embryology-2020-k._koelkebeck.pdf | HTTP 200 | A teacher-training slide deck rather than a guide, so not proposed. It states 87 °F wet bulb = 60 %RH for days 1–17, 90 °F = 70 %RH for days 18–21, and that 103 °F for 4 h causes high mortality. |
