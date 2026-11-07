# Calibration sources

The default `literature` profile (config/calibration.yaml) is tuned so that 12-month outputs fall inside ranges reported
in the studies below. Values are approximate and only good enough for a demo, not for evidence generation.

| Quantity | Literature range | POC output (literature profile, seed 42) | Source |
|---|---|---|---|
| Persistence on first biologic at 12 mo | 45-70% | ~62% | AJMC HealthCore study (45.2% persisted, 16.7% switched, 23.2% discontinued, 15% restarted; gap > 45d); Australian OPAL/ARAD study (69.7% remained on first-line b/tsDMARD, 24.0% switched, 6.2% stopped at 12 mo); US claims tocilizumab comparison (45-53% persisted at 1 yr) |
| Switch at 12 mo | ~16-24% | ~17% | same as above |
| Discontinuation at 12 mo | ~6-23% | ~21% | same as above |
| Restart after a gap | ~7-15% | 15% of discontinuers (input) | AJMC; Optum TNFi cycling study |
| Adalimumab adherence (PDC >= 80%) / mean time to discontinuation | ~50% discontinue in 12 mo in MarketScan JAK/ADA study; mean time to discontinuation ~249 d | ~60% PDC>=80% | Advances in Therapy 2023 (upadacitinib vs adalimumab vs JAKi) |
| Symptom onset to diagnosis | median 16-36 weeks | ~20 weeks (138 d) | BMJ Open 2019 UK delay study (27.2 wk onset to rheumatologist); Chan et al. 1994 (36 wk); EULAR 2024 systematic review abstract (>= 16 wk) |
| First visit to rheumatologist | GP delay median ~7-10 weeks | ~11 weeks (75 d) | BMJ Open 2019; ARD 2005 (women 10 wk vs men 3 wk) |
| Diagnosis to first DMARD | ~0-2.2 months | ~1 month (28 d) | OARRR 2017 review of lag times |

Assumptions with no calibration source yet (flag in any demo): hospitalization rates (on vs off therapy), ER rates,
cost amounts, covariate hazard ratios (age, sex, comorbidity), switch destination mix.

URLs:
- https://www.ajmc.com/view/cost-of-biologic-treatment-persistence-or-switching-in-rheumatoid-arthritis
- https://pmc.ncbi.nlm.nih.gov/articles/PMC9536792/
- https://pmc.ncbi.nlm.nih.gov/articles/PMC4883249/
- https://pmc.ncbi.nlm.nih.gov/articles/PMC5702369/
- https://link.springer.com/article/10.1007/s12325-023-02619-6
- https://bmjopen.bmj.com/content/9/3/e024361
- https://ard.bmj.com/content/64/8/1227
- https://pubmed.ncbi.nlm.nih.gov/8003053/
- https://www.tandfonline.com/doi/full/10.2147/OARRR.S138830
