# Next-day demand and station rebalancing

## Research question and scope

Does once-daily redistribution based on next-day pickup and return forecasts improve service in a fleet-conserving replay of September 2019 Citi Bike trips?

The full available network is preferable to a closed 15-station subset because trips crossing a subset boundary are important bike flows. The simulation uses stations observed before September, and excludes and reports September trips whose origin or destination is outside that universe. It is therefore a fixed, closed network approximation, not an exact representation of all September operations. The original milestone notebook remains a separate one-hour-ahead analysis.

## Changes needed to the proposed assumptions

- Unlimited docking capacity is consistent with finite inventory. Capacity matching to present-day stations is unnecessary for this experiment, and present-day capacity does not establish 2019 capacity.
- Trip history records completed rides, not all attempted requests. We treat each retained historical trip as a synthetic request. Simulated unmet requests measure shortages under this scenario, not actual historical unmet demand.
- A successful pickup schedules its own destination return at the recorded arrival time. A failed pickup schedules no return. This is essential for conservation. The original trip destination and duration are assumed unchanged by the simulation.
- Each policy starts identically on September 1 and then evolves independently. Inventory is never reset on subsequent days. Bikes still traveling at midnight remain in transit and cannot be rebalanced.
- Transfers occur instantaneously at 00:00 local New York time, before that day's pickups. This is the beginning-of-day interpretation of “morning.” Trucks have no persistent inventory because every modeled transfer completes immediately. A realistic 06:00 operation requires shifting the forecast and simulation day and modeling travel/handling time.
- Unlimited docking does not imply unlimited rebalancing. The default daily movement cap is 10% of the scenario fleet, adjustable with `--move-fraction`.

## Input preparation and auditing

Read all local monthly CSV parts in chunks. Parse timestamps, reject missing station/time records and nonpositive elapsed durations, and restrict departures to April–September. Keep long trips, including returns after the simulation horizon; those bikes remain in transit. Check global hashes of `(bikeid, starttime, stoptime, origin, destination)` and stop if duplicates appear rather than silently double-counting. A hash collision would also trigger inspection.

Count pickups at departure time and returns at arrival time. Fill missing station-hour counts with zero. This does not distinguish closure from zero demand. Station names and coordinates are taken from their first available pre-September records; historical relocation is not reconstructed. Coordinates describe straight-line movement costs only.

The source manifest records file paths, sizes, and modification timestamps. Reuse of prepared arrays verifies this manifest; it is not a cryptographic content checksum. Preparation audit and software versions accompany the results.

## Forecast design

Forecast the coming calendar day's total pickups and returns for every station at midnight, using records strictly before that midnight. Three interpretable models are compared:

1. **lag7:** the corresponding totals seven days earlier.
2. **calendar_mean:** the mean of all earlier matching weekdays.
3. **recent_dow:** the mean of matching weekdays in the preceding 28 days.

August is rolling validation. Select one model by mean station-day MAE across pickups and returns; also report RMSE and WAPE. August scoring covers stations observed before August. Model choice is fixed before September. September forecasts update their historical inputs daily, with the same model rule; this is a rolling information-origin evaluation, not a single August 31 forecast of the entire month.

September has already been explored in the existing notebook and must not be claimed as an untouched final test. The output includes September diagnostics for all candidate models, but those values do not select the operational model. No weather was supplied, so the baseline uses calendar/history only. Future extensions should use weather forecasts available at the prediction origin, not retrospectively observed next-day weather.

**Information assumption:** both policy simulations receive the same historical count series for forecast updates, including requests that their simulated policy could not serve. This isolates the inventory/rebalancing effect, but assumes an exogenous request-information feed. If a deployed system observes only served rides, policy-specific censored learning requires a separate experiment.

## Initial inventory and fleet sensitivity

Trip histories alone do not identify the true initial fleet or station inventories because unobserved rebalancing and maintenance change station stocks. We initialize a transparent scenario from August only.

