# How to Download the Olist Dataset

## Option A — Kaggle API

1. Create a [Kaggle](https://www.kaggle.com) account if you don't have one yet.
2. Generate an API token: Account -> Settings -> API Tokens -> "Generate New Token".
3. Add the token to your `.env` environment variable as the value for the `KAGGLE_API_TOKEN` key (KAGGLE_API_TOKEN = "YOUR_KEY").
4. Install the package (development dependency) and download the dataset:

```bash
poetry install
poetry run kaggle datasets download -d olistbr/brazilian-ecommerce -p data/raw --unzip
```

To **restore** the original CSVs after an incident test, repeat the second command appending `-o` to overwrite existing files.

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