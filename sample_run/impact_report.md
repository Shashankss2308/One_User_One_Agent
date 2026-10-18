# Impact report

## Historical randomized pilot: intent to treat

Arm assignment comes from `pilot_assignment.csv`; every matched outcome remains in its randomized arm regardless of service. The difference is treatment minus holdout. Confidence interval is an unadjusted normal approximation for the difference in two proportions.

- Matched outcomes: 3891 / 3891 assigned users.
- Treatment: 349/1912 = 18.3%.
- Holdout: 387/1979 = 19.6%.
- ITT risk difference: -1.3% (95% CI -3.8% to 1.2%).
- Biased served-versus-not-served comparison: 40.1% (n=217) vs 17.7% (n=3674); this is not causal because service was selected.

## Replay simulator: descriptive randomized-arm comparison

The practice-seed simulator produced 1698 matured first-goal records and 0 right-censored goals whose windows extend beyond the simulation horizon. The following is a single-seed, synthetic replay result; it is not a production impact estimate. Goal assignment can differ between treatment and holdout due to the treatment advisor, so interpret this as policy-level descriptive performance.

- Agent arm: 84/841 matured first goals = 10.0%.
- Holdout arm: 102/857 matured first goals = 11.9%.
- Descriptive difference: -1.9% (95% normal interval -4.9% to 1.1%); interval does not capture simulator-seed uncertainty.

The simulator accepted all 1,068 published actions with zero policy problems. It reports 824 personalized pages actually shown and 15 published pages not shown because the user's app dialect no longer matched. A second simulator seed is required to characterize simulation uncertainty.
