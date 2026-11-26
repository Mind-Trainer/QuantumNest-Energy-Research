# Data and provenance

| Source | Category | Use in QuantumNest |
|---|---|---|
| [Seeded scenario generator](scenarios.py) | Synthetic tariffs, background load, PV, appliance ratings, deadlines and preferences | All controlled quantum/classical experiments. The high-EV dispatch scenario uses a higher energy target, and the uncertainty scenario spreads the PV cases. Weather and season labels describe profile changes |
| [Hebrail and Berard, Individual Household Electric Power Consumption](https://doi.org/10.24432/C58K54) | Measured household active power and minute submetering, CC BY 4.0 | Separate measured-baseline dispatch replay. PV, tariffs, safety limits and scheduled service inputs remain synthetic |
| [Kelly and Knottenbelt, UK-DALE](https://doi.org/10.1038/sdata.2015.7) | Measured UK appliance and whole-house demand, five homes | Reviewed for appliance profiling. Offers disaggregated traces and does not supply coincident rooftop PV for this exercise |
| [Ratnam, Weller, Kellett and Murray, Ausgrid Solar Home](https://doi.org/10.1080/14786451.2015.1100196) | Measured Australian load and rooftop PV for 300 de-identified households | Reviewed for joint load/PV validation. The [IEEE dataset catalog](https://ieee-pes-data-sharing.org/datasets/detail/5795a399-f06e-4747-b518-cb9d961616a2) describes half-hour data and link provenance. Consult the provider's current research and redistribution terms before acquiring a copy |
| [Pecan Street Dataport](https://www.pecanstreet.org/access/) | Measured U.S. household and circuit demand, with site-dependent PV/EV channels | Reviewed. Access requires selecting a provider access plan. The study used no paid data |
| [NREL/NLR End-Use Load Profiles](https://registry.opendata.aws/nrel-pds-building-stock/) | Modeled ResStock/ComStock 15-minute profiles calibrated and validated against available observations | Reviewed for building and climate coverage. Treat releases and upgrades as modeled data |

## Measured replay

The derived profile is [uci_hourly_residual.json](uci_hourly_residual.json), adapted from Georges Hebrail and Alice Berard, *Individual Household Electric Power Consumption*, UCI Machine Learning Repository, [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). It uses exactly 1,440 readings from 17 December 2006, the first full date in the source archive. One-minute residual is whole-home active energy minus the three submeter readings. Totals are aggregated hourly and rounded to 0.1 kW. There were zero clipped negative readings and no imputation. The residual may still contain fixture loads whose individual identity is unknown, so the replay does not claim exact end-use separation. The full original archive is available from the provider and is excluded from this repository.

```bash
# Download the archive from the UCI dataset page. Then verify, aggregate, and write the profile.
python -m datasets.uci_baseline path/to/individual-household-electric-power-consumption.zip
python -m experiments.demonstrations
```

Timestamp, exact source ZIP checksum, license, conversion unit and each transformed value are stored in the derived profile. The benchmark manifest and raw instances identify the synthetic seed and both solar scenarios. Synthetic tariffs use configurable nominal currency units and do not represent any utility's tariff.
