# Gemeinde-Level OD Matrix — Design

**Date:** 2026-03-23
**Status:** Approved
**Author:** Christoph Garritsen + Claude

## Problem

The current Pendler-constrained model matches official Kreis-level shares perfectly (r=1.0) but only achieves r=0.50 at Gemeinde level. The gravity sub-distribution within each Kreis underestimates internal flows and over-attracts large employment centers.

## Data Sources

| File | Content | Coverage |
|------|---------|----------|
| `2024_Verfl_L09.csv` | Top-10 Auspendler destinations per Gemeinde (12-digit ARS) | 73% median of total Auspendler |
| `2024_IOP_Karte_L00.csv` | Internal commuters per Gemeinde | 100% |
| `19321-001r.xlsx` | Eckzahlen: total Auspendler per Gemeinde | 100% |

ARS (12-digit) to AGS (8-digit) mapping: `ags = ars[:5] + ars[9:]`.

## Design: Three-Way Config Switch

```yaml
# Mode 1: pure gravity (backward compatible)
pendler_od_path: null
gemeinde_od_path: null

# Mode 2: Kreis-level Pendler + gravity within Kreis
pendler_od_path: germany/krpend-k-0-202306-xlsx.xlsx
gemeinde_od_path: null

# Mode 3: Gemeinde-level OD + gravity fill for tail
pendler_od_path: germany/krpend-k-0-202306-xlsx.xlsx
gemeinde_od_path: bavaria/2024_Verfl_L09.csv
gemeinde_iop_path: bavaria/2024_IOP_Karte_L00.csv
```

Logic in `model.py execute()`:
1. `gemeinde_od_path` set → Gemeinde-level mode (Approach A)
2. `pendler_od_path` set → Kreis-level Pendler mode (current)
3. Neither → pure gravity (original)

All three return `(df_work, df_education, df_outside)`.

## Gemeinde-Level Mode (Approach A)

For each origin Gemeinde g_i, build weights from three sources:

1. **Internal** = IOP(g_i) from IOP file
2. **Top-10 cross-Gemeinde** = Verfl counts for within-study destinations
3. **Gravity fill** = remaining Auspendler not in top-10, not outside, distributed via employee × exp(slope × distance) across study-area Gemeinden (excluding top-10 destinations)

**Outside fraction**: computed from Verfl top-10 outside counts + proportional share of unaccounted tail. Uses Kreis-level Pendler outside fraction as fallback/cross-check.

**Normalization**: within-study weights normalized to sum to 1.0 after removing outside fraction. Outside fraction returned in `df_outside` for downstream population dropping.

## Files Changed

- `bavaria/gravity/pendler_data.py` — new `parse_gemeinde_od()` and `parse_gemeinde_iop()`
- `bavaria/gravity/model.py` — new `build_gemeinde_constrained_matrix()`, updated `configure()` and `execute()`
- Config files — add `gemeinde_od_path` and `gemeinde_iop_path`
- Tests — new test for Gemeinde-level mode

## Success Criteria

- Gemeinde-level Pearson r > 0.80 (up from 0.50)
- Internal flow correlation > 0.80 (up from 0.53)
- Kreis-level shares still match official exactly
- All existing tests still pass
- Pipeline runs successfully with 1% sample
