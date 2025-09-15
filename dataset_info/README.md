`dataset_info.json` contains the counts of resources from the dataset we [deterministically generate](../docker/synthea). To find them yourself, download the `fhir` folder inside the `medschool_synthea_out` Docker volume, place inside this folder, and run `analyze.py`


`previous_downloaded_data.json` contains counts of resources from the [dataset](https://github.com/synthetichealth/synthea-sample-data/blob/main/downloads/latest/synthea_sample_data_ccda_latest.zip) we used to download from Synthea.