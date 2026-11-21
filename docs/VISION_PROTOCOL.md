# Frozen synthetic inspection comparison

The first full evaluation exposed low sensitivity on development items 2000–2079. Version 2 adds local dark and bright residual features using fixed 5- and 9-pixel grayscale closing/opening windows to distinguish narrow marks from smooth illumination changes. The original run is preserved in reports/evaluation/basira_v1_development.json. Training and calibration groups are unchanged. Calibrate the new descriptor independently at alpha 0.05.

Evaluate once on new item seeds 9100 through 9179: the first 40 normal, the next 40 split equally between pit and crack. Compute the pixel baseline on those same new items. Report misses and false alarms, and keep auto-PASS disabled. Further runs on these items are regression runs. Field qualification needs independent real parts from the intended capture station.
