# Data licences

MIZAN's bundled datasets are generated fixtures, written by the code in this repository and covered by its Apache-2.0 licence. One dataset is real and comes from elsewhere.

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

## Typeface

Readex Pro, embedded in every page: SIL Open Font License 1.1, full text in `mizan/web/fonts/OFL-ReadexPro.txt`.
