# Next-day demand and rebalancing: executed pilot

Network: 841 stations known before September; 2,431,500 retained September requests.
Excluded 13,400 trips involving stations not observed before September.
Selected model: **recent_dow**, using mean pickup/return MAE on August rolling validation.
Reference fleet: **29,983 bikes**, a training-derived scenario, not an estimate of the historical fleet.

| Fleet scale | Policy | Service level | Unmet requests | Bikes moved | Bike-km |
|---|---|---:|---:|---:|---:|
| 0.5 | no_rebalancing | 92.74% | 176,572 | 0 | 0.0 |
| 0.5 | forecast_rebalancing | 95.26% | 115,226 | 44,970 | 24,942.7 |
| 1 | no_rebalancing | 96.95% | 74,195 | 0 | 0.0 |
| 1 | forecast_rebalancing | 99.73% | 6,457 | 89,940 | 118,889.4 |
| 1.5 | no_rebalancing | 98.31% | 40,984 | 0 | 0.0 |
| 1.5 | forecast_rebalancing | 99.99% | 327 | 121,799 | 161,581.5 |

All daily fleet conservation assertions passed. Station inventories carry forward independently in each policy.

Interpretation: this is replay of historically served trips as synthetic requests, not recovery of latent demand. Historical counts update forecasts daily in both policies, representing a shared exogenous demand-information feed. This is an optimistic information assumption if deployment observes only its own successful rides. Transfers are instantaneous at midnight, capped daily, and minimized by straight-line bike-km; these are not truck routes, road distances, or monetary costs. September was previously explored in the notebook. See METHODOLOGY.md for assumptions and limits.
