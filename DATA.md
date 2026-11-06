# Data provenance and setup

**Source:** Citi Bike system data and trip-history archive, https://citibikenyc.com/system-data .

**Study period:** April 1–September 30, 2019 (New York City Citi Bike trip history, not Jersey City). The current local analysis uses **16 CSV parts** containing **12,786,215 trips**. Because published archive packaging can change, check the local `data_manifest.txt` for the exact part filenames used for this run.

Download/extract the relevant 2019 trip-history files and organize them like this **relative to the notebook**:

```
2019-citibike-tripdata/
  4_April/       *.csv
  5_May/         *.csv
  6_June/        *.csv
  7_July/        *.csv
  8_August/      *.csv
  9_September/   *.csv
```

The notebook reads all `*.csv` in each monthly folder. If the archive downloads as ZIP files, extract them into the corresponding folders. Do not put unrelated CSV files there. Check that **16 CSV files** are found in total and the merged record count is **12,786,215**; otherwise compare with the recorded manifest and obtain the matching archive snapshot.

To create a list of the precise local input filenames for GitHub:

```bash
find 2019-citibike-tripdata -type f -name '*.csv' \
  | sed 's#^2019-citibike-tripdata/##' \
  | sort > data_manifest.txt
```

The actual trip CSV files are intentionally excluded from version control because they are large. Observed trips are **not** equivalent to latent demand: historical bike availability, dock availability and rebalancing operations are not directly observed. Weather variables are not included in this milestone.