For each station and each August day, calculate the maximum positive hourly cumulative pickups minus returns, then add that day's largest hourly pickup count as a buffer against within-hour ordering. The station weight is the 90th percentile of that daily quantity. The reference fleet is the ceiling of the sum of station weights. This is an initialization heuristic, not a statistically identified inventory estimate or a guaranteed service quantile.

Run fleet multipliers 0.5, 1.0, and 1.5. Allocate each integer fleet across station weights using the largest-remainder method, so allocations sum exactly to the fleet. Both policies use the same allocation. Initial in-transit and truck inventories are zero; August trips crossing September 1 are deliberately not injected. This is a common cold start, not a reconstruction of the actual midnight state. No discarded pre-period return is subsequently created.

## Rebalancing target and optimization

For each station, distribute predicted pickups and returns over 24 hours according to earlier matching weekdays in the last 28 days. Take the largest positive cumulative predicted net outflow and add the 90th percentile of historical daily peak-hour pickups from that window. This buffer captures some intraday uncertainty but is not tuned or claimed optimal.

Allocate the currently docked fleet proportionally to these target weights using integer largest-remainder rounding. Fleet in transit is excluded. Let station supply be `max(inventory - target, 0)` and deficit be `max(target - inventory, 0)`. The transportation linear program chooses nonnegative transfers subject to donor stock, receiver deficit, and a daily fleet-based movement cap. It first fixes the maximum possible transfer count within those limits, then minimizes the sum of bikes transferred times haversine distance. The network-flow structure yields integer transfers; numerical integrality is checked explicitly.

This optimizes transport cost **for the heuristic target**, not expected lost demand directly. When the cap binds, the cost objective favors inexpensive transfers; it does not rank shortages by marginal service benefit. There is no economic tradeoff between service and moving cost yet. Outputs are bikes moved and bike-km, not truck vehicle-km or dollars. Truck capacity, routing, road networks, handling time, maintenance and depot operations are not modeled.

## Event replay and evaluation

Serve a pickup only when a bike is docked at its origin. Returns always succeed. At identical timestamps, process returns before pickups; tied pickups follow a deterministic sorted order. A priority queue holds only returns from successfully served trips. At each daily boundary, assert nonnegative station inventories and:

`sum(docked bikes) + bikes in transit = initial fleet`.

The instantaneous transfer operator separately asserts stock conservation and nonnegativity. Day 30 ends without artificially returning trips that finish later.

Report:

- Trip-level service: total successful pickups divided by total requests (not the unweighted average of daily percentages).
- Unmet pickup requests by day and station.
- Station availability: the exact fraction of simulated station-time with at least one docked bike. This is unweighted across the fixed network, including inactive stations; it is different from request-weighted service.
- Transfer counts and bike-km.
- Daily docked and in-transit inventories and constant fleet totals.
- Paired fleet sensitivity with identical requests, starts, and information under both policies.

Six automated tests cover integer allocations, forecast leakage, transport optimality/budgets, canceled returns after rejected pickups, midnight processing, long trips, exact empty-station time and fleet conservation. Full replay checks conservation on every policy/day/scenario. This is a deterministic replay; results do not establish uncertainty intervals or generalize automatically to other months.

## Reproduce

From the repository root, with the dependencies in `requirements.txt` installed:

```bash
python -m unittest discover -s tests -v
python src/bike_project.py
# Reuse the audited inputs for another experiment:
python src/bike_project.py --reuse-prepared --fleet-scales 0.5 1 1.5 --move-fraction 0.1
```

All new outputs are under `outputs/next_day/`. Re-running overwrites results for the selected scales; CSVs for other scales from earlier runs may remain, so use `run_config.json` and `simulation_summary.csv` to identify the current run. The large `prepared.npz` cache is ignored by Git.

## Next research steps

Compare learned forecasting models against these baselines with multiple earlier chronological folds. Test alternative stock buffers, initialization distributions, movement budgets and information assumptions. Add a simple non-predictive redistribution control to separate benefits of forecasting from benefits of moving bikes at all. Account for station closures/new openings and, if available, historical inventory snapshots. A later truck-routing experiment can replace the instantaneous transfer assumption. Keep an untouched later time period for a final evaluation when additional data become available.
