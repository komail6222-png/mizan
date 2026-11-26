# Data licences

MIZAN's bundled datasets are generated fixtures, written by the code in this repository and covered by its Apache-2.0 licence. Two datasets are real and come from elsewhere.

## VisA (BASIRA on real images)

VisA (Zou et al., 2022, "SPot-the-Difference Self-Supervised Pre-training for Anomaly Detection and Segmentation", arXiv:2207.14315), CC BY 4.0, https://creativecommons.org/licenses/by/4.0/. Images were converted to 96×96 greyscale or resized for the model; no image is redistributed except the four 96 px examples shown.

- **Source:**
  - Archive: `https://amazon-visual-anomaly.s3.us-west-2.amazonaws.com/VisA_20220922.tar` (1,929,840,640 bytes).
  - Official one-class split: `split_csv/1cls.csv` from `github.com/amazon-science/spot-diff` at commit `2a692ab575001cbde74d402d897a7286086c6199`.
- **Licence:** Creative Commons Attribution 4.0 International. Commercial use is allowed, with attribution. The authors ask that work using the dataset cite the paper above.
- **What this repository holds:** the dataset itself is not in this repository.
  - `reports/visa/manifest.json` records every image's path and SHA-256, the archive's SHA-256 and the split's. `scripts/fetch_visa.py` downloads the data into the git-ignored `var/visa/`.
  - The results pages show four images, each converted to 96×96 greyscale, with this attribution beside them.
- **Changes made:** for BASIRA's engine path, each image is converted to greyscale and resized to 96×96. For DINOv2 at native input, each image is resized to 224×224. Nothing else is altered.
- **Status of the results:** the results on this data describe these public images only. They are not MIZAN's own data and certify no production line.

MVTec AD was considered and not used: its licence (CC BY-NC-SA 4.0) does not allow commercial use.

## Open Power System Data household package (RASID on real load)

Open Power System Data. 2020. Data Package Household Data. Version 2020-04-15. https://data.open-power-system-data.org/household_data/2020-04-15/. Primary data from the CoSSMic project (Konstanz, Germany). CC BY 4.0, https://creativecommons.org/licenses/by/4.0/.

- **Source:** the 60-minute file `household_data_60min_singleindex.csv` (15,654,559 bytes, SHA-256 `15956440b27465686eae2abf5895cbc3dab80f934a891bcb2f4f944428e97d60`).
- **Licence:** Creative Commons Attribution 4.0 International. Commercial use is allowed, with attribution.
- **What this repository holds:** the file is not in this repository. `scripts/fetch_opsd.py` downloads it into the git-ignored `var/opsd/` and keeps it only when its size and SHA-256 match the values above. `reports/opsd/rasid.json` holds per-day results derived from two of its sites, `industrial1` (a warehouse) and `industrial3` (a research-institute building).
- **Changes made:**
  - Hourly energies are differenced from the cumulative meter readings.
  - Hours the package marks as interpolated, days that copy the day before and straight-line fills are excluded. Days that repeat the day one or two weeks earlier are not excluded by the sealed rule; they are counted, and checked separately, in `reports/opsd/copies.json`.
  - Each site's load is reconstructed as grid import plus solar. The no-export check found no sunny clean hour with near-zero import.
- **Status of the results:** they describe these two sites in 2015–2017 only, in RASID's illustrative tariff units. They are not MIZAN's own data, and no bill or currency follows from them.

## Typeface

Readex Pro, embedded in every page: SIL Open Font License 1.1, full text in `mizan/web/fonts/OFL-ReadexPro.txt`.
