# How to Download the Olist Dataset

## Option A — Kaggle API

1. Create a [Kaggle](https://www.kaggle.com) account if you don't have one yet.
2. Install the dependencies (the Kaggle CLI is a development dependency):

   ```bash
   poetry install
   ```

3. Authenticate the Kaggle CLI with **one** of the options below. A `.env` file is **not** loaded: neither the project, Poetry nor the Kaggle CLI reads it.

   **Browser login** (recommended by Kaggle, no token to manage):

   ```bash
   poetry run kaggle auth login
   ```

   **API token in an environment variable** (valid only for the current terminal session). Generate the token at [kaggle.com/settings/api](https://www.kaggle.com/settings/api) ("Generate New Token"), then:

   ```powershell
   $env:KAGGLE_API_TOKEN = "YOUR_TOKEN"   # PowerShell
   ```

   ```bash
   export KAGGLE_API_TOKEN="YOUR_TOKEN"   # bash / Git Bash
   ```

   **API token in a file** (persistent): save the token as the only content of `~/.kaggle/access_token` (on Windows, `C:\Users\<you>\.kaggle\access_token`).

4. Download the dataset:

   ```bash
   poetry run kaggle datasets download -d olistbr/brazilian-ecommerce -p data/raw --unzip
   ```

To **restore** the original CSVs after an incident test, repeat the download command appending `-o` to overwrite existing files.

## Option B — Manual download

1. Go to https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce
2. Click "Download", extract the `.zip`, and copy the `.csv` files to `data/raw/`.

# Verification

There should be 9 files in `data/raw/`:

```
olist_customers_dataset.csv
olist_geolocation_dataset.csv
olist_order_items_dataset.csv
olist_order_payments_dataset.csv
olist_order_reviews_dataset.csv
olist_orders_dataset.csv
olist_products_dataset.csv
olist_sellers_dataset.csv
product_category_name_translation.csv
```
