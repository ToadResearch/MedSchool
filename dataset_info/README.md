`dataset_info.json` contains the counts of resources from the dataset we [deterministically generate](../docker/synthea). To find them yourself, add the `--save-synthea` flag while generating data on startup to preserve the synthea volume

```bash
./startup.sh --synthea --save-synthea
```

Then, download the `fhir` folder inside the `medschool_synthea_out` Docker volume, place inside this folder, and run

```bash
python analyze.py
```


`previous_downloaded_data.json` contains counts of resources from the dataset we used to [download](https://github.com/synthetichealth/synthea-sample-data/blob/main/downloads/latest/synthea_sample_data_ccda_latest.zip) from Synthea.
